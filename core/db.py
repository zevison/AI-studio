from __future__ import annotations
import json, sqlite3, threading, time, uuid
from contextlib import contextmanager
from .config import DB_PATH

_lock = threading.RLock()
CURRENT_SCHEMA_VERSION = 390

SCHEMA = [
'''CREATE TABLE IF NOT EXISTS app_meta(key TEXT PRIMARY KEY, value TEXT, updated_at REAL)''',
'''CREATE TABLE IF NOT EXISTS assets(
 id TEXT PRIMARY KEY, original_name TEXT, stored_name TEXT, kind TEXT, mime TEXT,
 path TEXT, sha256 TEXT, size_bytes INTEGER, duration REAL, width INTEGER, height INTEGER, fps REAL, has_audio INTEGER,
 created_at REAL
)''',
'''CREATE TABLE IF NOT EXISTS tasks(
 id TEXT PRIMARY KEY, kind TEXT, fingerprint TEXT, status TEXT, provider_task_id TEXT,
 request_json TEXT, response_json TEXT, result_json TEXT, error TEXT,
 created_at REAL, updated_at REAL, query_count INTEGER DEFAULT 0, last_polled_at REAL, last_query_error TEXT
)''',
'''CREATE INDEX IF NOT EXISTS idx_tasks_fp ON tasks(fingerprint, created_at)''',
'''CREATE TABLE IF NOT EXISTS generation_versions(
 version_id TEXT PRIMARY KEY, group_id TEXT, parent_version_id TEXT, kind TEXT,
 prompt TEXT, level TEXT, asset_id TEXT, metadata_json TEXT, confirmed INTEGER DEFAULT 0,
 created_at REAL
)''',
'''CREATE INDEX IF NOT EXISTS idx_versions_group ON generation_versions(group_id, created_at)''',
'''CREATE TABLE IF NOT EXISTS workflows(
 version_id TEXT PRIMARY KEY, group_id TEXT, parent_version_id TEXT,
 reference_asset_id TEXT, product_asset_ids_json TEXT, instruction TEXT,
 workflow_json TEXT, confirmed INTEGER DEFAULT 0, created_at REAL
)''',
'''CREATE INDEX IF NOT EXISTS idx_workflow_group ON workflows(group_id, created_at)''',
'''CREATE TABLE IF NOT EXISTS analysis_cache(
 cache_key TEXT PRIMARY KEY, value_json TEXT, created_at REAL, updated_at REAL, query_count INTEGER DEFAULT 0, last_polled_at REAL, last_query_error TEXT
)''',
'''CREATE TABLE IF NOT EXISTS generation_previews(
 token TEXT PRIMARY KEY, fingerprint TEXT, payload_json TEXT, expires_at REAL,
 consumed_at REAL, created_at REAL
)''',
'''CREATE TABLE IF NOT EXISTS usage_events(
 id TEXT PRIMARY KEY, task_id TEXT, kind TEXT, model TEXT, units REAL,
 estimated_cost_usd REAL, actual_cost_usd REAL, metadata_json TEXT, created_at REAL
)''',
'''CREATE TABLE IF NOT EXISTS user_settings(
 key TEXT PRIMARY KEY, value_json TEXT, updated_at REAL
)''',
'''CREATE TABLE IF NOT EXISTS workspace_drafts(
 workspace TEXT PRIMARY KEY, data_json TEXT, updated_at REAL
)''',
'''CREATE TABLE IF NOT EXISTS quality_reports(
 task_id TEXT PRIMARY KEY, report_json TEXT, created_at REAL, updated_at REAL
)'''
]

def _read_schema_version() -> int:
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0:
        return 0
    try:
        c=sqlite3.connect(DB_PATH)
        try:
            t=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='app_meta'").fetchone()
            if not t:
                return 0
            r=c.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()
            return int(r[0]) if r and str(r[0]).isdigit() else 0
        finally:
            c.close()
    except Exception:
        return 0


