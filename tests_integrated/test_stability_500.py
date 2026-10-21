"""V5.2.0 regression: protect user history while the real workspace is running.

All tests run with temporary files and mock-like local data; no network, no paid API.
"""
from __future__ import annotations
import io
import json
import sqlite3
import time
import zipfile
from pathlib import Path
from unittest.mock import patch
import pytest

from core import backup_manager, config, production_system


def _root(monkeypatch, root):
    data=root/'data'
    data.mkdir(exist_ok=True)
    assets=data/'assets'
    outputs=data/'outputs'
    assets.mkdir(exist_ok=True)
    outputs.mkdir(exist_ok=True)
    monkeypatch.setattr(config,'ROOT',root)
    monkeypatch.setattr(config,'USER_HOME',root)
    monkeypatch.setattr(config,'DATA_ROOT',data)
    monkeypatch.setattr(config,'DATA_DIR',data)
    monkeypatch.setattr(config,'DB_PATH',data/'studio.db')
    monkeypatch.setattr(config,'ASSET_DIR',assets)
    monkeypatch.setattr(config,'OUTPUT_DIR',outputs)
    return data


def _active_wal(path):
    db=sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute('CREATE TABLE history(message TEXT)')
    db.commit()
    db.execute("INSERT INTO history(message) VALUES('latest paid task')")
    db.commit()
    assert path.with_name(path.name+'-wal').exists()
    return db


def _zip_db_rows(file):
    with zipfile.ZipFile(file) as z:
        assert z.testzip() is None
        assert 'data/studio.db' in z.namelist()
        return z.read('data/studio.db'),json.loads(z.read('backup_manifest.json'))


def test_live_wal_backup_contains_latest_committed_history(tmp_path,monkeypatch):
    _root(monkeypatch,tmp_path)
    c=_active_wal(config.DB_PATH)
    try:
        bk=backup_manager.create_backup()
        raw,manifest=_zip_db_rows(bk['path'])
        recovered=tmp_path/'restore.db'
        recovered.write_bytes(raw)
        with sqlite3.connect(recovered) as r:
            assert r.execute('SELECT message FROM history').fetchone()[0]=='latest paid task'
            assert r.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert manifest['version']=='5.2.0'
        assert manifest['files'].count('data/studio.db')==1
    finally:c.close()


def test_fast_repeated_backup_never_overwrites_previous(tmp_path,monkeypatch):
    _root(monkeypatch,tmp_path)
    c=_active_wal(config.DB_PATH)
    try:
        a=backup_manager.create_backup()
        b=backup_manager.create_backup()
        assert a['path']!=b['path']
        assert Path(a['path']).exists() and Path(b['path']).exists()
        assert not list((tmp_path/'data'/'backups').glob('*.partial'))
        _zip_db_rows(a['path'])
        _zip_db_rows(b['path'])
    finally:c.close()


def test_system_backup_uses_same_wal_safe_snapshot(tmp_path,monkeypatch):
    _root(monkeypatch,tmp_path)
    (config.ASSET_DIR/'sample.png').write_bytes(b'example')
    (config.OUTPUT_DIR/'video.mp4').write_bytes(b'mock mp4 payload')
    c=_active_wal(config.DB_PATH)
    try:
        out=production_system.backup_bundle()
        assert Path(out['path']).parent==tmp_path
        raw,manifest=_zip_db_rows(out['path'])
        assert manifest['include_outputs'] is True
        assert b'latest paid task' in raw
        with zipfile.ZipFile(out['path']) as z:
            assert 'data/assets/sample.png' in z.namelist()
            assert 'data/outputs/video.mp4' in z.namelist()
    finally:c.close()


def test_corrupt_db_does_not_claim_backup_success(tmp_path,monkeypatch):
    _root(monkeypatch,tmp_path)
    config.DB_PATH.write_bytes(b'not a sqlite database')
    with pytest.raises(sqlite3.DatabaseError):
        backup_manager.create_backup()
    assert not list((config.DATA_DIR/'backups').glob('*.zip'))
    assert not list((config.DATA_DIR/'backups').glob('*.partial'))


def test_backup_excludes_plaintext_credentials(tmp_path,monkeypatch):
    _root(monkeypatch,tmp_path)
    (tmp_path/'.env').write_text('OPENLUX_API_KEY=synthetic-secret-only',encoding='utf8')
    (config.DATA_DIR/'secrets.json').write_text('{"api_key":"synthetic-secret-only"}',encoding='utf8')
    connection=_active_wal(config.DB_PATH)
    try:
        out=backup_manager.create_backup()
        with zipfile.ZipFile(out['path']) as archive:
            assert '.env' not in archive.namelist()
            assert 'data/secrets.json' not in archive.namelist()
            assert all(b'synthetic-secret-only' not in archive.read(name) for name in archive.namelist())
    finally:connection.close()


def test_sqlite_snapshot_extended_windows_destination(tmp_path):
    from core.sqlite_snapshot import snapshot_database, _native
    source=tmp_path/'source.db'
    connection=_active_wal(source)
    destination=tmp_path/('deep-path-'*8)/('中文路径 '*12)/('nested-'*10)/'snapshot.db'
    try:
        assert len(str(destination))>260
        snapshot_database(source,destination)
        with sqlite3.connect(_native(destination)) as copied:
            assert copied.execute('SELECT message FROM history').fetchone()[0]=='latest paid task'
    finally:connection.close()


def test_only_one_setup_and_start_entrypoint_and_stable_version():
    root=Path(__file__).resolve().parents[1]
    assert sorted(x.name for x in root.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd']
    assert not list(root.glob('*.vbs'))
    assert config.APP_VERSION=='5.2.0'
    assert '5.2.0' in (root/'01_INSTALL.cmd').read_text(encoding='utf-8')
    assert '5.2.0' in (root/'static/index.html').read_text(encoding='utf-8')
    assert '5.2.0' in (root/'tools/install_new_pc.ps1').read_text(encoding='utf-8-sig')


def test_bad_http_body_length_is_rejected_before_read():
    from app import Handler
    class Dummy:
        headers={'Content-Length':'-10'}
        rfile=io.BytesIO(b'')
    with pytest.raises(ValueError,match='长度无效'):
        Handler.json_in(Dummy())
