"""TikTok US-only evidence radar: evidence-first intake, review and opt-in GPT scripts.
No unrelated YouTube/news substitute. Licensed TikTok provider is an extension point;
without an approved provider only explicit TikTok link/batch imports are ingested.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import inspect
import hashlib
import math
import difflib
from collections import Counter
from contextlib import closing
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
from pathlib import Path
import sqlite3

from . import db, config, secrets_store, model_pricing
from . import us_marketing

_BJ = dt.timezone(dt.timedelta(hours=8), name='Asia/Shanghai')
_lock = threading.RLock()
MAX_PER_PRODUCT = 24
MAX_SEARCH_TERMS = 8
WINDOW_DAYS_PRIMARY = 7
WINDOW_DAYS_EXTENDED = 14
MIN_QUALITY_EVIDENCE = 2  # availability signal only; never a compulsory output quota
SOURCE_COOLDOWN_SECONDS = 6 * 3600
MAX_YOUTUBE_SEARCHES_PER_SCAN = 4
MAX_YOUTUBE_RECENT_SEARCHES = 3   # Search.list typically costs 100 quota units; strict local cap

MAX_SOURCES_FOR_GPT = 8
DEFAULT_MODEL = 'gpt-6.1-sol'
PAID_SCRIPT_PATH = '/v1/chat/completions'


def _now(): return time.time()
def _uid(prefix): return f'{prefix}_{uuid.uuid4().hex[:16]}'
def _json(value): return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
def _decode(value, fallback=None):
    try: return json.loads(value)
    except Exception: return fallback

def _clean(v, limit=1200): return re.sub(r'\s+', ' ', str(v or '')).strip()[:limit]
def _keys(v):
    if isinstance(v, str): v = re.split(r'[,，;；\n]+', v)
    return list(dict.fromkeys(_clean(x, 60) for x in (v or []) if _clean(x, 60)))[:16]

_schema_initialized = False

def ensure_schema():
    global _schema_initialized
    if _schema_initialized: return
    with _lock:
        if _schema_initialized: return
        # First-time additive migration: preserve existing user assets, history and settings.
        try:
            present=db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='radar_products'")
            if not present and config.DB_PATH.exists() and config.DB_PATH.stat().st_size > 0:
                folder=config.DB_PATH.parent/'backups';folder.mkdir(parents=True,exist_ok=True)
                dst=folder/('studio_pre_hotrader_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.db')
                with closing(sqlite3.connect(config.DB_PATH)) as source,closing(sqlite3.connect(dst)) as target:source.backup(target)
        except Exception:
            # Backups are mandatory before modifying an existing on-disk database.
            raise RuntimeError('创建热点雷达数据库备份失败，已停止本次迁移')
        # Upgrade backups are essential even when radar_products already exists.
        old_table=db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='radar_products'")
        if old_table:
            with db.connect() as reader:
                old_cols={r[1] for r in reader.execute('PRAGMA table_info(radar_products)')}
            if 'is_draft' not in old_cols and config.DB_PATH.exists():
                bk=config.DB_PATH.parent/'backups';bk.mkdir(parents=True,exist_ok=True)
                dest=bk/('studio_pre_products_3982_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.db')
                with closing(sqlite3.connect(config.DB_PATH)) as src, closing(sqlite3.connect(dest)) as dst:src.backup(dst)
        if old_table and not db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='radar_source_health'") and config.DB_PATH.exists():
            backup=config.DB_PATH.parent/'backups';backup.mkdir(parents=True,exist_ok=True)
            destination=backup/('studio_pre_quality_3987_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.db')
            with closing(sqlite3.connect(config.DB_PATH)) as src,closing(sqlite3.connect(destination)) as dst:src.backup(dst)
        with db.tx() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS radar_products(
                id TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT DEFAULT 'US', keywords_json TEXT NOT NULL,
                facts_json TEXT NOT NULL, restrictions_json TEXT NOT NULL, active INTEGER DEFAULT 1,
                created_at REAL, updated_at REAL, is_draft INTEGER NOT NULL DEFAULT 0)''')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_items(
                id TEXT PRIMARY KEY, product_id TEXT NOT NULL, kind TEXT NOT NULL,
                source TEXT NOT NULL, source_id TEXT, url TEXT NOT NULL,
                title TEXT NOT NULL, summary TEXT DEFAULT '', published_at TEXT DEFAULT '',
                discovered_at REAL, status TEXT DEFAULT 'candidate', metadata_json TEXT DEFAULT '{}',
                UNIQUE(product_id, url))''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_radar_items_product ON radar_items(product_id, discovered_at DESC)')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_runs(
                id TEXT PRIMARY KEY, product_id TEXT NOT NULL, day_cn TEXT NOT NULL,
                origin TEXT NOT NULL, status TEXT NOT NULL, discovered INTEGER DEFAULT 0,
                inserted INTEGER DEFAULT 0, error TEXT DEFAULT '', started_at REAL, ended_at REAL)''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_radar_runs_day ON radar_runs(product_id,day_cn,origin)')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_scripts(
                id TEXT PRIMARY KEY, product_id TEXT NOT NULL, source_ids_json TEXT NOT NULL,
                model TEXT NOT NULL, prompt_hash TEXT NOT NULL, status TEXT NOT NULL,
                title TEXT DEFAULT '', content_json TEXT DEFAULT '{}', error TEXT DEFAULT '',
                estimated_cost_usd REAL, actual_cost_usd REAL, created_at REAL, updated_at REAL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_scan_windows(
                run_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, window_news_start REAL NOT NULL,
                window_video_start REAL NOT NULL, window_end REAL NOT NULL, mode TEXT NOT NULL,
                baseline REAL, note TEXT DEFAULT '')''')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_options(
                key TEXT PRIMARY KEY, value_json TEXT NOT NULL, updated_at REAL)''')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_source_health(
                product_id TEXT NOT NULL, source TEXT NOT NULL, last_attempt REAL,
                last_success REAL, last_status TEXT NOT NULL DEFAULT 'unknown',
                last_error TEXT DEFAULT '', cooldown_until REAL DEFAULT 0,
                PRIMARY KEY(product_id,source))''')
            c.execute('''CREATE TABLE IF NOT EXISTS radar_item_metrics(
                item_id TEXT NOT NULL, observed_at REAL NOT NULL,
                view_count INTEGER, like_count INTEGER, comment_count INTEGER,
                PRIMARY KEY(item_id,observed_at))''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_radar_metric_item ON radar_item_metrics(item_id,observed_at DESC)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_radar_source ON radar_items(product_id,source_id)')
            existing_cols={r[1] for r in c.execute('PRAGMA table_info(radar_products)')}
            if 'is_draft' not in existing_cols:
                # DB backup also required when upgrading an existing product table.
                # Existing data is retained and all existing products are considered non-drafts.
                c.execute('ALTER TABLE radar_products ADD COLUMN is_draft INTEGER NOT NULL DEFAULT 0')
            # Never inject a fake AIR3 product into new installations.
            # Existing AIR3 records from earlier versions are preserved.


        _schema_initialized=True

def products():
    ensure_schema()
    rows = db.all_('SELECT * FROM radar_products ORDER BY updated_at DESC')
    for r in rows:
        r['keywords'] = _decode(r.pop('keywords_json'), [])
        r['facts'] = _decode(r.pop('facts_json'), [])
        r['restrictions'] = _decode(r.pop('restrictions_json'), [])
        r['active'] = bool(r['active'])
        r['is_draft'] = bool(r.get('is_draft',0))
    return rows


def save_product(payload):
    ensure_schema()
    name = _clean(payload.get('name'), 100)
    if not name: raise ValueError('请填写商品名称')
    product_id = _clean(payload.get('id'), 100) or _uid('product')
    prior = db.one('SELECT * FROM radar_products WHERE id=?', (product_id,))
    if payload.get('id') and not prior: raise ValueError('商品不存在，不能覆盖')
    keywords = _keys(payload.get('keywords', []))
    # Source-first onboarding: an empty keyword list is valid until GPT extracts
    # searchable US-market terms. Such products never run scheduled scans.

    facts = []
    for raw in payload.get('facts', []):
        if not isinstance(raw, dict): continue
        value = _clean(raw.get('text'), 300)
        state = raw.get('status', 'unverified')
        if state not in ('verified', 'unverified', 'prohibited'): state = 'unverified'
        if value: facts.append({'text':value, 'status':state, 'source':_clean(raw.get('source'), 300)})
    restrictions = _keys(payload.get('restrictions', []))
    is_draft = bool(prior['is_draft']) if prior else False
    if name and name not in ('新商品（待识别）','未命名商品（待分析）'):
        is_draft = False
    now = _now()
    with db.tx() as c:
        c.execute('''INSERT INTO radar_products(id,name,market,keywords_json,facts_json,restrictions_json,active,created_at,updated_at)
             VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
             name=excluded.name,market=excluded.market,keywords_json=excluded.keywords_json,
             facts_json=excluded.facts_json,restrictions_json=excluded.restrictions_json,
             active=excluded.active,updated_at=excluded.updated_at''',
             (product_id,name,'US',_json(keywords),_json(facts[:60]),_json(restrictions),
              int(bool(payload.get('active',True)) and bool(keywords)),prior['created_at'] if prior else now,now))
        c.execute('UPDATE radar_products SET is_draft=? WHERE id=?',(int(is_draft),product_id))
    return next(p for p in products() if p['id']==product_id)


def create_draft():
    """Create a *new* isolated product without reusing the previous selection.

    Uploads need a stable product id, but name/keywords are intentionally not
    required until the AI evidence-analysis step.
    """
    ensure_schema()
    pid=_uid('product');t=_now()
    with db.tx() as c:
        c.execute("""INSERT INTO radar_products(id,name,market,keywords_json,facts_json,restrictions_json,active,created_at,updated_at,is_draft)
          VALUES(?,?,?,?,?,?,?,?,?,1)""",(pid,'新商品（待识别）','US','[]','[]','[]',0,t,t))
    return next(p for p in products() if p['id']==pid)


def options():
    ensure_schema()
    defaults = {'auto_scan': False, 'scan_hour_bj': 9, 'script_model': DEFAULT_MODEL,
                'input_usd_per_million': None, 'output_usd_per_million': None,
                'daily_llm_usd_limit': 0, 'allow_auto_paid_gpt': False,
                'product_model': 'gpt-6.1-sol', 'product_input_usd_per_million': None,
                'product_output_usd_per_million': None, 'product_price_model': ''}
    # One-time model upgrade: previous defaults were GPT-5.4; new model pricing
    # MUST be re-verified because old router rates don't transfer automatically.
    migrated=db.one("SELECT value_json FROM radar_options WHERE key='product_model_policy_3982_done'")
    if not migrated:
        old=db.one("SELECT value_json FROM radar_options WHERE key='script_model'")
        if old and _decode(old['value_json'])=='gpt-5.4':
            with db.tx() as c:
                for k,v in [('script_model','gpt-6.1-sol'),('input_usd_per_million',None),('output_usd_per_million',None)]:
                    c.execute('INSERT INTO radar_options(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at',(k,_json(v),_now()))
                c.execute('INSERT INTO radar_options(key,value_json,updated_at) VALUES(?,?,?)',('product_model_policy_3982_done','true',_now()))
        else:
            with db.tx() as c:c.execute('INSERT INTO radar_options(key,value_json,updated_at) VALUES(?,?,?)',('product_model_policy_3982_done','true',_now()))
    for row in db.all_('SELECT key,value_json FROM radar_options'):
        if row['key'] in defaults: defaults[row['key']] = _decode(row['value_json'],defaults[row['key']])
    # Existing manually saved script choices stay unchanged. New installations use GPT-6.1 Sol.
    # Paid auto mode is intentionally not implemented in this phase, and cannot be toggled on.
    defaults['auto_scan'] = False
    defaults['allow_auto_paid_gpt'] = False
    return defaults


def save_options(payload):
    # Old manually entered rates remain readable for downgrade, but are no longer writable or used.
    allowed={'auto_scan','scan_hour_bj','script_model','daily_llm_usd_limit','product_model'}
    result=options()
    for key in allowed:
        if key not in payload: continue
        value=payload[key]
        if key=='auto_scan': value=False  # V4: manually triggered scan only
        elif key=='scan_hour_bj':
            value=int(value)
            if not 0<=value<=23: raise ValueError('抓取小时必须在0-23之间')
        elif key in ('script_model','product_model','product_price_model'):
            value=_clean(value,90)
            if value and not re.fullmatch(r'[a-zA-Z0-9_.:/@+-]{2,90}',value): raise ValueError('模型标识不合法')
        else:
            value = None if value in ('',None) else float(value)
            if value is not None and not 0<=value<=10000: raise ValueError('费率或限额无效')
        result[key] = value
    with db.tx() as c:
        for key in allowed:
            if key in payload:
                c.execute('INSERT INTO radar_options(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at', (key,_json(result[key]),_now()))
    return options()


def record_startup(now=None):
    """Call once per SERVER start. Previous start remains persistent across upgrades."""
    ensure_schema()
    t=float(now if now is not None else _now())
    with _lock,db.tx() as c:
        prior=c.execute("SELECT value_json FROM radar_options WHERE key='runtime_last_start'").fetchone()
        prev=_decode(prior[0]) if prior else None
        if isinstance(prev,(float,int)) and 0<prev<t:
            c.execute('INSERT INTO radar_options(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at',('runtime_previous_start',_json(float(prev)),t))
        c.execute('INSERT INTO radar_options(key,value_json,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at',('runtime_last_start',_json(t),t))
    return {'current_start':t,'previous_start':prev}


def _stored_time(key):
    row=db.one('SELECT value_json FROM radar_options WHERE key=?',(key,))
    val=_decode(row['value_json']) if row else None
    return float(val) if isinstance(val,(int,float)) and val>0 else None


def scan_window(product_id,now=None):
    """7-day fresh-first, extend only days 8-14 on evidence deficiency.

    Historical catch-up never silently displaces the latest 7-day discovery.
    Previous boot/success dates are recorded for diagnostics & future catch-up.
    """
    ensure_schema();t=float(now if now is not None else _now())
    prev=_stored_time('runtime_previous_start')
    r=db.one("SELECT ended_at FROM radar_runs WHERE product_id=? AND status IN ('completed','partial') AND ended_at<? ORDER BY ended_at DESC LIMIT 1",(product_id,t))
    last=float(r['ended_at']) if r and r['ended_at'] else None
    candidates=[x for x in (prev,last) if x and x<t]
    baseline=min(candidates) if candidates else None
    note='优先最近7天；只有有效商品相关证据不足，才补查第8—14天；不按数量凑热点'
    return {'news_start':t-7*86400,'video_start':t-7*86400,'end':t,
            'mode':'fresh_first','baseline':baseline,'note':note,
            'news_start_iso':dt.datetime.fromtimestamp(t-7*86400,dt.timezone.utc).isoformat(),
            'video_start_iso':dt.datetime.fromtimestamp(t-7*86400,dt.timezone.utc).isoformat(),
            'end_iso':dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat(),
            'fallback_older_start_iso':dt.datetime.fromtimestamp(t-14*86400,dt.timezone.utc).isoformat()}


def _published_timestamp(value):
    if not value:return None
    try:
        v=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
    except ValueError:
        try:v=email.utils.parsedate_to_datetime(str(value))
        except (ValueError,TypeError):return None
    if v.tzinfo is None:return None
    return v.timestamp()


def scan_window_state(product_id):
    ensure_schema()
    last=db.one("SELECT w.* FROM radar_scan_windows w JOIN radar_runs r ON r.id=w.run_id WHERE w.product_id=? ORDER BY r.started_at DESC LIMIT 1",(product_id,)) if product_id else None
    return {'next':scan_window(product_id) if product_id else None,'last':last}


def _item_public(row):
    r=dict(row);r['metadata']=_decode(r.pop('metadata_json'),{})
    return r


def items(product_id=None,limit=120):
    ensure_schema()
    if product_id:
        rows=db.all_('SELECT * FROM radar_items WHERE product_id=? ORDER BY discovered_at DESC LIMIT ?', (product_id,min(limit,500)))
    else: rows=db.all_('SELECT * FROM radar_items ORDER BY discovered_at DESC LIMIT ?', (min(limit,500),))
    result=[_item_public(row) for row in rows]
    # Recompute freshness at read time. Stored labels from older import dates are stale.
    now=_now()
    for item in result:
        stamp=_published_timestamp(item.get('published_at'))
        age=(now-stamp)/86400 if stamp is not None else None
        fresh='近7天' if age is not None and 0<=age<=7 else '第8至14天' if age is not None and 7<age<=14 else '历史参考/发布时间待核实'
        meta=item.get('metadata') or {}
        meta['freshness']=fresh
        meta['age_days']=round(age,2) if age is not None and age>=0 else None
        region=str(meta.get('region') or '').upper().strip()
        meta['us_status']='美国US' if region=='US' else ('地区待验证' if region in ('','待验证','UNKNOWN') else '非美国地区')
        item['metadata']=meta
    from . import viral_review
    video_ids=[it['id'] for it in result if it['kind']=='video']
    status_map=viral_review.statuses(video_ids)
    for it in result:
        if it['kind']=='video':it['video_review']=status_map[it['id']]
    # Most recently published, genuinely relevant content precedes older and unverified items.
    result.sort(key=lambda row:(
          1 if row['metadata'].get('freshness')=='近7天' else 0 if row['metadata'].get('freshness')=='第8至14天' else -1,
          1 if row['metadata'].get('us_status')=='美国US' else 0,
          1 if row['metadata'].get('metadata_relevant') is True else 0,
          _published_timestamp(row.get('published_at')) or 0,
          row.get('discovered_at') or 0),reverse=True)
    return result


def scripts(product_id=None,limit=40):
    ensure_schema()
    if product_id:
        rows=db.all_('SELECT * FROM radar_scripts WHERE product_id=? ORDER BY created_at DESC LIMIT ?', (product_id,min(limit,200)))
    else: rows=db.all_('SELECT * FROM radar_scripts ORDER BY created_at DESC LIMIT ?', (min(limit,200),))
    for r in rows:
        r['source_ids']=_decode(r.pop('source_ids_json'),[])
        r['content']=_decode(r.pop('content_json'),{})
    return rows


def recent_runs(limit=30):
    ensure_schema()
    rows=db.all_('SELECT * FROM radar_runs ORDER BY started_at DESC LIMIT ?', (limit,))
    for r in rows:
        w=db.one('SELECT * FROM radar_scan_windows WHERE run_id=?',(r['id'],))
        if w:
            r['window']={'mode':w['mode'],'note':w['note'],'news_start_iso':dt.datetime.fromtimestamp(w['window_news_start'],dt.timezone.utc).isoformat(),
                'video_start_iso':dt.datetime.fromtimestamp(w['window_video_start'],dt.timezone.utc).isoformat(),
                'end_iso':dt.datetime.fromtimestamp(w['window_end'],dt.timezone.utc).isoformat()}
    return rows


def add_item(product_id,kind,source,url,title,summary='',published_at='',metadata=None):
    ensure_schema()
    if not db.one('SELECT id FROM radar_products WHERE id=?',(product_id,)):
        raise ValueError('商品不存在')
    url=_clean(url,1800)
    p=urllib.parse.urlsplit(url)
    if p.scheme not in ('https','http') or not p.netloc or p.username or p.password:
        raise ValueError('来源链接必须是可访问的 http/https 地址')
    if kind not in ('news','video','ad','trend'): raise ValueError('来源类型无效')
    title=_clean(title,350)
    if not title: raise ValueError('请输入标题')
    created=_uid('trend')
    with db.tx() as c:
        c.execute('''INSERT OR IGNORE INTO radar_items(id,product_id,kind,source,url,title,summary,published_at,
            discovered_at,status,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
            (created,product_id,kind,_clean(source,90),url,title,_clean(summary,1500),
             _clean(published_at,90),_now(),'candidate',_json(metadata or {})))
        row=c.execute('SELECT * FROM radar_items WHERE product_id=? AND url=?',(product_id,url)).fetchone()
        if row and row['id']!=created and isinstance(metadata,dict) and metadata:
            old=_decode(row['metadata_json'],{}) or {}
            # Empty/unknown provider fields must never destroy previously valid values.
            for key,value in metadata.items():
                if value is None or value=='' or value=={}:continue
                if key=='metrics' and isinstance(value,dict):
                    old_metrics=old.get('metrics') if isinstance(old.get('metrics'),dict) else {}
                    old['metrics']={**old_metrics,**value}
                else:old[key]=value
            # Do not silently overwrite a manual edit to the source title/description.
            title_new=title if row['source']==_clean(source,90) and title else row['title']
            summary_new=_clean(summary,1500) if row['source']==_clean(source,90) and summary else row['summary']
            published_new=_clean(published_at,90) or row['published_at']
            c.execute('UPDATE radar_items SET title=?,summary=?,published_at=?,metadata_json=? WHERE id=?',
                (title_new,summary_new,published_new,_json(old),row['id']))
    return _item_public(db.one('SELECT * FROM radar_items WHERE id=?',(row['id'],)))


def add_manual(payload):
    url=str(payload.get('url') or '').strip()
    if not _is_tiktok_url(url):
        raise ValueError('热点收集只接受 TikTok 视频、创意中心或商城相关链接；新闻及 YouTube 不进入此流程')
    kind=payload.get('kind','video')
    if kind not in ('video','ad','trend'):kind='video'
    return add_item(_clean(payload.get('product_id'),100),kind,'TikTok链接导入',
                    url,payload.get('title',''),payload.get('summary',''),
                    payload.get('published_at',''),
                    {'manually_imported':True,'metrics_unverified':True,'metadata_relevant':None,
                     'evidence_grade':'D','heat_label':'TikTok内容已存档，热度待核实',
                     'needs_commercial_review':True})


# Only TikTok-native content is admissible for the product-focused creative radar.
_TIKTOK_HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com",
                 "vt.tiktok.com", "ads.tiktok.com", "shop.tiktok.com",
                 "seller-us.tiktok.com", "business.tiktok.com"}

def _is_tiktok_url(url):
    try:
        p=urllib.parse.urlsplit(str(url or ""))
        host=(p.hostname or "").lower().rstrip('.')
        return p.scheme == 'https' and (host in _TIKTOK_HOSTS or host.endswith('.tiktok.com')) and not p.username and not p.password
    except Exception:
        return False

def import_tiktok_batch(payload):
    """Import an authorized export, never claim a manually provided metric was verified by us."""
    pid=_clean(payload.get('product_id'),100)
    if not db.one('SELECT id FROM radar_products WHERE id=?',(pid,)):
        raise ValueError('商品不存在')
    rows=payload.get('records')
    if not isinstance(rows,list) or not 1<=len(rows)<=100:
        raise ValueError('每次提交1—100条 TikTok 来源记录')
    if len(_json(rows))>200000:raise ValueError('本次导入的数据过大')
    inserted=0; skipped=[]
    for index, entry in enumerate(rows):
        if not isinstance(entry,dict):skipped.append({'row':index+1,'error':'非对象'});continue
        url=str(entry.get('url') or entry.get('video_url') or '').strip()
        title=_clean(entry.get('title') or entry.get('description'),350)
        if not _is_tiktok_url(url) or not title:
            skipped.append({'row':index+1,'error':'缺少标题或不是 TikTok 官方域名链接'});continue
        kind=entry.get('kind','video')
        if kind not in ('video','ad','trend'):kind='video'
        rawmetrics=entry.get('metrics') if isinstance(entry.get('metrics'),dict) else {}
        metrics={}
        for key in ('viewCount','likeCount','commentCount','shareCount'):
            v=rawmetrics.get(key,entry.get(key))
            if isinstance(v,int) and not isinstance(v,bool) and 0<=v<=10**15:metrics[key]=v
        region=_clean(entry.get('region') or '待验证',12).upper()
        published=entry.get('published_at') or entry.get('create_time') or ''
        published_ts=_published_timestamp(published)
        age_days=(time.time()-published_ts)/86400 if published_ts else None
        freshness='近7天' if age_days is not None and 0<=age_days<=7 else '第8至14天' if age_days is not None and 7<age_days<=14 else '历史参考/发布时间待核实'
        metadata={'manually_imported':True,'origin':entry.get('provider_origin') or 'tiktok_export',
          'region':region,'post_id':_clean(entry.get('post_id'),45),
          'media_url':str(entry.get('media_url') or '')[:5000],
          'cover_url':str(entry.get('cover_url') or '')[:5000],
          'video_duration':entry.get('video_duration'), 'subtitle_info':(entry.get('subtitle_info') or [])[:6] if isinstance(entry.get('subtitle_info'),list) else [], 'freshness':freshness,
          'metrics':metrics,'metrics_unverified':True,'data_quality_verified':False,
          'metric_provenance':'第三方/人工导入，尚未独立核实','heat_label':'TikTok来源已存档，热度待核实',
          'metadata_relevant':None,'evidence_grade':'D','needs_commercial_review':True}
        was=db.one('SELECT id FROM radar_items WHERE product_id=? AND url=?',(pid,url))
        add_item(pid,kind,'TikTok授权数据导入',url,title,entry.get('summary') or entry.get('description') or '',entry.get('published_at') or entry.get('create_time') or '',metadata)
        if not was:inserted+=1
    return {'inserted':inserted,'skipped':skipped,'received':len(rows),'paid_calls':0,
            'notice':'导入记录并非平台官方认证热度。系统不会抓取未授权的视频，也不会发起 GPT 或视频生成。'}

def _https_json(url,timeout=10):
    req=urllib.request.Request(url,headers={'User-Agent':'SeedanceStudio-HotRadar/1.0','Accept':'application/json'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read(1200000).decode('utf-8'))


def _news_rss(term,timeout=10,window_start=None,window_end=None):
    # Fixed official Google News RSS search endpoint; never fetch a URL supplied by a user.
    start=dt.datetime.fromtimestamp(window_start or (_now()-14*86400),dt.timezone.utc).strftime('%Y-%m-%d')
    end=dt.datetime.fromtimestamp(window_end or _now(),dt.timezone.utc).date()+dt.timedelta(days=1)
    q=urllib.parse.quote(f'{term} after:{start} before:{end.isoformat()}')
    url=f'https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en'
    req=urllib.request.Request(url,headers={'User-Agent':'SeedanceStudio-HotRadar/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r: raw=r.read(1000000)
    root=ET.fromstring(raw)
    result=[]
    for x in root.findall('./channel/item')[:10]:
        def val(tag):return _clean(x.findtext(tag,''), 1400)
        title=val('title');link=val('link')
        if title and link:
            result.append({'kind':'news','source':'Google News RSS（新闻订阅）','title':title,
                           'url':link,'summary':re.sub('<[^>]*>','',val('description')),
                           'published_at':val('pubDate')})
    return result


def _youtube_search(term,api_key,timeout=10,window_start=None,window_end=None):
    # Official YouTube APIs only. Search order=date is NOT evidence of virality.
    params=urllib.parse.urlencode({'part':'snippet','type':'video','maxResults':10,'q':term,
          'regionCode':'US','relevanceLanguage':'en','order':'date',
          'publishedAfter':dt.datetime.fromtimestamp(window_start or (_now()-7*86400),dt.timezone.utc).isoformat().replace('+00:00','Z'),
          'publishedBefore':dt.datetime.fromtimestamp(window_end or _now(),dt.timezone.utc).isoformat().replace('+00:00','Z'),'key':api_key})
    payload=_https_json('https://www.googleapis.com/youtube/v3/search?'+params,timeout)
    vids=[]
    for x in payload.get('items',[]):
        vid=(x.get('id') or {}).get('videoId');meta=x.get('snippet') or {}
        if vid:vids.append((vid,meta))
    if not vids:return []
    # One batched statistics request for each search batch, never one per video.
    stats={}
    params=urllib.parse.urlencode({'part':'statistics','id':','.join(vid for vid,_ in vids),'key':api_key})
    try:
        details=_https_json('https://www.googleapis.com/youtube/v3/videos?'+params,timeout)
        stats={i.get('id'):i.get('statistics',{}) for i in details.get('items',[])}
    except Exception as exc:
        # Preserve discovery while exposing the partial source-level metrics failure.
        for vid,_ in vids:stats[vid]={'_metrics_error':_clean(str(exc),130)}
    out=[]
    for vid,meta in vids:
        raw=stats.get(vid,{})
        metrics={k:int(raw[k]) for k in ('viewCount','likeCount','commentCount') if str(raw.get(k,'')).isdigit()}
        out.append({'kind':'video','source':'YouTube Data API（官方接口）',
                    'source_id':vid,'title':meta.get('title',''),'url':'https://www.youtube.com/watch?v='+vid,
                    'summary':meta.get('description',''),'published_at':meta.get('publishedAt',''),
                    'metrics':metrics,'metrics_note':raw.get('_metrics_error') or ('' if metrics else '未提供公开视频互动指标')})
    return out


def _word_tokens(s):
    return [x for x in re.findall(r'[a-z0-9]{3,}',str(s or '').lower())
            if x not in {'with','your','from','this','that','have','more','will','today','video','shop','news','best','what','about','2026','2025','the','and','for'}]


def _relevance(item,product,term):
    # Conservative metadata match, NOT a substitute for video semantic analysis.
    combined=' '.join([item.get('title',''),item.get('summary','')[:900]]).lower()
    hay=set(_word_tokens(combined)); query=set(_word_tokens(term)); product_words=set(_word_tokens(product.get('name','')))
    restriction_terms=set()
    for r in product.get('restrictions',[]):restriction_terms.update(_word_tokens(r))
    for f in product.get('facts',[]):
        if f.get('status')=='prohibited':restriction_terms.update(_word_tokens(f.get('text','')))
    overlap=len(query & hay)
    product_overlap=len(product_words & hay)
    # One-token generic phrase is not adequate relevance evidence.
    relevant=(overlap>=min(2,len(query)) and overlap>0) or product_overlap>=2
    reason='检索词与标题/摘要匹配' if relevant else '仅为搜索返回结果，商品关联尚待核实'
    # Don't auto-classify product restrictions based only on broad shared words.
    restriction_hint=sorted(restriction_terms & hay)[:7]
    return {'verified':False,'metadata_relevant':relevant,'matched_keywords':sorted(query & hay)[:8],
            'restriction_overlap_requires_review':restriction_hint,'reason':reason}


def _canonical_source_id(entry):
    if entry.get('source_id'):return _clean(entry['source_id'],120)
    try:
        parts=urllib.parse.urlsplit(entry.get('url',''))
        if 'youtu' in parts.netloc.lower():
            key=urllib.parse.parse_qs(parts.query).get('v')
            if key:return _clean(key[0],120)
    except Exception:pass
    return None


def _event_signature(title):
    tokens=[t for t in _word_tokens(title) if len(t)>3]
    return ' '.join(sorted(set(tokens))[:12])


def _source_health(product_id,source):
    return db.one('SELECT * FROM radar_source_health WHERE product_id=? AND source=?',(product_id,source))


def _record_source_health(product_id,source,status,now,error=''):
    with db.tx() as c:
        c.execute('''INSERT INTO radar_source_health(product_id,source,last_attempt,last_success,last_status,last_error,cooldown_until)
                     VALUES(?,?,?,?,?,?,?) ON CONFLICT(product_id,source) DO UPDATE SET
                     last_attempt=excluded.last_attempt,last_success=COALESCE(excluded.last_success,radar_source_health.last_success),
                     last_status=excluded.last_status,last_error=excluded.last_error,cooldown_until=excluded.cooldown_until''',
                  (product_id,source,now,now if status=='ok' else None,status,_clean(error,320),
                   now+SOURCE_COOLDOWN_SECONDS if status=='failed' else 0))


def _keyword_plan(product):
    # Diversify confirmed keyword intents; avoid paid AI and unsupported invented claims.
    keys=product.get('keywords') or []
    groups={'product':[],'usage':[],'need':[]}
    for word in keys:
        lower=word.lower()
        if any(k in lower for k in ('how to','problem','alternative','vs ','review','worth')):kind='need'
        elif any(k in lower for k in ('camp','travel','outdoor','inflatable','mattress','equipment','setup','portable')):kind='usage'
        else:kind='product'
        groups[kind].append(word)
    # Interleave intent categories; avoid the old first-4-keywords bias.
    out=[]
    while len(out)<MAX_SEARCH_TERMS and any(groups.values()):
        for kind in ('product','usage','need'):
            if groups[kind] and len(out)<MAX_SEARCH_TERMS:out.append(groups[kind].pop(0))
    return out


def _record_metric(item_id,entry,observed_at):
    met=entry.get('metrics') or {}
    if not met:return
    with db.tx() as c:
        c.execute('''INSERT OR IGNORE INTO radar_item_metrics(item_id,observed_at,view_count,like_count,comment_count)
           VALUES(?,?,?,?,?)''',(item_id,observed_at,met.get('viewCount'),met.get('likeCount'),met.get('commentCount')))


def _evidence_quality(product,entry,term,stamp,now):
    match=_relevance(entry,product,term)
    days=max(0,(now-stamp)/86400)
    metrics=entry.get('metrics') or {}
    has_metrics=entry.get('kind')=='video' and 'viewCount' in metrics
    evidence='verified_video_metrics' if has_metrics else 'recent_content_only'
    # Data grade concerns evidence completeness, NOT confidence of sales or virality.
    return {**match,'source_status':'published_date_present_on_feed',
      'age_days':round(days,2),'evidence_grade':'B' if has_metrics else 'C',
      'heat_label':'互动数据可核对，不代表成交' if has_metrics else '近期相关内容（热度未验证）',
      'metrics':metrics,'metrics_note':entry.get('metrics_note',''),
      'needs_visual_analysis':True if entry.get('kind')=='video' else False,
      'source_kind':entry.get('kind'),'evidence_type':evidence,'data_quality_verified':has_metrics}


def scan(product_id,origin='manual',news_fetch=None,youtube_fetch=None,now=None):
    """Rank stored TikTok content; external discovery remains disabled until an authorized
    TikTok-specific provider with tested fields/permissions is configured.
    Never silently substitute YouTube or generic news.
    """
    ensure_schema()
    product=next((p for p in products() if p['id']==product_id),None)
    if not product:raise ValueError('商品不存在')
    if origin=='scheduled' and not product['active']:return {'skipped':'商品未启用'}
    if not product['keywords']:
        if origin=='scheduled':return {'skipped':'商品尚无关键词'}
        raise ValueError('请先在商品知识中心完成商品分析，获取 TikTok 美区搜索词')
    window=scan_window(product_id,now);t=window['end']
    day=dt.datetime.fromtimestamp(t,_BJ).date().isoformat()
    if origin=='scheduled':
        prev=db.one("SELECT id FROM radar_runs WHERE product_id=? AND day_cn=? AND status IN ('completed','waiting_source') ORDER BY started_at DESC LIMIT 1",(product_id,day))
        if prev:return {'skipped':'今日已检查 TikTok 数据源','run_id':prev['id']}
    run_id=_uid('scan')
    with db.tx() as c:
        c.execute('INSERT INTO radar_runs(id,product_id,day_cn,origin,status,started_at) VALUES(?,?,?,?,?,?)',(run_id,product_id,day,origin,'running',t))
        c.execute('INSERT INTO radar_scan_windows(run_id,product_id,window_news_start,window_video_start,window_end,mode,baseline,note) VALUES(?,?,?,?,?,?,?,?)',
                (run_id,product_id,t-7*86400,t-7*86400,t,'tiktok_recent_7d',window['baseline'],'仅 TikTok。优先7天，有效候选不足扩至14天；无自动授权采集接口时只检查已导入的记录'))
    stored=db.all_('SELECT * FROM radar_items WHERE product_id=?',(product_id,))
    recent=[];older=[];undated=0;excluded_legacy=0
    for row in stored:
        if not _is_tiktok_url(row['url']):excluded_legacy+=1;continue
        stamp=_published_timestamp(row['published_at'])
        if stamp is None:undated+=1;continue
        if not t-14*86400<=stamp<=t+300:continue
        existing=_item_public(row)
        # Exact matching is a low-cost hint, not an AI validation.
        relevance=max((_relevance({'title':row['title'],'summary':row['summary']},product,k)
                       for k in product['keywords']),key=lambda x:len(x['matched_keywords']),default={})
        qualified=bool(relevance.get('metadata_relevant'))
        meta=existing['metadata'];meta.update({'metadata_relevant':qualified,'matched_keywords':relevance.get('matched_keywords',[]),
               'source_type':'tiktok','heat_label':'TikTok相关素材（热度未经平台独立核验）',
               'needs_commercial_review':True,'data_quality_verified':False,
               'evidence_grade':'C' if qualified else 'D'})
        with db.tx() as c:c.execute('UPDATE radar_items SET metadata_json=? WHERE id=?',(_json(meta),row['id']))
        entry={'id':row['id'],'timestamp':stamp,'relevant':qualified}
        (recent if stamp>=t-7*86400 else older).append(entry)
    candidates=[e for e in recent if e['relevant']]
    fallback=len(candidates)==0
    if fallback:candidates.extend(e for e in older if e['relevant'])
    mode='tiktok_extended_14d' if fallback else 'tiktok_recent_7d'
    note=('已回看第8—14天；' if fallback else '近7天已有相关资料；')+'仅针对已导入的 TikTok 数据。未接入经授权的自动热点采集接口。'
    status='completed' if stored and any(_is_tiktok_url(x['url']) for x in stored) else 'waiting_source'
    with db.tx() as c:
        c.execute('UPDATE radar_runs SET status=?,discovered=?,inserted=?,error=?,ended_at=? WHERE id=?',
                  (status,len(recent)+len(older),0,'' if status=='completed' else '未配置自动 TikTok 数据源，且没有已导入的 TikTok 来源',_now(),run_id))
        c.execute('UPDATE radar_scan_windows SET mode=?,note=?,window_video_start=?,window_news_start=? WHERE run_id=?',
                  (mode,note,t-(14 if fallback else 7)*86400,t-(14 if fallback else 7)*86400,run_id))
    return {'id':run_id,'status':status,'discovered':len(recent)+len(older),'inserted':0,
       'quality_candidates':len(candidates),'filtered_legacy':excluded_legacy,'undated_skipped':undated,
       'window':{**window,'mode':mode,'note':note},'paid_ai_calls':0,'video_creation_calls':0,
       'auto_tiktok_source_connected':False,
       'coverage_note':'TikTok官方公开创意中心不能等同于开放商业抓取接口；目前只能扫描手动/授权导入的数据。若需全自动采集，需要先验证 TikTok 合法数据接口或经许可的第三方服务。'}


def _script_sources(product_id,ids):
    if not isinstance(ids,list) or not ids or len(ids)>MAX_SOURCES_FOR_GPT: raise ValueError('请选1—8条来源')
    selected=[]
    for source_id in dict.fromkeys(str(x) for x in ids):
        r=db.one('SELECT * FROM radar_items WHERE id=? AND product_id=?',(source_id,product_id))
        if not r: raise ValueError('来源不存在或不属于当前商品')
        if not _is_tiktok_url(r['url']):
            raise ValueError('仅允许 TikTok 平台来源生成本系统带货脚本，请取消勾选旧版 YouTube/新闻资料')
        selected.append(_item_public(r))
    return selected


def _script_preview_hash(model, system, user_text, out_tokens, estimate):
    return hashlib.sha256(_json([model,system,user_text,out_tokens,estimate]).encode()).hexdigest()


def script_preview(payload):
    ensure_schema()
    pid=_clean(payload.get('product_id'),100)
    product=next((p for p in products() if p['id']==pid),None)
    if not product: raise ValueError('商品不存在')
    source_ids=payload.get('source_ids',[])
    selected=_script_sources(pid,source_ids)
    if len(selected)!=1 or selected[0]['kind']!='video':
        raise ValueError('精简流程一次仅拆解一条真实 TikTok 视频，请只勾选一条视频')
    from . import viral_review
    asset=viral_review.asset_for_item(selected[0]['id'])
    selected_warnings=[]
    for it in selected:
        md=it.get('metadata') or {}
        if md.get('metadata_relevant') is False:selected_warnings.append(f'{it["id"]}: 标题摘要与商品关联未验证')
        if md.get('potential_event_duplicate'):selected_warnings.append(f'{it["id"]}: 可能与其他热点重复')
        if not md.get('data_quality_verified'):selected_warnings.append(f'{it["id"]}: 未核实互动或销量')
    rates=options(); model=_clean(payload.get('model') or rates['script_model'],90)
    if not re.fullmatch(r'[a-zA-Z0-9_.:/@+-]{2,90}',model):raise ValueError('模型编号不合法')
    fact_verified=[x for x in product['facts'] if x['status']=='verified']
    if not fact_verified:raise ValueError('请先在商品知识中心核实至少一条商品事实，再付费生成商品脚本')
    constraints=product['restrictions']+[x['text'] for x in product['facts'] if x['status']=='prohibited']
    # Reuse the standalone Product Knowledge Center's summary for creative context,
    # but NEVER elevate AI-inferred audiences or selling points to verified claims.
    memory={}
    if db.one("SELECT name FROM sqlite_master WHERE type='table' AND name='product_memories'"):
        saved=db.one('SELECT summary_json FROM product_memories WHERE product_id=?',(pid,))
        if saved:memory=_decode(saved['summary_json'],{}) or {}
    context_unverified={'summary_cn':str(memory.get('summary_cn') or '')[:1000],
        'audiences_cn':(memory.get('audiences_cn') or [])[:10],
        'selling_points_cn':(memory.get('selling_points_cn') or [])[:10],
        'notice':'这些是AI整理的商品画像，只能作为创意方向参考；涉及功能、规格和效果的宣传必须从已确认事实中取证。'}
    market=us_marketing.context()
    prompt={
      'us_market_calendar':market,'product':product['name'],'market':'US','target':'TikTok Shop 美国站短视频与商品卡转化',
      'verified_facts':fact_verified,'unverified_facts':[x for x in product['facts'] if x['status']=='unverified'],
      'restrictions':constraints,
      'product_memory_notice':'商品记忆中未确认的事实不可作为功能、参数或功效证据。',
      'product_context_unverified':context_unverified,
      'video_reference':{'asset_id':asset['id'],'sha256':asset['sha256'],
        'duration_seconds':asset['duration'],'status':'真实本地视频已绑定；付费前提取实际画面'},
      'evidence':[{'id':i['id'],'kind':i['kind'],'title':i['title'],'url':i['url'],
                   'summary':i['summary'][:500],'published_at':i['published_at'],
                   'evidence_quality':{k:v for k,v in (i.get('metadata') or {}).items() if k in ('metrics','region','freshness','heat_label','evidence_grade')},'notice':'互动量不等于成交；只能按照实际视频帧和字幕得出拆解结论。'} for i in selected],
      'directive':'必须先依据真实视频画面拆解创意，再适配当前商品；强制遵守美国市场日期、季节、仅可宣传已确认商品事实；输出英文口播及中文翻译。'
    }
    system=('你是美区电商广告创意分析师。把热点当作创意素材，而非产品性能证据。'
      '严格区分来源事实、元数据分析和创意假设。不可编造参数、用户评价、销量、新闻数据或竞品效果。'
      '仅使用本次实际传入的时间戳关键帧拆解可见画面，缺失的连续动作、口播、声音必须明确标注待验证，不能假称看过连续视频。'
      '发现来源相关性低、未验证的产品用途或无购买动机时，必须在风险提示中说明，不得为了凑热点编造。'
      '所有创意仅针对 TikTok Shop 美区商品短视频；强制考虑美区3秒开头钩子、真人UGC风格、9:16、真实产品展示与明确购买动机，不能把观看量视为订单或转化率。' '美国日期与季节是硬规则，不可省略。必须以JSON对象返回字段：market_country(必须US),as_of_us(必须与提供日期一致),season(必须与美国日历一致),seasonal_fit_cn,title_cn,reason_cn,hook_en,hook_zh,script_segments'
      '(数组元素字段为duration_s,narration_en,narration_zh,visual_cn,source_ids),'
      'risk_notes_cn(数组),hypothesis_cn，另须提供video_breakdown数组，每项time_seconds、visual_cn、creative_insight_cn；仅分析有真实证据的画面。'
      '还必须提供product_match对象：verdict取suitable/needs_changes/unsuitable，reason_cn说明参考视频与当前商品的购买需求和使用场景是否匹配，'
      'verified_claims_used为数组，只能逐字选择提供的已核实商品事实text；transferable_elements_cn为可迁移的镜头创意数组。'
      '不相关或必须虚构商品功能才能套用时，verdict必须unsuitable，不能硬写成可用广告。美国不同地区气候不可臆测，不得照搬原视频人物或版权内容。')
    user_text=_json(prompt)
    # Conservative budget preview; provider-specific token usage is checked after the call.
    # Includes conservative extra visual-token allowance for up to seven JPEG frames.
    in_tokens=max(600,int((len(system)+len(user_text))/2.5)+350)+12*1600
    out_tokens=4500 if model.startswith(('gpt-6','gpt-5.6','gpt-5.5','o1','o3','o4')) else 1900
    price=model_pricing.get(model)
    ir=price.get('input_usd_per_million');orr=price.get('output_usd_per_million')
    estimate=(in_tokens*ir+out_tokens*orr)/1e6 if ir is not None and orr is not None else None
    return {'model':model,'product_id':pid,'source_ids':[x['id'] for x in selected],
            'preview_hash':_script_preview_hash(model,system,user_text,out_tokens,estimate),
            'system':system,'user_text':user_text,'estimated_input_tokens':in_tokens,
            'max_output_tokens':out_tokens,'estimated_cost_usd':estimate,
            'price_verified':bool(price.get('available')),'price':price,'source_count':len(selected),'evidence_warnings':selected_warnings,'visual_source_confirmed':True,
            'market_context':market,'video_asset_id':asset['id'],
            'warning':price.get('warning','费用按 OpenLux 自动获取的参考价估算，实际以账号账单为准。')+' 本次视觉分析需要模型支持图片消息，未实测时不可视为已兼容。'}


def validate_product_match(document,product):
    match=document.get('product_match')
    if not isinstance(match,dict):return ['缺少来源视频与当前商品的匹配分析']
    errors=[]
    if match.get('verdict')!='suitable':errors.append('商品匹配尚未通过，需人工调整或另选视频')
    if not isinstance(match.get('reason_cn'),str) or not match['reason_cn'].strip():errors.append('缺少商品匹配依据')
    verified={f.get('text') for f in product.get('facts',[]) if f.get('status')=='verified'}
    claims=match.get('verified_claims_used')
    if not isinstance(claims,list) or not claims or any(not isinstance(x,str) or x not in verified for x in claims):
        errors.append('脚本使用的商品声明未绑定到已核实事实')
    elements=match.get('transferable_elements_cn')
    if not isinstance(elements,list) or not elements or any(not isinstance(x,str) or not x.strip() for x in elements):
        errors.append('缺少可迁移的镜头创意说明')
    return errors


def generate_script(payload):
    # Explicit per-call approval; never invoked from scheduler.
    if payload.get('approve_paid') is not True:raise ValueError('需单次确认付费 GPT 脚本生成')
    preview=script_preview(payload)
    if not preview.get('preview_hash') or payload.get('preview_hash')!=preview['preview_hash']:
        raise ValueError('商品事实、来源视频、模型或报价已变化，请重新预检后再确认；未提交付费请求')
    if not secrets_store.get('openlux_api_key'):
        raise ValueError('尚未配置 OpenLux 密钥，未提交付费调用')
    estimate=preview['estimated_cost_usd']
    if estimate is None:raise ValueError('无法自动核实模型报价，未知成本禁止自动提交')
    maximum=float(payload.get('max_cost_usd') or 0)
    limit=options()['daily_llm_usd_limit']
    import math
    if not math.isfinite(maximum) or maximum<=0 or estimate>maximum:raise ValueError('本次预估费用超出单次授权上限')
    if limit is None or limit<=0:raise ValueError('请先设置每日 GPT 预算上限')
    today=dt.datetime.now(_BJ)
    start=dt.datetime.combine(today.date(), dt.time.min,tzinfo=_BJ).timestamp()
    model=preview['model']; user_text=preview['user_text']; system=preview['system']
    # Extract actual local frames BEFORE reserving any paid GPT task.
    # This stage neither downloads media nor calls external APIs.
    from . import viral_review
    visual=viral_review.visual_evidence(preview['source_ids'][0],max_frames=12)
    prompt_hash=hashlib.sha256((model+'|'+user_text+'|video_sha256='+str(visual['sha256'])).encode()).hexdigest()
    with _lock,db.tx() as c:
        c.execute('BEGIN IMMEDIATE')
        daily=c.execute("SELECT COALESCE(SUM(estimated_cost_usd),0) FROM radar_scripts WHERE created_at>=? AND status IN ('running','completed','needs_review','submission_unknown','reconciled_charged')",(start,)).fetchone()
        other=0
        if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='product_analysis_runs'").fetchone():
            previous=c.execute("SELECT COALESCE(SUM(estimated_usd),0) FROM product_analysis_runs WHERE created_at>=? AND status IN ('running','completed','parse_failed','submission_unknown','reconciled_charged')",(start,)).fetchone()
            other=float(previous[0] or 0)
        if float(daily[0] or 0)+other+estimate>limit:raise ValueError('商品资料分析与热点脚本合计将超出今日 GPT 预算')
        existing=c.execute("SELECT id,status FROM radar_scripts WHERE prompt_hash=? AND status IN ('running','completed','needs_review','submission_unknown','reconciled_charged') ORDER BY created_at DESC LIMIT 1",(prompt_hash,)).fetchone()
        if existing:raise ValueError('相同输入已存在有效或结果未知的付费任务，禁止重复扣费。请修改来源或核对历史结果。')
        sid=_uid('script');now=_now()
        c.execute('''INSERT INTO radar_scripts(id,product_id,source_ids_json,model,prompt_hash,status,estimated_cost_usd,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?)''',(sid,preview['product_id'],_json(preview['source_ids']),model,prompt_hash,'running',estimate,now,now))
    # Single paid create attempt. If result is uncertain, do not re-submit automatically.
    from . import openlux, task_observability
    task_observability.record('script',sid,'等待 GPT 创意脚本','running','已保存请求和本次费用上限，禁止自动重试')
    try:
        user_parts=[{'type':'text','text':user_text+'\n真实视频时长：'+str(visual['duration'])+'秒。'
              +'\n本地音频状态：'+visual['speech_state']+'。'
              +'\n可用转写：'+_json(visual['speech'])+'。'
              +'\n下方附带真实视频时间戳帧。video_breakdown必须根据真实画面与时间戳填写；不知道的内容写待验证。'}]
        user_parts.extend(visual['parts'])
        request={'model':model,'messages':[{'role':'system','content':system},{'role':'user','content':user_parts}],
                 'response_format':{'type':'json_object'}}
        if model.startswith(('gpt-6','gpt-5.6','gpt-5.5','o1','o3','o4')):request['max_completion_tokens']=preview['max_output_tokens']
        else:request.update({'temperature':0.55,'max_tokens':preview['max_output_tokens']})
        task_observability.record('script',sid,'等待 OpenLux 同步响应','running','模型 '+model)
        response=openlux.request_json('POST',PAID_SCRIPT_PATH,request,retries=0,timeout=70)
        choice=(response.get('choices') or [{}])[0]
        content=(choice.get('message') or {}).get('content','')
        doc=_decode(content)
        if not isinstance(doc,dict) or not isinstance(doc.get('script_segments'),list) or not isinstance(doc.get('video_breakdown'),list) or not doc['video_breakdown']:
            raise RuntimeError('模型未返回真实视频拆解或完整分镜，原结果需复核，禁止自动重新付费调用')
        market_check=us_marketing.validate_script(doc,preview['market_context'])
        product=next((p for p in products() if p['id']==preview['product_id']),None)
        fit_errors=validate_product_match(doc,product or {})
        if fit_errors:
            market_check=dict(market_check,ok=False,errors=[*market_check.get('errors',[]),*fit_errors])
        if not market_check['ok']:
            with db.tx() as c:
                c.execute('UPDATE radar_scripts SET status=?,error=?,content_json=?,updated_at=? WHERE id=?',
                          ('needs_review','美国市场硬规则未通过：'+','.join(market_check['errors']),_json({'script':doc,'market_check':market_check}),_now(),sid))
            task_observability.record('script',sid,'美国市场规则需要人工复核','needs_review','；'.join(market_check['errors']))
            return {'id':sid,'status':'needs_review','errors':market_check['errors'],'script':doc,'estimated_cost_usd':estimate,'charged_unknown':True}
        usage=response.get('usage') or {}
        provider_model=str(response.get('model') or '')
        provider_response_id=str(response.get('id') or '')
        model_mismatch=bool(provider_model and provider_model!=model)
        with db.tx() as c:
            c.execute('''UPDATE radar_scripts SET status='completed',title=?,content_json=?,updated_at=? WHERE id=?''',
                      (_clean(doc.get('title_cn'),150),_json({'script':doc,'usage':usage,'visual_evidence':{'frame_times':visual['frames'],'asset_id':visual['asset_id'],'video_sha256':visual['sha256'],'speech_state':visual['speech_state']},'cost_status':'实际账单待核验','request_model':model,'actual_model':provider_model,'response_id':provider_response_id,'model_mismatch':model_mismatch}),_now(),sid))
        task_observability.record('script',sid,'脚本内容已归档','completed')
        return {'id':sid,'status':'completed','script':doc,'estimated_cost_usd':estimate,
                'actual_cost_usd':None,'usage':usage,'actual_model':provider_model,'model_mismatch':model_mismatch}
    except Exception as exc:
        # A definite 4xx rejection is not the same as an ambiguous timeout/5xx.
        from .openlux import ApiError
        definite_rejection=isinstance(exc,ApiError) and exc.status is not None and 400<=exc.status<500 and exc.status not in (408,429)
        next_state='rejected' if definite_rejection else 'submission_unknown'
        with db.tx() as c:
            c.execute('UPDATE radar_scripts SET status=?,error=?,updated_at=? WHERE id=?',
                      (next_state,_clean(str(exc),400),_now(),sid))
        task_observability.record('script',sid,'脚本接口明确拒绝' if definite_rejection else '脚本提交结果未知',next_state,str(exc))
        if definite_rejection:
            raise ValueError(f'接口拒绝脚本请求（HTTP {exc.status}），未自动重试：{str(exc)[:160]}') from exc
        raise RuntimeError(f'脚本任务 {sid} 的结果待核对：{str(exc)[:160]}。禁止重复提交。') from exc


def state(product_id=None):
    stored=items(product_id)
    tik=[x for x in stored if _is_tiktok_url(x.get('url'))]
    return {'products':products(),'options':options(), 'capabilities':{
        'source_focus':'Bright Data 手动采集 TikTok 视频；人工决定是否拆解',
        'tiktok_api':'Bright Data 已集成手动触发；媒体链接有效性及美国地区仍须逐条核实',
        'automatic_discovery':False,'archived_tiktok_count':len(tik),
        'paid_video_generation':False,'automatic_paid_gpt':False,
        'ranking':'近7天优先；必要时补第8—14天；不能把人工导入数据伪装成已核实爆款',
        'video_models_in_test':['Vidu Q3 Turbo','MiniMax H3','Seedance 2.5'],
        'model_testing_status':'选择模型仅保存测试意向；实际路由、接口参数和费用还需核实'},
        'items':tik,'runs':recent_runs(),'scripts':scripts(product_id),
        'scan_window':scan_window_state(product_id) if product_id else None}


_scheduler_stop=threading.Event()

def scheduler_loop(stop_event=None):
    stopper=stop_event or _scheduler_stop
    while not stopper.is_set():
        try:
            cfg=options(); now=dt.datetime.now(_BJ)
            if cfg['auto_scan'] and now.hour>=cfg['scan_hour_bj']:
                for product in products():
                    if product['active'] and product['keywords'] and not product['is_draft']:
                        scan(product['id'],origin='scheduled')
        except Exception:
            import logging
            logging.getLogger('seedance-studio').exception('hot radar scheduler failed')
        stopper.wait(60)
