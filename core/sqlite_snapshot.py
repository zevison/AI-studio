"""Bounded, validated SQLite online snapshots, including committed WAL pages."""
import os
from pathlib import Path
import sqlite3
import tempfile
import time


def snapshot_database(source,destination,timeout_seconds=60):
    source,destination=Path(source),Path(destination)
    if destination.exists():raise FileExistsError('Snapshot destination already exists')
    if source.is_symlink() or (hasattr(source,'is_junction') and source.is_junction()):
        raise PermissionError('Database link refused')
    destination.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.sqlite-snapshot-',suffix='.partial',dir=destination.parent)
    os.close(fd);temporary=Path(name);started=time.monotonic()
    def progress(*_):
        if time.monotonic()-started>timeout_seconds:raise TimeoutError('SQLite snapshot timed out; no incomplete backup was published')
    try:
        incoming=sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)
        try:
            outgoing=sqlite3.connect(temporary)
            try:
                incoming.backup(outgoing,pages=256,sleep=.1,progress=progress)
                check=outgoing.execute('PRAGMA quick_check').fetchone()
                if not check or check[0]!='ok':raise sqlite3.DatabaseError('Snapshot integrity check failed')
            finally:outgoing.close()
        finally:incoming.close()
        # Windows rename refuses an existing destination, preserving earlier backups.
        temporary.rename(destination)
    finally:temporary.unlink(missing_ok=True)
    return destination
