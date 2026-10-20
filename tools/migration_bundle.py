from __future__ import annotations
import argparse, json, shutil, sys, tempfile, time, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import backup_manager, config


def choose_zip():
    try:
        import tkinter as tk
        from tkinter import filedialog
        root=tk.Tk();root.withdraw();root.attributes('-topmost',True)
        p=filedialog.askopenfilename(title='选择 Seedance Studio 迁移备份',filetypes=[('Seedance Studio 备份','*.zip'),('ZIP','*.zip')])
        root.destroy();return Path(p) if p else None
    except Exception:return None


def do_export(include_outputs=False):
    r=backup_manager.create_backup(include_outputs);print('[OK] 已创建迁移包：',r['path']);return 0


def extract_backup(src, destination):
    """Bounded, manifest-checked extraction; no file is published into Studio here."""
    import os, re, stat
    from pathlib import PurePosixPath
    with zipfile.ZipFile(src) as archive:
        infos=archive.infolist()
        if len(infos)>200000:raise ValueError('Migration archive has too many entries')
        names=set();size=0
        for item in infos:
            name=item.filename;parts=PurePosixPath(name).parts
            if (not parts or item.orig_filename!=name or name.startswith('/') or '\\' in name or
                any(x in ('..','.') or x.rstrip(' .')!=x or re.search(r'[<>:"|?*\x00-\x1f]',x) or
                    x.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for x in parts)
                or stat.S_ISLNK(item.external_attr>>16) or name.casefold().rstrip('/') in names):
                raise ValueError('Unsafe or duplicate migration entry')
            names.add(name.casefold().rstrip('/'));size+=item.file_size
        if size>shutil.disk_usage(destination).free-1024**3:raise OSError('Insufficient temporary space for migration')
        manifest_info=next((i for i in infos if i.filename=='backup_manifest.json'),None)
        if not manifest_info or manifest_info.file_size>2*1024**2:raise ValueError('Missing or oversized backup manifest')
        manifest=json.loads(archive.read(manifest_info).decode('utf-8-sig'))
        if not isinstance(manifest,dict) or manifest.get('version') not in {'5.0.0','5.1.1','5.2.0'}:
            raise ValueError('Unsupported backup version')
        declared=manifest.get('files')
        actual=[i.filename for i in infos if not i.is_dir() and i.filename!='backup_manifest.json']
        if not isinstance(declared,list) or len(declared)!=len(set(declared)) or set(actual)!=set(declared):
            raise ValueError('Backup manifest differs from archive contents')
        for item in infos:
            if item.is_dir() or item.filename=='backup_manifest.json':continue
            name=item.filename
            if name not in {'.env','data/studio.db','data/audio_subjects.db','data/secrets.json','data/shared_profile.json','data/current_url.txt'} and not name.startswith(('data/assets/','data/outputs/','data/product_knowledge/','data/hypit_projects/','data/hypit_agent_sessions/','data/hypit_token_grants/','data/hypit_video_operations/')):
                raise ValueError('Unexpected file in migration backup')
            path=destination.joinpath(*PurePosixPath(name).parts)
            path.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(item) as incoming,path.open('xb') as outgoing:
                shutil.copyfileobj(incoming,outgoing,1024*1024)
        (destination/'core').mkdir()
        (destination/'core/config.py').write_text('APP_VERSION = '+repr(manifest['version']),encoding='utf8')
        (destination/'data').mkdir(exist_ok=True)
        return manifest


def do_import(src:Path|None):
    from safe_migrate import stopped, database, migrate
    src=src or choose_zip()
    if not src or not src.is_file():print('[INFO] No backup selected.');return 2
    stopped(ROOT)
    database(config.DB_PATH,fresh=True)
    # Reuse exactly the stopped-install migration transaction and rollback path.
    # Existing credentials/configuration are never overwritten or merged.
    with tempfile.TemporaryDirectory(prefix='seedance-restore-') as td:
        stage=Path(td)
        manifest=extract_backup(src,stage)
        if manifest.get('data_root') is not None and not isinstance(manifest['data_root'],str):raise ValueError('Invalid source data root')
        origin=manifest.get('data_root') or (str(Path(manifest['root'])/'data') if isinstance(manifest.get('root'),str) else None)
        receipt=migrate(stage,ROOT,source_data_root=origin)
    print('[OK] Backup restored into a fresh installation; previous folder retained. Files:',receipt['copied_files'])
    return 0


def main():
    ap=argparse.ArgumentParser();ap.add_argument('action',choices=['export','import']);ap.add_argument('--include-outputs',action='store_true');ap.add_argument('--file');a=ap.parse_args()
    return do_export(a.include_outputs) if a.action=='export' else do_import(Path(a.file) if a.file else None)
if __name__=='__main__':raise SystemExit(main())
