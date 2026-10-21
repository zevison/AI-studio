"""Durable local admission ledger. A lost response is never permission to retry."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib,json,os,sqlite3,time,uuid
from pathlib import Path
from . import config

CHARGED={
 '/api/image/generate','/api/video/direct-generate','/api/video/generate',
 '/api/workflow/analyze','/api/workflow/edit','/api/assistant/chat','/api/gpt-v41/chat/stream',
 '/api/hypit/author','/api/hypit/build','/api/hypit/build-paid',
 '/api/bright-tiktok/submit','/api/product-memory/analyze/generate',
 '/api/hot-radar/video/review','/api/hot-radar/scripts/generate',
 '/api/creative-production/plan/generate','/api/creative-production/first-cut/generate'}
BUSY=('pending','running','result_pending','authoring','building','validating','exporting','submitting','in_flight','calling','submitted')
UNCERTAIN=('submission_unknown','paid_result_unknown','unknown')

class Refused(ValueError):pass

def control_path():return Path(os.getenv('STUDIO_CONTROL_DB',str(config.DATA_ROOT/'team-control.sqlite')))

@contextmanager
def connection():
    path=control_path();path.parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(path,timeout=15);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA busy_timeout=15000')
        c.execute('''CREATE TABLE IF NOT EXISTS admissions(
          id TEXT PRIMARY KEY,workspace TEXT NOT NULL,route TEXT NOT NULL,digest TEXT NOT NULL,
          client_key TEXT NOT NULL,state TEXT NOT NULL,pid INTEGER NOT NULL,created REAL NOT NULL,
          updated REAL NOT NULL,status INTEGER,response BLOB,note TEXT)''')
        c.execute('CREATE INDEX IF NOT EXISTS admissions_fingerprint ON admissions(workspace,digest,created)')
        c.execute('''CREATE TABLE IF NOT EXISTS update_request_writes(
          id TEXT PRIMARY KEY,workspace TEXT NOT NULL,pid INTEGER NOT NULL,
          status TEXT NOT NULL,updated REAL NOT NULL)''')
        c.commit();yield c
    finally:c.close()

def workspace():return getattr(config,'WORKSPACE_ID','owner')

def active_in_database(path,include_unknown=True):
    if not Path(path).exists():return 0
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        total=0;states=BUSY+(UNCERTAIN if include_unknown else ())
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            q='"'+table.replace('"','""')+'"'
            cols={r[1] for r in c.execute('PRAGMA table_info('+q+')')}
            column=next((x for x in ('status','state','phase') if x in cols),None)
            if column:total+=c.execute('SELECT COUNT(*) FROM '+q+' WHERE lower("'+column+'") IN ('+','.join('?' for _ in states)+')',states).fetchone()[0]
        return total
    finally:c.close()

def busy(include_unknown=True):
    with connection() as c:
        pending=c.execute("SELECT COUNT(*) FROM admissions WHERE workspace=? AND state IN ('pending','unknown')",(workspace(),)).fetchone()[0]
    return bool(pending or active_in_database(config.DB_PATH,include_unknown))

def recover():
    # Called only by a newly bound backend, after it owns its workspace port.
    with connection() as c:
        c.execute("UPDATE update_request_writes SET status='interrupted',updated=? WHERE workspace=? AND pid<>? AND status='in_flight'",
                  (time.time(),workspace(),os.getpid()))
        c.execute("UPDATE admissions SET state='unknown',updated=?,note='后台重启，原请求结果待核对；禁止自动重发' WHERE workspace=? AND state='pending' AND pid<>?",(time.time(),workspace(),os.getpid()));c.commit()

def claim(route,payload,client_key=''):
    if not isinstance(payload,dict):raise Refused('生成参数必须是 JSON 对象')
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    digest=hashlib.sha256((route+'\0'+raw).encode()).hexdigest()
    if len(client_key)>128:raise Refused('提交标识过长')
    now=time.time()
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        from .update_service import assert_admission_open
        assert_admission_open(config.USER_HOME,connection=c)
        row=c.execute('''SELECT * FROM admissions WHERE workspace=? AND
            ((client_key<>'' AND client_key=?) OR
            (digest=? AND (state IN ('pending','unknown') OR (?='' AND created>?))))
            AND state<>'acknowledged' ORDER BY created DESC LIMIT 1''',
            (workspace(),client_key,digest,client_key,now-600)).fetchone()
        if row:
            if row['digest']!=digest:raise Refused('同一提交标识不能用于不同参数')
            if row['state']=='done' and row['response'] is not None:
                return {'replay':True,'id':row['id'],'status':row['status'],'body':bytes(row['response'])}
            raise Refused('原请求正在处理或结果待核对，请查看任务记录；未重复提交。记录：'+row['id'])
        pending=c.execute("SELECT COUNT(*) FROM admissions WHERE state='pending'").fetchone()[0]
        per_user=c.execute("SELECT COUNT(*) FROM admissions WHERE workspace=? AND state='pending'",(workspace(),)).fetchone()[0]
        roots=[config.DATA_ROOT/'studio.db',*(config.DATA_ROOT/'workspaces').glob('*/studio.db')]
        jobs=sum(active_in_database(p,False) for p in roots if p.exists())
        own=active_in_database(config.DB_PATH,False)
        if per_user or own>=int(os.getenv('STUDIO_USER_CONCURRENCY','2')):
            raise Refused('当前空间正在处理任务，请等待后再提交；未调用生成接口。')
        if pending+jobs>=int(os.getenv('STUDIO_GLOBAL_CONCURRENCY','4')):
            raise Refused('主机已达到任务并发上限，请稍后提交；未调用生成接口。')
        item='op_'+uuid.uuid4().hex
        c.execute('INSERT INTO admissions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
            (item,workspace(),route,digest,client_key,'pending',os.getpid(),now,now,None,None,None))
        c.commit();return {'id':item,'replay':False}


@contextmanager
def mutation_scope():
    """Serialize upgrade admission with even non-paid writes, without a long DB lock."""
    identifier='write_'+uuid.uuid4().hex
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        from .update_service import assert_admission_open
        assert_admission_open(config.USER_HOME,connection=c)
        c.execute('INSERT INTO update_request_writes VALUES(?,?,?,?,?)',
                  (identifier,workspace(),os.getpid(),'in_flight',time.time()))
        c.commit()
    try:yield
    finally:
        with connection() as c:
            c.execute("DELETE FROM update_request_writes WHERE id=? AND pid=?",(identifier,os.getpid()))
            c.commit()

def finish(item,status,body):
    uncertain=status is None or status>=500
    # Persist before writing bytes to the browser, so disconnect cannot replay a paid call.
    if body is not None and len(body)>2*1024*1024:body=None
    with connection() as c:
        c.execute("UPDATE admissions SET state=?,status=?,response=?,updated=? WHERE id=? AND workspace=? AND state='pending'",
            ('unknown' if uncertain else 'done',status,None if uncertain else body,time.time(),item,workspace()));c.commit()

def recent():
    with connection() as c:
        return [dict(r) for r in c.execute('SELECT id,route,state,created,updated,note FROM admissions WHERE workspace=? ORDER BY created DESC LIMIT 30',(workspace(),))]

def acknowledge(item,confirmed,note):
    if confirmed is not True or not isinstance(note,str) or len(note.strip())<4:raise Refused('请先核对供应商账单和任务，并填写核对结果')
    with connection() as c:
        c.execute('BEGIN IMMEDIATE')
        n=c.execute("UPDATE admissions SET state='acknowledged',updated=?,note=? WHERE id=? AND workspace=? AND state='unknown'",(time.time(),note[:500],item,workspace())).rowcount
        if n!=1:raise Refused('只能核对当前空间中结果未知的请求，运行中请求不能解除')
        c.commit()
    return {'acknowledged':True,'resubmitted':False}
