from __future__ import annotations
"""Create restorable live backups. Never archive a WAL-mode SQLite main file directly."""
import json
import os
import sqlite3
import tempfile
import time
import uuid
import zipfile
from pathlib import Path
from . import config


def create_backup(include_outputs=False, *, destination_dir=None, filename_prefix='SeedanceStudio_backup'):
    """Snapshot SQLite via the online backup API, then publish ZIP atomically.

    The studio keeps SQLite in WAL mode. Zipping data/studio.db directly can
    silently omit recently committed rows that still live in the -wal file.
    """
    outdir=Path(destination_dir) if destination_dir is not None else config.DATA_DIR/'backups'
    outdir.mkdir(parents=True,exist_ok=True)
    stamp=time.strftime('%Y%m%d_%H%M%S')+'_'+uuid.uuid4().hex[:8]
    dst=outdir/f'{filename_prefix}_{stamp}.zip'
    manifest={'created_at':time.time(),'version':config.APP_VERSION,
              'root':str(config.ROOT),'include_outputs':bool(include_outputs),'files':[]}
    with tempfile.TemporaryDirectory(prefix='seedance_backup_') as tmp:
        tempdir=Path(tmp)
        snapshot=tempdir/'studio.db'
        if config.DB_PATH.is_file():
            # SQLite online backup incorporates committed WAL pages consistently.
            # Fail rather than produce a deceptively successful incomplete backup.
            source=sqlite3.connect(config.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True,timeout=30)
            try:
                target=sqlite3.connect(snapshot)
                try:
                    source.backup(target, pages=256, sleep=0.1)
                    check=target.execute('PRAGMA quick_check').fetchone()
                    if not check or check[0]!='ok':
                        raise RuntimeError('数据库一致性验证未通过，已取消生成备份。')
                finally:target.close()
            finally:source.close()
        include=[]
        for p in (config.ROOT/'.env',config.DATA_DIR/'secrets.json'):
            if p.is_file():include.append(p)
        for base in (config.ASSET_DIR,):
            if base.is_dir():include.extend(p for p in base.rglob('*') if p.is_file() and not p.is_symlink())
        if include_outputs and config.OUTPUT_DIR.is_dir():
            include.extend(p for p in config.OUTPUT_DIR.rglob('*') if p.is_file() and not p.is_symlink())
        for p in (config.DATA_DIR/'shared_profile.json',config.DATA_DIR/'current_url.txt'):
            if p.is_file():include.append(p)
        partial=outdir/(dst.name+'.partial')
        try:
            with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                if snapshot.is_file():
                    z.write(snapshot,'data/studio.db')
                    manifest['files'].append('data/studio.db')
                for p in include:
                    rel=p.relative_to(config.ROOT).as_posix()
                    z.write(p,rel)
                    manifest['files'].append(rel)
                z.writestr('backup_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            with zipfile.ZipFile(partial) as z:
                if z.testzip() is not None:
                    raise RuntimeError('备份 ZIP 校验失败，未生成正式备份。')
            os.replace(partial,dst)
        finally:
            partial.unlink(missing_ok=True)
    return {'path':str(dst),'name':dst.name,'size_bytes':dst.stat().st_size,
            'include_outputs':bool(include_outputs)}
