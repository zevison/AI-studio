"""Unified, read-only history/cost reporting across existing local tables.

No network requests, re-submission, or provider billing assumptions.
Historical records can only be shown when already present in this installation's DB.
"""
from __future__ import annotations
from collections import Counter
from contextlib import closing
import json
import re
import sqlite3
import time
from . import config, db

SOURCE_TABLES = {
    'native': 'tasks',
    'gpt': 'ai_calls',
    'product': 'product_analysis_runs',
    'script': 'radar_scripts',
    'brightdata': 'bt_jobs',
}
LABELS = {
    'video':'视频生成', 'image':'图片生成', 'gpt':'GPT 对话',
    'product':'商品理解', 'script':'GPT 创意脚本', 'brightdata':'TikTok 手动采集',
}


def _tables(c):
    return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def _rows(c, table, sql, limit):
    if table not in _tables(c):
        return []
    try:
        return [dict(r) for r in c.execute(sql, (limit,)).fetchall()]
    except sqlite3.OperationalError:
        return []


def _stats(c, table, cost_col=None, real_col=None):
    if table not in _tables(c):return {'rows':0,'estimated_usd':None,'actual_usd':None,'estimated_known':0,'actual_known':0}
    try:
        names={x[1] for x in c.execute(f'PRAGMA table_info({table})')}
        parts=['COUNT(*) n']
        for col,alias in ((cost_col,'estimated'),(real_col,'actual')):
            if col and col in names:
                parts.extend((f'COUNT({col}) {alias}_known',f'SUM({col}) {alias}_sum'))
        data=dict(c.execute(f"SELECT {', '.join(parts)} FROM {table}").fetchone())
        return {'rows':int(data['n'] or 0),
                'estimated_usd':float(data['estimated_sum']) if data.get('estimated_sum') is not None else None,
                'actual_usd':float(data['actual_sum']) if data.get('actual_sum') is not None else None,
                'estimated_known':int(data.get('estimated_known') or 0),
                'actual_known':int(data.get('actual_known') or 0)}
    except sqlite3.OperationalError:
        return {'rows':0,'estimated_usd':None,'actual_usd':None,'estimated_known':0,'actual_known':0}


def _as_json(s):
    try:return json.loads(s or '{}') if isinstance(s,str) else (s or {})
    except (TypeError,ValueError):return {}


def _safe_error(value):
    text=str(value or '')[:600]
    text=re.sub(r'(?i)(bearer\s+)[^\s,;]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)(\b(?:api[_ -]?key|access[_ -]?token|secret|password)\b\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',text)
    return text[:180]


def _as_float(x):
    try:return float(x) if x is not None else None
    except (TypeError,ValueError):return None


def record_gpt_usage(call_id, usage):
    """Optional post-completion telemetry. Failing here must not fail GPT replies.
    Token counts are not charges; no implied dollar conversion.
    """
    if not call_id:return False
    info=usage if isinstance(usage,dict) else {}
    def token(*keys):
        for key in keys:
            if isinstance(info.get(key),int) and info[key]>=0:return info[key]
        return None
    p=token('prompt_tokens','input_tokens')
    q=token('completion_tokens','output_tokens')
    total=token('total_tokens')
    with db.tx() as c:
        c.execute('''CREATE TABLE IF NOT EXISTS gpt_token_usage_v423(
            call_id TEXT PRIMARY KEY, prompt_tokens INTEGER, completion_tokens INTEGER,
            total_tokens INTEGER, created_at REAL NOT NULL)''')
        c.execute('''INSERT OR IGNORE INTO gpt_token_usage_v423
                     (call_id,prompt_tokens,completion_tokens,total_tokens,created_at)
                     VALUES(?,?,?,?,?)''',(str(call_id),p,q,total,time.time()))
    return True


