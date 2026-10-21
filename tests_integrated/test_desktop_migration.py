import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest

from core import desktop_migration as migration


def legacy(tmp_path, *, status='completed', future=False):
    root = tmp_path / '旧版 工作台'
    data = root / 'data'
    (data / 'assets').mkdir(parents=True)
    (data / 'assets/cup.png').write_bytes(b'original-synthetic-image')
    (data / 'hypit_projects/hp_test').mkdir(parents=True)
    (data / 'hypit_projects/hp_test/main.svc').write_text('<Film/>', encoding='utf8')
    (root / '.env').write_text('OPENLUX_API_KEY=do-not-copy', encoding='utf8')
    (data / 'secrets.json').write_text('{"api_key":"do-not-copy"}', encoding='utf8')
    (data / 'cache').mkdir()
    (data / 'cache/unused').write_bytes(b'cache')
    conn = sqlite3.connect(data / 'studio.db')
    conn.execute('PRAGMA journal_mode=WAL')
    conn.execute('CREATE TABLE app_meta(key TEXT PRIMARY KEY,value TEXT)')
    conn.execute('INSERT INTO app_meta VALUES(?,?)', ('schema_version', str(migration.schema_version() + int(future))))
    conn.execute('CREATE TABLE assets(id TEXT PRIMARY KEY,path TEXT,metadata TEXT)')
    original = str(data / 'assets/cup.png')
    metadata = {'path': original, 'outside': str(root / 'data-other/assets/cup.png'), 'url': 'https://example.test/video'}
    conn.execute('INSERT INTO assets VALUES(?,?,?)', ('cup', original, json.dumps(metadata)))
    conn.execute('CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT,provider_task_id TEXT)')
    conn.execute('INSERT INTO tasks VALUES(?,?,?)', ('task', status, 'acknowledged-task-id'))
    conn.execute('CREATE TABLE generation_previews(token TEXT,expires_at REAL)')
    conn.execute('INSERT INTO generation_previews VALUES(?,?)', ('old-preview', 9999999999))
    conn.execute('CREATE TABLE keyed(id TEXT PRIMARY KEY,path TEXT) WITHOUT ROWID')
    conn.execute('INSERT INTO keyed VALUES(?,?)', ('cup', original))
    conn.commit()
    return root, conn


def test_wal_snapshot_rebase_and_sensitive_exclusion(tmp_path):
    root, conn = legacy(tmp_path)
    try:
        conn.execute('INSERT INTO tasks VALUES(?,?,?)', ('wal-only', 'completed', 'second-id'))
        conn.commit()
        before = conn.execute('SELECT path FROM assets').fetchone()[0]
        target = tmp_path / '新电脑 用户目录'
        receipt = migration.migrate_legacy(root, target)
        assert receipt['state'] == 'complete'
        assert receipt['credentials_copied'] is False
        assert not (target / '.env').exists()
        assert not (target / 'data/secrets.json').exists()
        assert not (target / 'data/cache').exists()
        assert (root / '.env').read_text().endswith('do-not-copy')
        with sqlite3.connect(target / 'data/studio.db') as migrated:
            path, raw = migrated.execute('SELECT path,metadata FROM assets').fetchone()
            assert path == str(target / 'data/assets/cup.png')
            assert json.loads(raw)['outside'] == str(root / 'data-other/assets/cup.png')
            assert json.loads(raw)['url'] == 'https://example.test/video'
            assert migrated.execute('SELECT count(*) FROM tasks').fetchone()[0] == 2
            assert migrated.execute('SELECT path FROM keyed').fetchone()[0] == path
            assert migrated.execute('SELECT expires_at FROM generation_previews').fetchone()[0] == 0
            assert migrated.execute('PRAGMA quick_check').fetchone()[0] == 'ok'
        assert conn.execute('SELECT path FROM assets').fetchone()[0] == before
        assert (target / 'data/hypit_projects/hp_test/main.svc').read_text() == '<Film/>'
        assert migration.migrate_legacy(root, target)['reused'] is True
    finally:
        conn.close()


