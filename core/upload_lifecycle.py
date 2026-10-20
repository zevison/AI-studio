"""Reversible upload removal; never unlink files referenced by historical work."""
from __future__ import annotations
from contextlib import closing
import json
import sqlite3
import threading
import time
from . import config

_lock=threading.RLock()
_ready=False
ACTIVE=('pending','running','submitting','submitted','in_flight','calling','result_pending','submission_unknown',
        'authoring','building','validating','exporting','paid_result_unknown')

def connect():
    c=sqlite3.connect(config.DB_PATH,timeout=10);c.row_factory=sqlite3.Row;return c

def quote(name):return '"'+name.replace('"','""')+'"'

def ensure():
    global _ready
    if _ready:return
    with _lock:
        if _ready:return
        with closing(connect()) as c:
            c.execute('CREATE TABLE IF NOT EXISTS removed_uploads(kind TEXT NOT NULL,item_id TEXT NOT NULL,context TEXT NOT NULL DEFAULT \'\',removed_at REAL NOT NULL,PRIMARY KEY(kind,item_id))')
            # Atomic protection against a stale preview racing with removal.
            tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'tasks' in tables:
                c.execute('''CREATE TRIGGER IF NOT EXISTS prevent_removed_upload_task BEFORE INSERT ON tasks
                    WHEN EXISTS(SELECT 1 FROM removed_uploads WHERE kind='asset'
                        AND instr(COALESCE(NEW.request_json,''),'"'||item_id||'"')>0)
                    BEGIN SELECT RAISE(ABORT,'参考素材已删除，请重新选择并预览'); END''')
            c.commit()
        _ready=True

def removed(kind,item_id):
    ensure()
    with closing(connect()) as c:return c.execute('SELECT 1 FROM removed_uploads WHERE kind=? AND item_id=?',(kind,str(item_id))).fetchone() is not None

def filter_rows(rows,kind='asset'):
    ensure()
    with closing(connect()) as c:ids={r[0] for r in c.execute('SELECT item_id FROM removed_uploads WHERE kind=?',(kind,))}
    return [r for r in rows if r.get('id') not in ids]

def validate_request(value):
    ensure()
    ids=set()
    def walk(v):
        if isinstance(v,dict):
            for x in v.values():walk(x)
        elif isinstance(v,(list,tuple)):
            for x in v:walk(x)
        elif isinstance(v,str):ids.add(v)
    walk(value)
    if not ids:return
    with closing(connect()) as c:
        hidden={r[0] for r in c.execute('SELECT item_id FROM removed_uploads')}
        if isinstance(value,dict) and hidden:
            project_id=value.get('project_id') or value.get('id')
            if isinstance(project_id,str) and project_id.startswith('hp_') and c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='hypit_projects'").fetchone():
                project=c.execute('SELECT * FROM hypit_projects WHERE id=?',(project_id,)).fetchone()
                if project and any(json.dumps(item,ensure_ascii=False) in str(field or '') for item in hidden for field in project):
                    raise ValueError('该工程引用了已删除素材，请重新选择素材创建工程；未调用生成接口。')
    if ids & hidden:raise ValueError('所选文件已删除，请刷新素材并重新预览；本次未调用生成接口。')

def memory_stale(context,updated):
    ensure()
    with closing(connect()) as c:
        return bool(c.execute("SELECT 1 FROM removed_uploads WHERE kind='source' AND context=? AND removed_at>? LIMIT 1",(str(context),float(updated or 0))).fetchone())

def remove(kind,item_id,context=''):
    if kind not in ('asset','source'):raise ValueError('不支持的文件类型')
    if not isinstance(item_id,str) or not item_id or len(item_id)>100:raise ValueError('无效文件编号')
    ensure()
    with _lock,closing(connect()) as c:
        try:
            c.execute('BEGIN IMMEDIATE')
            table='assets' if kind=='asset' else 'product_sources'
            record=c.execute('SELECT * FROM '+table+' WHERE id=?',(item_id,)).fetchone()
            if not record:raise ValueError('文件不存在')
            if kind=='source' and record['product_id']!=context:raise ValueError('资料不属于当前商品')
            # Inspect durable in-flight evidence before hiding a referenced upload.
            needle=json.dumps(item_id,ensure_ascii=False)
            tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            for t in tables:
                cols=[r[1] for r in c.execute('PRAGMA table_info('+quote(t)+')')]
                status=next((x for x in ('status','state','phase') if x in cols),None)
                refs=[x for x in cols if x.endswith('_json')]
                if not status or not refs:continue
                sql='SELECT '+','.join(quote(x) for x in refs)+' FROM '+quote(t)+' WHERE lower('+quote(status)+') IN ('+','.join('?' for _ in ACTIVE)+')'
                for row in c.execute(sql,ACTIVE):
                    if any(needle in str(v or '') for v in row):raise ValueError('文件正被运行中或结果待核实的任务引用，暂不能删除。')
            c.execute('INSERT OR IGNORE INTO removed_uploads(kind,item_id,context,removed_at) VALUES(?,?,?,?)',(kind,item_id,str(context),time.time()))
            if 'generation_previews' in tables:
                c.execute('UPDATE generation_previews SET expires_at=0 WHERE instr(payload_json,?)>0',(needle,))
            c.commit()
            return {'removed':True,'id':item_id,'kind':kind,'physical_deleted':False,
                    'message':'已从可选文件中删除，历史记录及原文件保留；请重新预览。'}
        except Exception:c.rollback();raise

def restore(kind,item_id):
    ensure()
    with closing(connect()) as c:
        c.execute('DELETE FROM removed_uploads WHERE kind=? AND item_id=?',(kind,item_id));c.commit()
    return {'restored':True,'id':item_id}
