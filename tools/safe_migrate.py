"""Snapshot a stopped Studio into a fresh folder; old installation is retained."""
import ast,json,os,shutil,sqlite3,sys,time,urllib.request,uuid
from pathlib import Path
if str(Path(__file__).resolve().parent) not in sys.path:sys.path.insert(0,str(Path(__file__).resolve().parent))

BUSY_STATES=('pending','running','result_pending','submission_unknown','in_flight','calling',
             'submitting','submitted','authoring','building','validating','exporting','paid_result_unknown','unknown')

def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())

def database(path,*,fresh=False):
    if not path.exists():return
    if linked(path):raise PermissionError('Database link refused')
    c=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('Database integrity check failed')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in tables:
            if table.startswith('sqlite_'):continue
            quoted='"'+table.replace('"','""')+'"'
            cols={r[1] for r in c.execute('PRAGMA table_info('+quoted+')')}
            status=next((x for x in ('status','state','phase') if x in cols),None)
            if status and c.execute('SELECT 1 FROM '+quoted+' WHERE lower("'+status+'") IN ('+','.join('?' for _ in BUSY_STATES)+') LIMIT 1',BUSY_STATES).fetchone():
                raise PermissionError('Running or uncertain tasks must be reconciled before migration')
            if fresh and table not in {'app_meta','analysis_cache'} and c.execute('SELECT 1 FROM '+quoted+' LIMIT 1').fetchone():
                raise PermissionError('Destination has user data; databases will not be merged or overwritten')
    finally:c.close()

def stopped(root):
    from workspace_install_guard import any_workspace_running
    if any_workspace_running(root):raise PermissionError('Close all Studio workspaces before migration')
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

def rebase_snapshot(path,source_data,destination_data,staging):
    """Rewrite only local data paths whose copied files actually exist."""
    from pathlib import PureWindowsPath, PurePosixPath
    flavor=PureWindowsPath if PureWindowsPath(str(source_data)).drive else PurePosixPath
    source_prefix=flavor(str(source_data));changed=0
    def value(item):
        if isinstance(item,list):return [value(x) for x in item]
        if isinstance(item,dict):return {k:value(v) for k,v in item.items()}
        if not isinstance(item,str):return item
        try:relative=flavor(item).relative_to(source_prefix)
        except ValueError:return item
        if not relative.parts or '..' in relative.parts:return item
        copied=staging.joinpath(*relative.parts)
        if not copied.exists():return item
        return str(destination_data.joinpath(*relative.parts))
    c=sqlite3.connect(path)
    try:
        c.execute('BEGIN IMMEDIATE')
        for (table,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall():
            q='"'+table.replace('"','""')+'"'
            columns=[r[1] for r in c.execute('PRAGMA table_info('+q+')') if 'TEXT' in str(r[2]).upper()]
            if not columns:continue
            selected=','.join('"'+x.replace('"','""')+'"' for x in columns)
            for row in c.execute('SELECT rowid,'+selected+' FROM '+q):
                updates={}
                for column,original in zip(columns,row[1:]):
                    if not isinstance(original,str):continue
                    updated=value(original)
                    if original.lstrip().startswith(('{','[')):
                        try:
                            decoded=json.loads(original);rebased=value(decoded)
                            if rebased!=decoded:updated=json.dumps(rebased,ensure_ascii=False,separators=(',',':'))
                        except (ValueError,RecursionError):pass
                    if updated!=original:updates[column]=updated
                if updates:
                    c.execute('UPDATE '+q+' SET '+','.join('"'+k.replace('"','""')+'"=?' for k in updates)+' WHERE rowid=?',(*updates.values(),row[0]));changed+=1
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'generation_previews' in tables:c.execute('UPDATE generation_previews SET expires_at=0')
        c.commit()
    except Exception:c.rollback();raise
    finally:c.close()
    return changed


def migrate(old,destination,*,fail_after=None,source_data_root=None):
    old_input,new_input=Path(old),Path(destination)
    if linked(old_input) or linked(new_input):raise PermissionError('Installation links refused')
    old,destination=old_input.resolve(strict=True),new_input.resolve(strict=True)
    if old==destination or old.is_relative_to(destination) or destination.is_relative_to(old):
        raise PermissionError('Source and destination must be separate installation folders')
    old_version=version(old);version(destination);stopped(old);stopped(destination)
    source,target=old/'data',destination/'data'
    if not source.is_dir() or linked(source) or linked(target):raise PermissionError('Regular data directory required')
    for base in (target/'assets',target/'outputs'):
        if base.exists() and any(p.is_file() for p in base.rglob('*')):
            raise PermissionError('Destination already contains media; use a fresh installation folder, no files were merged')
    database(source/'studio.db');database(target/'studio.db',fresh=True)
    database(target/'audio_subjects.db',fresh=True)
    files=[];size=0
    for folder,dirs,names in os.walk(source,followlinks=False):
        for name in dirs+names:
            p=Path(folder)/name
            if linked(p) or not p.resolve().is_relative_to(source):raise PermissionError('Linked or escaping data path refused')
        for name in names:
            p=Path(folder)/name
            if p.name.endswith(('-wal','-shm')):continue
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
            if path.suffix.lower() in {'.db','.sqlite','.sqlite3'}:
                from core.sqlite_snapshot import snapshot_database
                snapshot_database(path,copy)
            else:
                before=path.stat();shutil.copy2(path,copy);after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('Source changed during copy')
            receipt['copied_files']+=1
            if fail_after is not None and receipt['copied_files']>=fail_after:raise OSError('Injected migration interruption')
        if (stage/'studio.db').is_file():
            receipt['rebased_rows']=rebase_snapshot(stage/'studio.db',source_data_root or source,target,stage)
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
