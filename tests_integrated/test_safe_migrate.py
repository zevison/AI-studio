"""Migrate real SQLite WAL snapshots; preserve data on failures, no network."""
import importlib.util,sqlite3
from pathlib import Path
import pytest
spec=importlib.util.spec_from_file_location('safe_migrate',Path(__file__).resolve().parents[1]/'tools/safe_migrate.py')
migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)

def installation(root,version='5.0.0'):
    (root/'core').mkdir(parents=True);(root/'core/config.py').write_text('APP_VERSION = '+repr(version))
    (root/'data/assets').mkdir(parents=True)
    return root

@pytest.mark.parametrize('version',['5.0.0','5.0.2','5.1.1'])
def test_real_wal_history_and_materials_preserved(tmp_path,version):
    old=installation(tmp_path/'old',version);new=installation(tmp_path/'new')
    (old/'data/assets/a.mp4').write_bytes(b'user material')
    (old/'.env').write_text('PRIVATE_TEST_KEY=keep-private')
    c=sqlite3.connect(old/'data/studio.db')
    try:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE tasks(id TEXT,status TEXT)');c.execute("INSERT INTO tasks VALUES('old','completed')");c.commit()
        result=migration.migrate(old,new)
        assert result['state']=='complete'
        copied=sqlite3.connect(new/'data/studio.db')
        try:assert copied.execute('SELECT * FROM tasks').fetchall()==[('old','completed')]
        finally:copied.close()
        assert (new/'data/assets/a.mp4').read_bytes()==(old/'data/assets/a.mp4').read_bytes()
        assert (new/'.env').read_bytes()==(old/'.env').read_bytes()
        with pytest.raises(PermissionError):migration.migrate(old,new)
    finally:c.close()

@pytest.mark.parametrize('status',['running','submission_unknown'])
def test_running_or_uncertain_paid_work_blocks_before_copy(tmp_path,status):
    old=installation(tmp_path/'old');new=installation(tmp_path/'new')
    c=sqlite3.connect(old/'data/studio.db');c.execute('CREATE TABLE tasks(status TEXT)')
    c.execute('INSERT INTO tasks VALUES(?)',(status,));c.commit();c.close()
    with pytest.raises(PermissionError):migration.migrate(old,new)
    assert not list(new.glob('.migration-*'))

def test_copy_failure_keeps_original_and_destination_data(tmp_path):
    old=installation(tmp_path/'old');new=installation(tmp_path/'new')
    (old/'data/assets/a.mp4').write_bytes(b'original');(new/'data/readme.txt').write_bytes(b'preexisting')
    with pytest.raises(OSError):migration.migrate(old,new,fail_after=1)
    assert (old/'data/assets/a.mp4').read_bytes()==b'original'
    assert (new/'data/readme.txt').read_bytes()==b'preexisting'
    assert len(list(new.glob('.migration-stage-*')))==1

def test_corrupt_database_is_not_copied(tmp_path):
    old=installation(tmp_path/'old');new=installation(tmp_path/'new')
    (old/'data/studio.db').write_bytes(b'corrupt')
    with pytest.raises(sqlite3.DatabaseError):migration.migrate(old,new)
    assert not list(new.glob('.migration-*'))


def test_migration_downgrade_is_refused(tmp_path):
    old=installation(tmp_path/'old','5.3.0');new=installation(tmp_path/'new','5.2.0')
    with pytest.raises(PermissionError):migration.migrate(old,new)
