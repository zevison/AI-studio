"""Workspace-confined storage, reversible moves and conservative retention."""
from __future__ import annotations
from contextlib import contextmanager
import json,os,shutil,sqlite3,threading,time,uuid
from pathlib import Path
from . import config,team_safety,upload_lifecycle

GIB=1024**3
MEDIA=('assets','outputs','product_knowledge')
_lock=threading.RLock()
upload_gate=threading.BoundedSemaphore(1)
_usage_cache=(0,None)

@contextmanager
def database():
    c=sqlite3.connect(config.DATA_DIR/'storage.sqlite',timeout=15);c.row_factory=sqlite3.Row
    try:
        c.execute('''CREATE TABLE IF NOT EXISTS trash(id TEXT PRIMARY KEY,original TEXT NOT NULL,
            size INTEGER NOT NULL,created REAL NOT NULL,state TEXT NOT NULL,asset_ids TEXT NOT NULL,
            previously_removed TEXT NOT NULL,modified_ns INTEGER NOT NULL)''');c.commit();yield c
    finally:c.close()

def no_links(path):
    base=config.DATA_DIR.resolve()
    # Inspect the lexical path before resolve; resolve alone would hide junctions.
    path=Path(path).absolute()
    if not path.is_relative_to(base):raise PermissionError('文件不在当前用户空间内')
    for p in (path,*path.parents):
        if p==base.parent:break
        if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise PermissionError('不处理链接或目录联接')
    if not path.resolve().is_relative_to(base):raise PermissionError('文件路径越界')
    return path

def media_path(relative,exists=True):
    if not isinstance(relative,str) or len(relative)>800 or '\\' in relative:raise ValueError('无效文件路径')
    rel=Path(relative)
    if rel.is_absolute() or not rel.parts or rel.parts[0] not in MEDIA or any(p in ('.','..') or ':' in p for p in rel.parts):raise PermissionError('只能管理当前空间的素材和成片')
    p=no_links(config.DATA_DIR/rel)
    if exists and (not p.is_file() or p.stat().st_nlink>1):raise ValueError('文件不存在或有多个硬链接')
    return p

def walk_files(base):
    count=0
    for directory,dirs,files in os.walk(base,followlinks=False):
        parent=Path(directory)
        dirs[:]=[d for d in dirs if not (parent/d).is_symlink() and not (hasattr(parent/d,'is_junction') and (parent/d).is_junction())]
        if parent==config.DATA_DIR:dirs[:]=[d for d in dirs if d!='workspaces']
        for name in files:
            count+=1
            if count>100000:raise ValueError('文件数量过多，请管理员分批整理后重试')
            p=parent/name
            if p.is_symlink():continue
            try:
                s=p.stat()
                if p.is_file():yield p,s
            except FileNotFoundError:continue

def usage(force=False):
    global _usage_cache
    with _lock:
        if not force and time.monotonic()-_usage_cache[0]<10 and _usage_cache[1] is not None:return dict(_usage_cache[1])
        totals={x:0 for x in (*MEDIA,'cache','.trash','other')}
        for p,s in walk_files(config.DATA_DIR):
            rel=p.relative_to(config.DATA_DIR);cat=rel.parts[0]
            # The administrator's old data directory contains separate user spaces.
            if cat=='workspaces':continue
            totals[cat if cat in totals else 'other']+=s.st_size
        _usage_cache=(time.monotonic(),totals);return dict(totals)

def invalidate():
    global _usage_cache
    _usage_cache=(0,None)

def require_capacity(incoming=0):
    incoming=max(0,int(incoming));u=usage();free=shutil.disk_usage(config.DATA_DIR).free
    reserve=int(os.getenv('STUDIO_DISK_RESERVE_GB','5'))*GIB
    quota=int(os.getenv('STUDIO_WORKSPACE_QUOTA_GB','20'))*GIB
    if free-incoming<reserve:raise ValueError('主机剩余空间不足：已暂停新增上传和生成，请先清理文件。')
    if sum(u.values())+incoming>quota:raise ValueError('当前用户空间已达到容量上限，请先清理或联系管理员调整。')

