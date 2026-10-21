"""Unified, read-only-safe task evidence and recovery diagnostics.

No expensive provider calls or paid retries occur here.  Unknown results are never
reinterpreted as definite failures. This module can run when offline on Windows.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import sqlite3
import threading
import time
from . import db

INIT_LOCK=threading.RLock()
_initialized=False
STALE_AFTER={'product':120,'script':105,'image':150,'video':240}
RECONCILE='submission_unknown'


def ensure_schema():
    global _initialized
    if _initialized:return
    with INIT_LOCK:
        if _initialized:return
        with db.tx() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS task_observation_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT NOT NULL,task_id TEXT NOT NULL,
                phase TEXT NOT NULL,status TEXT NOT NULL,detail TEXT NOT NULL DEFAULT '',
                recorded_at REAL NOT NULL)''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_observation_task ON task_observation_events(kind,task_id,id DESC)')
        _initialized=True


def _clean(v):
    """Protect diagnostic output from authentication data and arbitrary remote body dumps."""
    s=str(v or '')[:500]
    s=re.sub(r'(?i)(Bearer\s+)[A-Za-z0-9._~+/-]+',r'\1[REDACTED]',s)
    s=re.sub(r'(?i)(sk-[A-Za-z0-9_\-]{6,})','[REDACTED_KEY]',s)
    s=re.sub(r'(?i)((?:key|token|secret|password)\s*[:=]\s*)[^\s,;&]+',r'\1[REDACTED]',s)
    return s[:360]


def record(kind,task_id,phase,status,detail=''):
    if not task_id:return
    ensure_schema()
    with db.tx() as c:
        c.execute('INSERT INTO task_observation_events(kind,task_id,phase,status,detail,recorded_at) VALUES(?,?,?,?,?,?)',
                  (str(kind),str(task_id),str(phase),str(status),_clean(detail),time.time()))


def _recent_event(kind,tid):
    try:return db.one('SELECT phase,status,detail,recorded_at FROM task_observation_events WHERE kind=? AND task_id=? ORDER BY id DESC LIMIT 1',(kind,tid))
    except sqlite3.OperationalError:return None


def _kind_rows(limit):
    rows=[]
    def read(sql,kind):
        try:
            for r in db.all_(sql,(limit,)):
                r=dict(r);r['kind']=kind;rows.append(r)
        except sqlite3.OperationalError:pass
    read('SELECT id,status,model,estimated_usd AS estimated_cost,created_at,updated_at,error FROM product_analysis_runs ORDER BY created_at DESC LIMIT ?', 'product')
    read('SELECT id,status,model,estimated_cost_usd AS estimated_cost,created_at,updated_at,error FROM radar_scripts ORDER BY created_at DESC LIMIT ?','script')
    read('SELECT id,kind AS native_kind,status,created_at,updated_at,error,provider_task_id,last_polled_at,last_query_error,request_json,result_json,query_count FROM tasks ORDER BY created_at DESC LIMIT ?','native')
    return rows


def _item(raw,now):
    raw=dict(raw);kind=raw['kind']
    if kind=='native':
        kind='video' if raw.get('native_kind')=='video' else str(raw.get('native_kind') or 'image')
    status=raw.get('status') or 'unknown'
    event=_recent_event(kind,raw['id'])
    last=float(raw.get('updated_at') or raw.get('created_at') or now)
    started=float(raw.get('created_at') or now)
    staleness=int(max(0,now-last))
    provider_id=raw.get('provider_task_id') or None
    if status=='submission_unknown':
        phase='提交结果未知，待核对账单或平台任务'
        action='拥有供应商任务号可查询原任务；否则人工核对 OpenLux 账单，禁止再次提交相同内容'
    elif status in ('reconciled_no_charge','reconciled_charged'):
        phase='人工对账完成：未发现扣费，可另行授权重试' if status=='reconciled_no_charge' else '人工对账完成：确认扣费，原结果尚未恢复'
        action='原记录永久保留，若需重新分析请重新预检费用并单次授权；不会自动重试'
    elif status=='completed':
        phase='已返回结果；核对本地下载' if kind=='video' else '已完成并保存'
        action='查看结果与下载状态'
    elif status in ('failed','rejected'):
        phase='已失败或接口明确拒绝';action='查看原始错误，再决定是否重新授权'
    elif status=='needs_review':
        phase='待人工审核美国市场及产品事实';action='人工确认后继续'
    elif status in ('running','pending','result_pending'):
        phase=event['phase'] if event else ('等待视频平台任务返回' if provider_id else '等待外部接口返回')
        action='有平台任务编号可查询原任务；否则不自动重复提交'
    else:phase=event['phase'] if event else '等待人工处理';action='查看任务状态'
    stalled=status in ('running','pending','result_pending') and staleness>=STALE_AFTER.get(kind,180)
    if stalled:action='超过该环节提示阈值，检查原任务及网络连接；不自动付费重试'
    if kind=='video':
        req=db.loads(raw.get('request_json'),{}) or {}
        model=req.get('_request_model') or req.get('model') or ''
        result=db.loads(raw.get('result_json'),{}) or {}
        if result.get('download_error'):
            phase='远端成片已返回，本地下载失败';action='仅重试下载，严禁重新生成'
    else:model=raw.get('model') or ''
    return {'id':raw['id'],'kind':kind,'status':status,'phase':phase,
         'last_event':event['detail'] if event else '',
         'model':model,'provider_task_id':provider_id,
         'elapsed_seconds':int(max(0,now-started)),
         'since_update_seconds':staleness,'stalled':bool(stalled),
         'last_update_at':last,'created_at':started,
         'last_provider_response_at':raw.get('last_polled_at'),
         'last_query_error':_clean(raw.get('last_query_error','')),
         'error':_clean(raw.get('error','')),
         'estimated_cost_usd':raw.get('estimated_cost'),
         'charge_state':'实际扣费待账单核对' if status=='submission_unknown' else '实际扣费以账单为准',
         'suggested_action':action,'safe_to_auto_resubmit':False}


def overview(limit=45):
    ensure_schema();now=time.time()
    limit=max(1,min(100,int(limit)))
    data=sorted((_item(x,now) for x in _kind_rows(limit)),key=lambda x:x['created_at'],reverse=True)[:limit]
    return {'generated_at':now,'tasks':data,
            'attention_count':sum(x['stalled'] or x['status'] in ('submission_unknown','needs_review') or bool(x['error']) for x in data),
            'running_count':sum(x['status'] in ('pending','running','result_pending') for x in data),
            'note':'数据来自本地持久化记录；没有获得提供商进度时不伪造百分比。查询不提交付费请求。'}


def diagnostics(limit=60):
    view=overview(limit)
    # Whitelist fields only; never expose entire saved request, original source text, API Key or remote bodies.
    return {'diagnostic_version':'3.9.8.12','created_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
        'summary':{'running':view['running_count'],'attention':view['attention_count']},
        'tasks':view['tasks'],
        'safety':'此报告不包含 API Key、原始请求体、商品文档、参考视频文件或完整供应商响应。'}


def recover_interrupted_on_start():
    """Mark unfinished synchronous requests ambiguous after a process restart.

    No inference about actual provider billing can be made.  Video tasks WITH a
    provider task id remain running and are polled by the existing watcher.
    """
    ensure_schema();changed=[]
    with db.tx() as c:
        for table,kind in (('product_analysis_runs','product'),('radar_scripts','script')):
            try: rows=c.execute(f"SELECT id FROM {table} WHERE status='running'").fetchall()
            except sqlite3.OperationalError:continue
            for row in rows:
                tid=row['id'];c.execute(f"UPDATE {table} SET status='submission_unknown',error=?,updated_at=? WHERE id=?",(
                    '服务重启时发现未完成的同步请求，无法确认远端是否已扣费；禁止自动重新提交。',time.time(),tid));changed.append((kind,tid))
        rows=c.execute("SELECT id,kind FROM tasks WHERE status IN ('pending','running') AND provider_task_id IS NULL").fetchall()
        for r in rows:
            # Existing image tasks may already have reached upstream; don't re-submit.
            c.execute("UPDATE tasks SET status='submission_unknown',error=?,updated_at=? WHERE id=?",(
                '服务中断且无平台任务编号，远端处理结果未知；请对账，禁止自动重新提交。',time.time(),r['id']))
            changed.append((r['kind'],r['id']))
    for kind,tid in changed:record(kind,tid,'启动恢复：远端结果待核对','submission_unknown')
    return {'reconciled':len(changed),'no_paid_calls':True}


def reconcile_product(payload):
    """Human accounting acknowledgement; never infers provider success or repeats a call.

    Billing must be checked by the user on OpenLux; model response is unrecoverable
    without provider request ID. This only removes the local duplicate gate.
    """
    tid=str(payload.get('task_id') or '')
    answer=str(payload.get('billing_result') or '')
    if answer not in ('no_charge','charged'):
        raise ValueError('必须选择已核账：未发现扣费，或已发生扣费。')
    if payload.get('confirmed') is not True or str(payload.get('confirmation') or '').strip()!='已核对OpenLux账单':
        raise ValueError('请先确认已经核对 OpenLux 账单；不能自动判断付费状态。')
    if not tid.startswith('pa_'):raise ValueError('不是商品理解分析任务')
    new_state='reconciled_no_charge' if answer=='no_charge' else 'reconciled_charged'
    ensure_schema()
    now=time.time()
    with db.tx() as c:
        row=c.execute('SELECT status FROM product_analysis_runs WHERE id=?',(tid,)).fetchone()
        if not row:raise ValueError('任务不存在')
        if row['status']!='submission_unknown':raise ValueError('只能人工核对结果未知的商品 GPT 任务')
        c.execute('UPDATE product_analysis_runs SET status=?,updated_at=?,error=? WHERE id=? AND status=?',
                (new_state,now,'人工账单核对完成；未恢复远端原始结果。重新分析需重新预检并再次明确付费授权。',tid,'submission_unknown'))
    record('product',tid,'用户对账确认并解除原任务的重复提交限制',new_state,
           '账单状态：'+('未见扣费' if answer=='no_charge' else '用户确认已经扣费')+'；本操作无模型调用')
    return {'task_id':tid,'status':new_state,'no_paid_calls':True,
            'notice':'原任务未恢复。若需要重新生成，请在商品知识中心重新预检并明确单次付费授权。'}