def test_existing_target_never_merged(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    home = tmp_path / 'new'
    (home / 'data').mkdir(parents=True)
    untouched = home / 'data/important.txt'
    untouched.write_bytes(b'keep')
    with pytest.raises(FileExistsError):
        migration.migrate_legacy(root, home)
    assert untouched.read_bytes() == b'keep'


def test_interruption_preserves_source_and_can_retry(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    before = hashlib.sha256((root / 'data/studio.db').read_bytes()).hexdigest()
    home = tmp_path / 'new'
    with pytest.raises(OSError, match='Injected'):
        migration.migrate_legacy(root, home, fail_after=1)
    assert not (home / 'data').exists()
    assert json.loads(next((home / 'migrations').glob('*.json')).read_text(encoding='utf8'))['state'] == 'failed_preserved'
    assert hashlib.sha256((root / 'data/studio.db').read_bytes()).hexdigest() == before
    assert migration.migrate_legacy(root, home)['state'] == 'complete'


@pytest.mark.parametrize('status', ['running', 'submission_unknown', 'unknown', 'paid_result_unknown'])
def test_unresolved_paid_work_refuses_migration_without_replay(tmp_path, status):
    root, conn = legacy(tmp_path, status=status)
    conn.close()
    with pytest.raises(PermissionError, match='结果不明'):
        migration.migrate_legacy(root, tmp_path / 'new')
    assert not (tmp_path / 'new/data').exists()


def test_future_database_schema_rejected(tmp_path):
    root, conn = legacy(tmp_path, future=True)
    conn.close()
    with pytest.raises(ValueError, match='禁止降级'):
        migration.migrate_legacy(root, tmp_path / 'new')


def test_linked_data_rejected(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    original = migration._linked
    from unittest.mock import patch
    with patch.object(migration, '_linked', side_effect=lambda path: Path(path).name == 'cup.png' or original(path)):
        with pytest.raises(PermissionError, match='链接'):
            migration.migrate_legacy(root, tmp_path / 'new')


def test_live_process_marker_rejected(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    (root / 'seedance_studio.pid').write_text(str(os.getpid()), encoding='ascii')
    with pytest.raises(PermissionError, match='进程仍存在'):
        migration.migrate_legacy(root, tmp_path / 'new')


def test_modified_destination_cannot_be_overwritten_on_repeat(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    home = tmp_path / 'new'
    migration.migrate_legacy(root, home)
    (home / 'data/assets/cup.png').write_bytes(b'new-user-image')
    with pytest.raises(ValueError, match='目标数据已变化'):
        migration.migrate_legacy(root, home)
    assert (home / 'data/assets/cup.png').read_bytes() == b'new-user-image'


def test_discovery_only_direct_requested_locations(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    hidden = tmp_path / 'arbitrary/deep/unrequested/data'
    hidden.mkdir(parents=True)
    (hidden / 'studio.db').write_bytes(b'not-a-candidate')
    found = migration.discover_candidates([tmp_path])
    assert [item['root'] for item in found] == [str(root)]
    assert migration.discover_candidates([tmp_path], limit=1) == found


def test_corrupt_database_never_published(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    (root / 'data/studio.db').write_bytes(b'not sqlite')
    with pytest.raises(sqlite3.DatabaseError):
        migration.migrate_legacy(root, tmp_path / 'new')
    assert not (tmp_path / 'new/data').exists()


def test_permission_failure_retains_stage_and_original(tmp_path, monkeypatch):
    root, conn = legacy(tmp_path)
    conn.close()
    def denied(*args, **kwargs):
        raise PermissionError('injected destination denial')
    monkeypatch.setattr(migration.shutil, 'copy2', denied)
    with pytest.raises(PermissionError):
        migration.migrate_legacy(root, tmp_path / 'new')
    assert (root / 'data/assets/cup.png').read_bytes() == b'original-synthetic-image'
    assert not (tmp_path / 'new/data').exists()


def test_nested_destination_rejected(tmp_path):
    root, conn = legacy(tmp_path)
    conn.close()
    with pytest.raises(PermissionError, match='互相独立'):
        migration.migrate_legacy(root, root / 'new-version')


def test_timeout_is_bounded_and_does_not_publish(tmp_path, monkeypatch):
    root, conn = legacy(tmp_path)
    conn.close()
    ticks = iter([0, 5])
    monkeypatch.setattr(migration.time, 'monotonic', lambda: next(ticks, 10))
    with pytest.raises(TimeoutError):
        migration.migrate_legacy(root, tmp_path / 'new', timeout_seconds=1)
    assert not (tmp_path / 'new/data').exists()


def test_unrelated_sqlite_file_is_not_a_seedance_installation(tmp_path):
    root = tmp_path / 'unrelated'
    (root / 'data').mkdir(parents=True)
    conn = sqlite3.connect(root / 'data/studio.db')
    conn.execute('CREATE TABLE unrelated(id INTEGER)')
    conn.close()
    with pytest.raises(ValueError, match='不能认定'):
        migration.inspect_legacy(root)