def status():
    with database() as c:rows=[dict(r) for r in c.execute("SELECT id,original,size,created,state FROM trash WHERE state IN ('ready','moving','restoring','error') ORDER BY created DESC LIMIT 300")]
    conn=sqlite3.connect(config.DB_PATH,timeout=10);conn.row_factory=sqlite3.Row
    try:tasks=[dict(r) for r in conn.execute('SELECT id,kind,status,created_at,updated_at FROM tasks ORDER BY created_at DESC LIMIT 50')]
    finally:conn.close()
    return {'workspace':team_safety.workspace(),'usage':usage(),'disk_free':shutil.disk_usage(config.DATA_DIR).free,
      'quota_bytes':int(os.getenv('STUDIO_WORKSPACE_QUOTA_GB','20'))*GIB,
      'reserve_bytes':int(os.getenv('STUDIO_DISK_RESERVE_GB','5'))*GIB,
      'trash_days':7,'cache_days':3,'trash':rows,'operations':team_safety.recent(),'tasks':tasks,
      'retention':'上传素材和成片不自动删除；回收站保留7天。只有可重建的过期缓存会自动清理。'}

def files(category='outputs',page=0):
    if category not in MEDIA:raise ValueError('无效目录')
    page=max(0,min(int(page),10000));rows=[]
    for p,s in walk_files(config.DATA_DIR/category):
        rows.append({'path':p.relative_to(config.DATA_DIR).as_posix(),'name':p.name,'bytes':s.st_size,'modified':s.st_mtime,
            'url':('/outputs/' if category=='outputs' else '/public/assets/')+__import__('urllib.parse',fromlist=['quote']).quote(p.name) if category!='product_knowledge' and p.parent==config.DATA_DIR/category else None})
    rows.sort(key=lambda r:r['modified'],reverse=True)
    return {'rows':rows[page*100:(page+1)*100],'total':len(rows),'page':page}

def _idle(control):
    if control.execute("SELECT 1 FROM admissions WHERE workspace=? AND state IN ('pending','unknown') LIMIT 1",(team_safety.workspace(),)).fetchone() or team_safety.active_in_database(config.DB_PATH):
        raise ValueError('当前空间有运行中或结果待核对的任务，暂不能移动或清理文件。')

def _asset_ids(path):
    c=sqlite3.connect(config.DB_PATH,timeout=10)
    try:
        return [r[0] for r in c.execute('SELECT id FROM assets WHERE path=? OR stored_name=?',(str(path),path.name))]
    finally:c.close()

def trash(relative,confirmed=False):
    if confirmed is not True:raise ValueError('请确认移入回收站后，相关历史记录暂时不能播放或下载')
    with _lock,team_safety.connection() as control:
        control.execute('BEGIN IMMEDIATE');_idle(control)
        p=media_path(relative);s=p.stat();item='trash_'+uuid.uuid4().hex
        if Path(relative).parts[0]=='product_knowledge':
            conn=sqlite3.connect(config.DB_PATH,timeout=10)
            try:
                sources=conn.execute('SELECT id FROM product_sources WHERE stored_path=?',(str(p),)).fetchall()
            finally:conn.close()
            if any(not upload_lifecycle.removed('source',r[0]) for r in sources):
                raise ValueError('请先在商品知识中心取消引用该资料，再移动文件，避免旧资料继续参与生成。')
        dest=no_links(config.DATA_DIR/'.trash'/item);dest.parent.mkdir(parents=True,exist_ok=True)
        ids=_asset_ids(p);previous=[i for i in ids if upload_lifecycle.removed('asset',i)]
        # Hide assets before move; a failed move restores only our own soft removals.
        with database() as c:
            c.execute('INSERT INTO trash VALUES(?,?,?,?,?,?,?,?)',(item,relative,s.st_size,time.time(),'moving',json.dumps(ids),json.dumps(previous),s.st_mtime_ns));c.commit()
            try:
                for i in ids:upload_lifecycle.remove('asset',i)
                os.replace(p,dest)
                c.execute("UPDATE trash SET state='ready' WHERE id=?",(item,));c.commit()
            except Exception:
                # If rename succeeded, preserve the journal for recovery; never guess or unlink.
                if not dest.exists():
                    for i in set(ids)-set(previous):upload_lifecycle.restore('asset',i)
                    c.execute("UPDATE trash SET state='error' WHERE id=?",(item,));c.commit()
                raise
        invalidate();return {'id':item,'moved':True,'physical_space_freed':False,'message':'已移入回收站，7天内可以恢复；清空后才释放磁盘空间。'}

