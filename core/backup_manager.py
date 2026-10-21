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
from .sqlite_snapshot import snapshot_database as _snapshot_database


def _files(base):
    if base.is_symlink() or (hasattr(base,'is_junction') and base.is_junction()):
        raise PermissionError('备份目录链接已拒绝')
    for folder,dirs,names in os.walk(base,followlinks=False):
        for name in dirs+names:
            path=Path(folder)/name
            if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()):
                raise PermissionError('备份目录含链接；已停止，防止越界读取')
        for name in names:
            if not name.endswith(('-wal','-shm')):yield Path(folder)/name


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
              'root':str(config.ROOT),'data_root':str(config.DATA_DIR),'include_outputs':bool(include_outputs),'files':[],
              'consistency_scope':'Per-database online snapshots and stable file copies; stop all work for a whole-workspace snapshot'}
    with tempfile.TemporaryDirectory(prefix='seedance_backup_') as tmp:
        tempdir=Path(tmp)
        snapshot=tempdir/'studio.db'
        if config.DB_PATH.is_file():
            # SQLite online backup incorporates committed WAL pages consistently.
            # Fail rather than produce a deceptively successful incomplete backup.
            _snapshot_database(config.DB_PATH,snapshot)
        include=[]
        if (config.DATA_DIR/'audio_subjects.db').is_file():include.append(config.DATA_DIR/'audio_subjects.db')
        # Ordinary backups are portable user data, not plaintext credential
        # exports. Credentials stay in the same-user DPAPI vault. A different
        # Windows account must authorize them again.
        for base in (config.ASSET_DIR,*(config.DATA_DIR/name for name in ('product_knowledge','hypit_projects','hypit_agent_sessions','hypit_token_grants','hypit_video_operations'))):
            if base.is_dir():include.extend(_files(base))
        if include_outputs and config.OUTPUT_DIR.is_dir():
            include.extend(_files(config.OUTPUT_DIR))
        for p in (config.DATA_DIR/'shared_profile.json',config.DATA_DIR/'current_url.txt'):
            if p.is_file():include.append(p)
        partial=outdir/(dst.name+'.partial')
        try:
            with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
                if snapshot.is_file():
                    z.write(snapshot,'data/studio.db')
                    manifest['files'].append('data/studio.db')
                for p in include:
                    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise PermissionError('备份文件链接已拒绝')
                    # A workspace backup must restore into its own data root,
                    # never under the exporting host's workspace identifier.
                    rel=('data/outputs/'+p.relative_to(config.OUTPUT_DIR).as_posix() if p.is_relative_to(config.OUTPUT_DIR) else 'data/'+p.relative_to(config.DATA_DIR).as_posix()
                         if p.is_relative_to(config.DATA_DIR) else p.relative_to(config.USER_HOME).as_posix())
                    if p.suffix.lower() in {'.db','.sqlite','.sqlite3'}:
                        auxiliary=tempdir/'auxiliary'/Path(rel)
                        _snapshot_database(p,auxiliary);z.write(auxiliary,rel)
                    else:
                        before=p.stat();z.write(p,rel);after=p.stat()
                        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise RuntimeError('文件在备份中变化，未发布备份；停止任务后重试')
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