def overview(limit=140):
    """No writes, no provider calls. Preserves real uncertainty on billing."""
    limit=max(10,min(300,int(limit)))
    entries=[]
    with closing(db.connect()) as c:
        tables=_tables(c)
        source_stats={
            'video_image':_stats(c,'tasks'),
            'gpt':_stats(c,'ai_calls'),
            'product':_stats(c,'product_analysis_runs','estimated_usd'),
            'script':_stats(c,'radar_scripts','estimated_cost_usd','actual_cost_usd'),
            'brightdata':_stats(c,'bt_jobs'),
            'usage':_stats(c,'usage_events','estimated_cost_usd','actual_cost_usd'),
        }
        # Read all known usage events, grouped by job, with no guessed provider cost.
        usage_by_task={}
        if 'usage_events' in tables:
            for r in c.execute('''SELECT task_id, SUM(estimated_cost_usd) estimated,
                       SUM(actual_cost_usd) actual, COUNT(*) events
                       FROM usage_events GROUP BY task_id'''):
                usage_by_task[r['task_id']]=dict(r)
        tokens={}
        if 'gpt_token_usage_v423' in tables:
            tokens={r['call_id']:dict(r) for r in c.execute('SELECT * FROM gpt_token_usage_v423')}
        for r in _rows(c,'tasks',"SELECT id,kind,status,provider_task_id,request_json,error,created_at FROM tasks ORDER BY created_at DESC LIMIT ?",limit):
            req=_as_json(r['request_json']); u=usage_by_task.get(r['id'],{})
            model=str(req.get('_request_model') or req.get('model') or '')[:110]
            kind=r['kind'] if r['kind'] in ('video','image') else 'video_image'
            entries.append({'id':r['id'],'source':'tasks','kind':kind,'label':LABELS.get(kind,'生成任务'),
                'status':r['status'],'model':model,'provider_id':r['provider_task_id'],
                'created_at':r['created_at'],'estimated_cost_usd':_as_float(u.get('estimated')),
                'actual_cost_usd':_as_float(u.get('actual')),'error':_safe_error(r['error'])})
        for r in _rows(c,'ai_calls','SELECT id,model,status,error,created_at FROM ai_calls ORDER BY created_at DESC LIMIT ?',limit):
            u=tokens.get(r['id'],{})
            entries.append({'id':r['id'],'source':'ai_calls','kind':'gpt','label':LABELS['gpt'],'status':r['status'],
                'model':r['model'],'created_at':r['created_at'],'estimated_cost_usd':None,'actual_cost_usd':None,
                'tokens':u.get('total_tokens'),'error':_safe_error(r['error'])})
        for r in _rows(c,'product_analysis_runs','SELECT id,model,status,estimated_usd,error,created_at FROM product_analysis_runs ORDER BY created_at DESC LIMIT ?',limit):
            entries.append({'id':r['id'],'source':'product_analysis_runs','kind':'product','label':LABELS['product'],'status':r['status'],
                'model':r['model'],'created_at':r['created_at'],'estimated_cost_usd':_as_float(r['estimated_usd']),
                'actual_cost_usd':None,'error':_safe_error(r['error'])})
        for r in _rows(c,'radar_scripts','SELECT id,model,status,estimated_cost_usd,actual_cost_usd,error,created_at FROM radar_scripts ORDER BY created_at DESC LIMIT ?',limit):
            entries.append({'id':r['id'],'source':'radar_scripts','kind':'script','label':LABELS['script'],'status':r['status'],
                'model':r['model'],'created_at':r['created_at'],'estimated_cost_usd':_as_float(r['estimated_cost_usd']),
                'actual_cost_usd':_as_float(r['actual_cost_usd']),'error':_safe_error(r['error'])})
        for r in _rows(c,'bt_jobs','SELECT id,status,snapshot_id,quantity,actual_records,error,created_at FROM bt_jobs ORDER BY created_at DESC LIMIT ?',limit):
            entries.append({'id':r['id'],'source':'bt_jobs','kind':'brightdata','label':LABELS['brightdata'],'status':r['status'],
                'model':'Bright Data','provider_id':r['snapshot_id'],'quantity':r['quantity'],
                'records':r['actual_records'],'created_at':r['created_at'],
                'estimated_cost_usd':None,'actual_cost_usd':None,'error':_safe_error(r['error'])})
        entries.sort(key=lambda r:float(r.get('created_at') or 0),reverse=True)
        entries=entries[:limit]
        counter=Counter(e['kind'] for e in entries)
        # These sums cover the respective WHOLE tables, not merely the latest page.
        money_sources=(source_stats['usage'],source_stats['product'],source_stats['script'])
        est=sum(s['estimated_usd'] for s in money_sources if s['estimated_usd'] is not None)
        actual=sum(s['actual_usd'] for s in money_sources if s['actual_usd'] is not None)
        est_known=sum(s['estimated_known'] for s in money_sources)
        actual_known=sum(s['actual_known'] for s in money_sources)
        all_records=sum(source_stats[k]['rows'] for k in ('video_image','gpt','product','script','brightdata'))
        return {'entries':entries, 'categories':dict(counter), 'sources':source_stats,
                'summary':{'total_records':all_records,'estimated_usd':round(est,6) if est_known else None,
                    'actual_usd':round(actual,6) if actual_known else None,
                    'estimated_known':est_known,'actual_known':actual_known,
                    'gpt_tokens_recorded':sum(1 for u in tokens.values() if u.get('total_tokens') is not None)},
                'diagnostics':{'database_file':'data/studio.db','database_exists':config.DB_PATH.is_file(),
                    'database_bytes':config.DB_PATH.stat().st_size if config.DB_PATH.is_file() else 0,
                    'is_empty':all_records==0,'recovery_note':'恢复中心只显示需要继续查询/下载的原视频任务，空列表不等于历史数据丢失。'},
                'notes':['所有记录来自当前本地数据库，不补造未记录的历史。',
                         '预估金额不等于实际扣费；未知价格不是免费。',
                         '实际扣费只统计本地明确存储的账单金额，未完整覆盖 OpenLux/Bright Data 结算。',
                         '历史 GPT token 数量若未持久保存无法追溯；升级后才开始记录可用用量。']}