def restore(item):
    with _lock,team_safety.connection() as control:
        control.execute('BEGIN IMMEDIATE');_idle(control)
        with database() as c:
            row=c.execute("SELECT * FROM trash WHERE id=? AND state IN ('ready','moving','restoring')",(item,)).fetchone()
            if not row:raise ValueError('回收记录不存在')
            src=no_links(config.DATA_DIR/'.trash'/row['id']);target=media_path(row['original'],False)
            if target.exists():raise ValueError('原位置已有文件，未覆盖任何文件')
            if not src.is_file() or src.stat().st_size!=row['size']:raise ValueError('回收文件缺失或已改变，请管理员检查')
            c.execute("UPDATE trash SET state='restoring' WHERE id=?",(item,));c.commit()
            target.parent.mkdir(parents=True,exist_ok=True);os.replace(src,target)
            for i in set(json.loads(row['asset_ids']))-set(json.loads(row['previously_removed'])):upload_lifecycle.restore('asset',i)
            c.execute("UPDATE trash SET state='restored' WHERE id=?",(item,));c.commit()
        invalidate();return {'restored':True}

def purge(item,confirmed=False,expired_only=False):
    if confirmed is not True:raise ValueError('永久删除需再次确认，删除后无法恢复')
    with _lock,team_safety.connection() as control:
        control.execute('BEGIN IMMEDIATE');_idle(control)
        with database() as c:
            row=c.execute("SELECT * FROM trash WHERE id=? AND state='ready'",(item,)).fetchone()
            if not row:raise ValueError('回收记录不存在或尚未完成')
            if expired_only and row['created']>time.time()-7*86400:return {'purged':False}
            p=no_links(config.DATA_DIR/'.trash'/row['id'])
            if p.exists():
                if not p.is_file() or p.stat().st_size!=row['size'] or p.stat().st_nlink>1:raise ValueError('文件状态已改变，停止清理')
                p.unlink()
            c.execute("UPDATE trash SET state='purged' WHERE id=?",(item,));c.commit()
        invalidate();return {'purged':True,'freed_bytes':row['size']}

def reconcile_moves():
    # A crashed move is reconciled from both paths; uncertain cases stay visible.
    with database() as c:
        for row in c.execute("SELECT * FROM trash WHERE state IN ('moving','restoring')").fetchall():
            a=media_path(row['original'],False);b=no_links(config.DATA_DIR/'.trash'/row['id'])
            if b.is_file() and not a.exists():state='ready'
            elif a.is_file() and not b.exists():
                for i in set(json.loads(row['asset_ids']))-set(json.loads(row['previously_removed'])):upload_lifecycle.restore('asset',i)
                state='restored'
            else:state='error'
            c.execute('UPDATE trash SET state=? WHERE id=?',(state,row['id']))
        c.commit()

def maintenance():
    removed=0
    # No media expiry: only files already explicitly placed in the recycle bin.
    with database() as c:ids=[r[0] for r in c.execute("SELECT id FROM trash WHERE state='ready' AND created<?",(time.time()-7*86400,))]
    for item in ids:
        try:removed+=int(purge(item,True,True).get('purged',False))
        except (ValueError,PermissionError,sqlite3.Error):break
    with _lock,team_safety.connection() as control:
        control.execute('BEGIN IMMEDIATE');_idle(control)
        for p,s in walk_files(config.CACHE_DIR):
            rel=p.relative_to(config.CACHE_DIR)
            rebuildable=rel.parts[0] in ('analysis_frames','smart_analysis','pose-install-check') or p.suffix in ('.tmp','.part')
            if rebuildable and s.st_mtime<time.time()-3*86400 and s.st_nlink==1:
                no_links(p).unlink();removed+=1
        invalidate()
    return {'cleaned_files':removed,'media_deleted':False}

def worker(stop):
    while not stop.wait(1800):
        try:maintenance()
        except Exception:
            __import__('logging').getLogger(__name__).exception('Storage maintenance postponed safely')