def _backup_before_migration(old_version:int):
    if not DB_PATH.exists() or DB_PATH.stat().st_size == 0 or old_version >= CURRENT_SCHEMA_VERSION:
        return None
    backup_dir=DB_PATH.parent/'backups'
    backup_dir.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime('%Y%m%d_%H%M%S')
    dst=backup_dir/f'studio_pre_schema_{CURRENT_SCHEMA_VERSION}_{stamp}.db'
    src=sqlite3.connect(DB_PATH)
    out=sqlite3.connect(dst)
    try:
        src.backup(out)
    finally:
        out.close(); src.close()
    return dst


def connect():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA foreign_keys=ON')
    return c


def init_db():
    old_version=_read_schema_version()
    _backup_before_migration(old_version)
    with _lock, connect() as c:
        deferred_indexes=[]
        for sql in SCHEMA:
            try:
                c.execute(sql)
            except sqlite3.OperationalError:
                if sql.lstrip().upper().startswith('CREATE INDEX'):
                    deferred_indexes.append(sql)
                else:
                    raise
        # Non-destructive migration for older V2.3.x databases: add missing columns only.
        # Never DROP / DELETE / recreate user history tables here.
        expected={
            'app_meta':{'value':'TEXT','updated_at':'REAL'},
            'assets':{'original_name':'TEXT','stored_name':'TEXT','kind':'TEXT','mime':'TEXT','path':'TEXT','sha256':'TEXT','size_bytes':'INTEGER','duration':'REAL','width':'INTEGER','height':'INTEGER','fps':'REAL','has_audio':'INTEGER','created_at':'REAL'},
            'tasks':{'kind':'TEXT','fingerprint':'TEXT','status':'TEXT','provider_task_id':'TEXT','request_json':'TEXT','response_json':'TEXT','result_json':'TEXT','error':'TEXT','created_at':'REAL','updated_at':'REAL','query_count':'INTEGER DEFAULT 0','last_polled_at':'REAL','last_query_error':'TEXT'},
            'generation_versions':{'group_id':'TEXT','parent_version_id':'TEXT','kind':'TEXT','prompt':'TEXT','level':'TEXT','asset_id':'TEXT','metadata_json':'TEXT','confirmed':'INTEGER DEFAULT 0','created_at':'REAL'},
            'workflows':{'group_id':'TEXT','parent_version_id':'TEXT','reference_asset_id':'TEXT','product_asset_ids_json':'TEXT','instruction':'TEXT','workflow_json':'TEXT','confirmed':'INTEGER DEFAULT 0','created_at':'REAL'},
            'analysis_cache':{'value_json':'TEXT','created_at':'REAL','updated_at':'REAL'},
            'generation_previews':{'fingerprint':'TEXT','payload_json':'TEXT','expires_at':'REAL','consumed_at':'REAL','created_at':'REAL'},
            'usage_events':{'task_id':'TEXT','kind':'TEXT','model':'TEXT','units':'REAL','estimated_cost_usd':'REAL','actual_cost_usd':'REAL','metadata_json':'TEXT','created_at':'REAL'},
            'user_settings':{'value_json':'TEXT','updated_at':'REAL'},
            'workspace_drafts':{'data_json':'TEXT','updated_at':'REAL'},
            'quality_reports':{'report_json':'TEXT','created_at':'REAL','updated_at':'REAL'}
        }
        for table,cols in expected.items():
            have={r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()}
            for col,decl in cols.items():
                if col not in have:
                    c.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')
        for sql in deferred_indexes:
            c.execute(sql)
        # V2.5.9 raises the product-level video concurrency standard from the old
        # default of 3 to 10. Migrate only the legacy value 3 once; users may
        # lower the value again after this schema upgrade if they prefer.
        if old_version < 259:
            try:
                r=c.execute("SELECT value_json FROM user_settings WHERE key='max_concurrent_video_tasks'").fetchone()
                if r and json.loads(r[0]) == 3:
                    c.execute("UPDATE user_settings SET value_json=?,updated_at=? WHERE key='max_concurrent_video_tasks'",(json.dumps(10),now()))
            except Exception:
                pass
        c.execute("""INSERT INTO app_meta(key,value,updated_at) VALUES('schema_version',?,?)
                     ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
                  (str(CURRENT_SCHEMA_VERSION), now()))
        c.commit()

@contextmanager
def tx():
    with _lock:
        c = connect()
        try:
            yield c
            c.commit()
        except Exception:
            c.rollback(); raise
        finally:
            c.close()


def one(sql, params=()):
    with connect() as c:
        r = c.execute(sql, params).fetchone()
        return dict(r) if r else None


def all_(sql, params=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, params).fetchall()]


def execute(sql, params=()):
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def now(): return time.time()
def new_id(prefix): return f'{prefix}_{uuid.uuid4().hex[:20]}'
def dumps(v): return json.dumps(v, ensure_ascii=False, separators=(',', ':'))
def loads(v, default=None):
    if not v: return default
    try: return json.loads(v)
    except Exception: return default


def cache_get(key):
    r = one('SELECT value_json FROM analysis_cache WHERE cache_key=?', (key,))
    return loads(r['value_json']) if r else None


def cache_set(key, value):
    t = now()
    execute('''INSERT INTO analysis_cache(cache_key,value_json,created_at,updated_at)
               VALUES(?,?,?,?) ON CONFLICT(cache_key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at''',
            (key, dumps(value), t, t))


def task_create(kind, fingerprint, request):
    task_id = new_id('task')
    t = now()
    execute('INSERT INTO tasks(id,kind,fingerprint,status,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
            (task_id, kind, fingerprint, 'pending', dumps(request), t, t))
    return task_id



def claim_video_submission(preview_token, dedupe_seconds, max_active, expected_mode=None):
    """Atomically claim one paid video submission.

    All duplicate detection, preview consumption, capacity enforcement and local
    task creation happen inside one BEGIN IMMEDIATE transaction.
    Only state='claimed' is allowed to call the paid OpenLux create endpoint.
    """
    now_ts = now()
    dedupe_seconds = max(0, int(dedupe_seconds or 0))
    max_active = max(1, int(max_active or 1))
    active_states = ('pending','running','result_pending','submission_unknown')
    placeholders = ','.join('?' for _ in active_states)

    with _lock:
        c = connect()
        try:
            c.execute('BEGIN IMMEDIATE')

            row = c.execute(
                'SELECT * FROM generation_previews WHERE token=?',
                (preview_token,)
            ).fetchone()
            if not row:
                c.rollback()
                return {'state':'missing'}

            pre = dict(row)
            fp = pre.get('fingerprint')
            payload = loads(pre.get('payload_json'), {}) or {}

            if expected_mode is not None and payload.get('mode') != expected_mode:
                c.rollback()
                return {
                    'state':'mode_mismatch',
                    'preview':pre,
                    'payload':payload,
                    'fingerprint':fp,
                }

            # Reuse an already active equivalent task.
            existing = c.execute(
                f"SELECT * FROM tasks WHERE fingerprint=? AND kind='video' "
                f"AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
                (fp, *active_states)
            ).fetchone()
            if existing:
                if pre.get('consumed_at') is None:
                    c.execute(
                        'UPDATE generation_previews SET consumed_at=? '
                        'WHERE token=? AND consumed_at IS NULL',
                        (now_ts, preview_token)
                    )
                c.commit()
                return {
                    'state':'reused',
                    'task':dict(existing),
                    'preview':pre,
                    'payload':payload,
                    'fingerprint':fp,
                    'reason':'active_same_fingerprint',
                }

            # Reuse a very recent equivalent task as the dedupe window requires.
            if dedupe_seconds > 0:
                recent = c.execute(
                    "SELECT * FROM tasks WHERE fingerprint=? AND kind='video' "
                    "AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                    (fp, now_ts - dedupe_seconds)
                ).fetchone()
                if recent:
                    if pre.get('consumed_at') is None:
                        c.execute(
                            'UPDATE generation_previews SET consumed_at=? '
                            'WHERE token=? AND consumed_at IS NULL',
                            (now_ts, preview_token)
                        )
                    c.commit()
                    return {
                        'state':'reused',
                        'task':dict(recent),
                        'preview':pre,
                        'payload':payload,
                        'fingerprint':fp,
                        'reason':'recent_same_fingerprint',
                    }

            if pre.get('consumed_at') is not None:
                c.rollback()
                return {
                    'state':'consumed',
                    'preview':pre,
                    'payload':payload,
                    'fingerprint':fp,
                }

            if float(pre.get('expires_at') or 0) < now_ts:
                c.rollback()
                return {
                    'state':'expired',
                    'preview':pre,
                    'payload':payload,
                    'fingerprint':fp,
                }

            # Enforce global video capacity in the same transaction, eliminating
            # the old 9/10 + two concurrent submits => 11/10 race.
            active_count = c.execute(
                "SELECT COUNT(*) FROM tasks WHERE kind='video' "
                "AND (status IN ('pending','running','result_pending') "
                "OR (status='submission_unknown' AND provider_task_id IS NOT NULL))",
                ()
            ).fetchone()[0]

            if int(active_count or 0) >= max_active:
                c.rollback()
                return {
                    'state':'capacity',
                    'active_count':int(active_count or 0),
                    'preview':pre,
                    'payload':payload,
                    'fingerprint':fp,
                }

            # Compare-and-set preview ownership.
            cur = c.execute(
                'UPDATE generation_previews SET consumed_at=? '
                'WHERE token=? AND consumed_at IS NULL AND expires_at>=?',
                (now_ts, preview_token, now_ts)
            )
            if cur.rowcount != 1:
                # Never continue to a paid POST without exclusive ownership.
                existing = c.execute(
                    f"SELECT * FROM tasks WHERE fingerprint=? AND kind='video' "
                    f"AND status IN ({placeholders}) ORDER BY created_at DESC LIMIT 1",
                    (fp, *active_states)
                ).fetchone()
                if existing:
                    c.commit()
                    return {
                        'state':'reused',
                        'task':dict(existing),
                        'preview':pre,
                        'payload':payload,
                        'fingerprint':fp,
                        'reason':'lost_claim_reused_winner',
                    }
                c.rollback()
                return {
                    'state':'consumed',
                    'preview':pre,
                    'payload':payload,
                    'fingerprint':fp,
                }

            # Create the task before releasing the SQLite write lock.
            # Every concurrent caller that wakes up now will see this task.
            task_id = new_id('task')
            c.execute(
                'INSERT INTO tasks('
                'id,kind,fingerprint,status,request_json,created_at,updated_at'
                ') VALUES(?,?,?,?,?,?,?)',
                (task_id, 'video', fp, 'pending', dumps(payload), now_ts, now_ts)
            )
            c.commit()

            task = one('SELECT * FROM tasks WHERE id=?', (task_id,))
            return {
                'state':'claimed',
                'task_id':task_id,
                'task':task,
                'preview':pre,
                'payload':payload,
                'fingerprint':fp,
                'active_count_before':int(active_count or 0),
            }
        except Exception:
            try:
                c.rollback()
            except Exception:
                pass
            raise
        finally:
            c.close()


def task_update(task_id, **fields):
    if not fields: return
    fields['updated_at'] = now()
    cols=[]; vals=[]
    for k,v in fields.items():
        if k in {'request_json','response_json','result_json'} and not isinstance(v,str): v=dumps(v)
        cols.append(f'{k}=?'); vals.append(v)
    vals.append(task_id)
    execute(f"UPDATE tasks SET {','.join(cols)} WHERE id=?", tuple(vals))


def task_recent_by_fp(fp, seconds=None):
    q='SELECT * FROM tasks WHERE fingerprint=?'
    p=[fp]
    if seconds is not None:
        q+=' AND created_at>=?'; p.append(now()-seconds)
    q+=' ORDER BY created_at DESC LIMIT 1'
    return one(q, tuple(p))


def usage_add(task_id, kind, model, units=1, estimated=None, actual=None, metadata=None):
    execute('INSERT INTO usage_events VALUES(?,?,?,?,?,?,?,?,?)',
            (new_id('usage'), task_id, kind, model, units, estimated, actual, dumps(metadata or {}), now()))

init_db()
