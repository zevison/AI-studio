"""Snapshot a stopped Studio into a fresh folder; old installation is retained."""
import ast,json,os,shutil,sqlite3,time,urllib.request,uuid
from pathlib import Path

def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())

def database(path,*,fresh=False):
    if not path.exists():return
    if linked(path):raise PermissionError('Database link refused')
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'tasks' in tables and c.execute("SELECT 1 FROM tasks WHERE status IN ('pending','running','result_pending','submission_unknown') LIMIT 1").fetchone():
            raise PermissionError('Running or uncertain tasks must be reconciled before migration')
        if fresh:
            for table in tables & {'assets','tasks','hypit_projects','usage_events','workflows','generation_versions','workspace_drafts'}:
                if c.execute('SELECT 1 FROM "'+table+'" LIMIT 1').fetchone():
                    raise PermissionError('Destination has user data; databases will not be merged or overwritten')
    finally:c.close()

def stopped(root):
    marker=root/'.runtime_port'
    if not marker.is_file():return
    try:port=int(marker.read_text(encoding='ascii').strip())
    except (OSError,ValueError):raise PermissionError('Unverifiable running-instance marker')
    if not 1<=port<=65535:raise PermissionError('Invalid running-instance port')
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/bootstrap-health',timeout=2) as response:doc=json.loads(response.read(16384))
    except OSError:return
    info=doc.get('data',doc)
    if isinstance(info,dict) and info.get('root_dir') and Path(info['root_dir']).resolve()==root:
        raise PermissionError('Close this Studio backend before migration')

def version(root):
    file=root/'core/config.py'
    if not file.is_file() or linked(file):raise ValueError('Not a supported Studio installation')
    for node in ast.parse(file.read_text(encoding='utf8')).body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='APP_VERSION' for t in node.targets):
            value=ast.literal_eval(node.value)
            if value not in {'5.0.0','5.1.1','5.2.0'}:raise ValueError('Unsupported source version')
            return value
    raise ValueError('Source version is unknown')

def migrate(old,destination,*,fail_after=None):
    old_input,new_input=Path(old),Path(destination)
    if linked(old_input) or linked(new_input):raise PermissionError('Installation links refused')
    old,destination=old_input.resolve(strict=True),new_input.resolve(strict=True)
    if old==destination or old.is_relative_to(destination) or destination.is_relative_to(old):
        raise PermissionError('Source and destination must be separate installation folders')
    old_version=version(old);version(destination);stopped(old);stopped(destination)
    source,target=old/'data',destination/'data'
    if not source.is_dir() or linked(source) or linked(target):raise PermissionError('Regular data directory required')
    database(source/'studio.db');database(target/'studio.db',fresh=True)
    files=[];size=0
    for folder,dirs,names in os.walk(source,followlinks=False):
        for name in dirs+names:
            p=Path(folder)/name
            if linked(p) or not p.resolve().is_relative_to(source):raise PermissionError('Linked or escaping data path refused')
        for name in names:
            p=Path(folder)/name
            if p.name in {'studio.db-wal','studio.db-shm'}:continue
            files.append(p);size+=p.stat().st_size
            if len(files)>200000:raise ValueError('Migration file-count bound exceeded')
    if shutil.disk_usage(destination).free<size+1024**3:raise OSError('Insufficient space for independent copy')
    stamp=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    stage=destination/('.migration-stage-'+stamp);stage.mkdir()
    receipt={'format':'seedance.migration@1','state':'staging','source':str(old),'source_version':old_version,
        'destination':str(destination),'staging':stage.name,'copied_files':0,'source_preserved':True}
    log=destination/('migration-'+stamp+'.json');backup=None
    def save():log.write_text(json.dumps(receipt,ensure_ascii=False,indent=2),encoding='utf8')
    save();published=False
    try:
        for path in files:
            if linked(path) or not path.resolve().is_relative_to(source):raise PermissionError('Source changed to a linked path')
            relative=path.relative_to(source);copy=stage/relative;copy.parent.mkdir(parents=True,exist_ok=True)
            if relative==Path('studio.db'):
                incoming=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5);outgoing=sqlite3.connect(copy)
                try:incoming.backup(outgoing)
                finally:outgoing.close();incoming.close()
            else:
                before=path.stat();shutil.copy2(path,copy);after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('Source changed during copy')
            receipt['copied_files']+=1
            if fail_after is not None and receipt['copied_files']>=fail_after:raise OSError('Injected migration interruption')
        database(source/'studio.db');database(stage/'studio.db');stopped(old);stopped(destination)
        env=old/'.env'
        if env.is_file() and not linked(env) and not (destination/'.env').exists():
            with (destination/'.env').open('xb') as out:out.write(env.read_bytes())
        if target.exists():
            backup=destination/('.migration-previous-'+stamp)
            assert backup.parent==destination and target.resolve().parent==destination
            target.rename(backup)
        assert stage.resolve().parent==destination
        stage.rename(target);published=True
        receipt.update(state='complete',destination_previous_data=backup.name if backup else None,
            note='Keep original folder for rollback and legacy absolute project references; no credentials printed')
        save();return receipt
    except Exception as exc:
        if published and target.exists():
            assert target.resolve().parent==destination and not stage.exists()
            target.rename(stage)
        if backup and backup.exists() and not target.exists():backup.rename(target)
        receipt.update(state='failed_preserved',error_type=type(exc).__name__);save();raise

def main():
    import argparse
    parser=argparse.ArgumentParser(description='Stopped Studio migration; original installation retained')
    parser.add_argument('--from',dest='old');args=parser.parse_args()
    old=args.old or input('请先关闭新旧版后台，再输入旧版工作台文件夹路径：').strip().strip('"')
    try:
        receipt=migrate(old,Path(__file__).resolve().parents[1]);print('迁移完成，请保留旧版文件夹用于回退。复制文件数：',receipt['copied_files']);return 0
    except Exception as exc:
        print('迁移未完成，未合并已有用户数据库。原因：',str(exc));return 2

if __name__=='__main__':raise SystemExit(main())
