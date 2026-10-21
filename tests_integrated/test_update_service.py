import json
import sqlite3
import time
from pathlib import Path

import pytest
from test_update_security import repository
from core.update_service import assert_admission_open,business_fingerprint
from core.update_security import UpdateBlocked,UpdateRejected


def ready(repository,tmp_path,status='completed'):
    home=tmp_path/'user';data=home/'data';data.mkdir(parents=True)
    db=sqlite3.connect(data/'studio.db');db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE tasks(id TEXT,status TEXT)');db.execute('INSERT INTO tasks VALUES(?,?)',('kept',status))
    db.execute('CREATE TABLE app_meta(key TEXT,value TEXT)');db.execute('INSERT INTO app_meta VALUES(?,?)',('schema_version','390'))
    db.commit()
    service=repository.service(home,signature_verifier=lambda path,signers:{'status':'LOCAL CONTROLLED TEST VERIFIER; not production Authenticode'})
    found=service.check();service.download(found['target_id'])
    install=tmp_path/'installed';install.mkdir();(install/'SeedanceStudio.exe').write_bytes(b'original controlled runner fixture')
    (install/'_internal').mkdir();(install/'_internal/resource').write_bytes(b'resource')
    stop=home/'desktop-sessions/current/shutdown';stop.parent.mkdir(parents=True)
    options={'confirmed':True,'stop_file':stop,'parent_pid':12345,'parent_birthstamp':'123456789','install_root':install}
    return service,found,db,options


def test_update_gate_and_wal_snapshot_are_durable_and_cancellable(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    try:
        db.execute('INSERT INTO tasks VALUES(?,?)',('wal-only','completed'));db.commit()
        prepared=service.prepare_install(found['target_id'],**options)
        plan=json.loads(Path(prepared['plan']).read_text('utf8'))
        assert prepared['runner_bytes']>0
        assert Path(prepared['helper_exe']).read_bytes()==b'original controlled runner fixture'
        snap=plan['snapshots'][0]
        with sqlite3.connect(snap['snapshot']) as copy:assert copy.execute('SELECT count(*) FROM tasks').fetchone()[0]==2
        with pytest.raises(UpdateBlocked):assert_admission_open(service.home)
        # A fresh service/process sees the durable gate too.
        with sqlite3.connect(service.control_path) as control:
            control.execute('BEGIN IMMEDIATE')
            with pytest.raises(UpdateBlocked):assert_admission_open(service.home,control)
        service.cancel_prepared(prepared['transaction_id'])
        assert_admission_open(service.home)
    finally:db.close()


@pytest.mark.parametrize('state',['running','submission_unknown','unknown','usage_unverified'])
def test_running_or_unknown_tasks_prevent_update_without_clearing_evidence(repository,tmp_path,state):
    service,found,db,options=ready(repository,tmp_path,state)
    try:
        with pytest.raises(UpdateBlocked,match='结果未知'):service.prepare_install(found['target_id'],**options)
        assert db.execute('SELECT status FROM tasks').fetchone()[0]==state
        assert_admission_open(service.home)
    finally:db.close()


def test_no_confirmation_never_pauses_or_stages_update(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    try:
        options['confirmed']=False
        with pytest.raises(UpdateBlocked,match='确认'):service.prepare_install(found['target_id'],**options)
        assert_admission_open(service.home)
        assert not (service.directory/'runners').exists()
    finally:db.close()


def test_busy_admission_ledger_prevents_updating(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    try:
        with sqlite3.connect(service.control_path) as conn:
            conn.execute('CREATE TABLE admissions(id TEXT,state TEXT)');conn.execute("INSERT INTO admissions VALUES('paid-call','unknown')")
        with pytest.raises(UpdateBlocked):service.prepare_install(found['target_id'],**options)
        assert_admission_open(service.home)
    finally:db.close()


def test_rollback_refuses_post_upgrade_business_writes(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    try:
        prepared=service.prepare_install(found['target_id'],**options)
        plan=json.loads(Path(prepared['plan']).read_text('utf8'))
        db.execute("INSERT INTO tasks VALUES('new-after-upgrade','completed')");db.commit()
        with pytest.raises(UpdateBlocked,match='已变化'):service.recovery.restore(plan)
        assert db.execute('SELECT count(*) FROM tasks').fetchone()[0]==2
    finally:db.close()


def test_snapshot_restore_preserves_business_and_reverts_schema_marker(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    before=business_fingerprint(service.home/'data/studio.db')
    prepared=service.prepare_install(found['target_id'],**options);plan=json.loads(Path(prepared['plan']).read_text('utf8'))
    db.execute("UPDATE app_meta SET value='391'");db.commit();db.close()
    result=service.recovery.restore(plan)
    assert result['old_program_restored'] is False
    with sqlite3.connect(service.home/'data/studio.db') as restored:
        assert restored.execute('SELECT value FROM app_meta').fetchone()[0]=='390'
    assert business_fingerprint(service.home/'data/studio.db')==before
    assert result['preserved_new_databases']


def test_failed_runner_copy_clears_gate_keeps_original_database(repository,tmp_path,monkeypatch):
    service,found,db,options=ready(repository,tmp_path)
    try:
        def failure(*args):raise OSError('injected unavailable disk')
        monkeypatch.setattr(service.installer,'stage_runner',failure)
        with pytest.raises(OSError):service.prepare_install(found['target_id'],**options)
        assert_admission_open(service.home)
        assert db.execute('SELECT * FROM tasks').fetchall()==[('kept','completed')]
    finally:db.close()


def test_async_real_tuf_check_reports_result_without_blocking_ui(repository,tmp_path):
    service=repository.service(tmp_path/'user')
    initial=service.begin_check()
    assert initial['job']['action']=='check'
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        current=service.status()['job']
        if current['state']!='running':break
        time.sleep(.01)
    assert current['state']=='completed',current
    assert current['result']['version']=='5.2.1'


def test_frontend_cannot_submit_arbitrary_package_path(repository,tmp_path):
    service=repository.service(tmp_path/'user')
    with pytest.raises(UpdateRejected):service.download(r'C:\arbitrary\evil.exe')


def test_callback_launch_failure_releases_prepared_gate(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    try:
        def fail(result):raise OSError('helper could not start')
        service.begin_install(found['target_id'],on_prepared=fail,**options)
        deadline=time.monotonic()+10
        while time.monotonic()<deadline and service.status()['job']['state']=='running':time.sleep(.01)
        assert service.status()['job']['state']=='failed'
        assert_admission_open(service.home)
    finally:db.close()


def test_versioned_install_plan_separates_app_root_and_running_payload(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    try:
        root=options['install_root'];program=root/'versions'/(service.current_version+'-'+service.current_build)
        program.mkdir(parents=True)
        (root/'SeedanceStudio.exe').rename(program/'SeedanceStudio.exe')
        (root/'_internal').rename(program/'_internal')
        options['program_root']=program
        prepared=service.prepare_install(found['target_id'],**options)
        plan=json.loads(Path(prepared['plan']).read_text('utf8'))
        assert plan['versioned_layout'] is True
        assert Path(plan['install_root'])==root and Path(plan['program_root'])==program
        from desktop.update_helper import _restore_program
        assert _restore_program(plan) is None
        assert (program/'SeedanceStudio.exe').read_bytes()==b'original controlled runner fixture'
        service.cancel_prepared(prepared['transaction_id'])
    finally:db.close()


def test_changed_credentials_block_restore_before_any_database_replacement(repository,tmp_path):
    service,found,db,options=ready(repository,tmp_path)
    vault=service.home/'secrets.dpapi';vault.write_bytes(b'old-encrypted-fixture')
    prepared=service.prepare_install(found['target_id'],**options)
    plan=json.loads(Path(prepared['plan']).read_text('utf8'))
    before=business_fingerprint(service.home/'data/studio.db');db.close()
    vault.write_bytes(b'new-encrypted-fixture')
    with pytest.raises(UpdateBlocked,match='凭据已变化'):service.recovery.restore(plan)
    assert vault.read_bytes()==b'new-encrypted-fixture'
    assert business_fingerprint(service.home/'data/studio.db')==before


def test_startup_metadata_and_empty_migration_table_do_not_count_as_business_write(tmp_path):
    file=tmp_path/'state.db'
    with sqlite3.connect(file) as conn:
        conn.execute('CREATE TABLE tasks(id TEXT,status TEXT)')
        conn.execute("INSERT INTO tasks VALUES('kept','completed')")
        conn.execute('CREATE TABLE radar_options(key TEXT,value_json TEXT,updated_at REAL)')
        conn.execute("INSERT INTO radar_options VALUES('runtime_last_start','1',1)")
    before=business_fingerprint(file)
    with sqlite3.connect(file) as conn:
        conn.execute("UPDATE radar_options SET value_json='2',updated_at=2")
        conn.execute('CREATE TABLE new_empty_table(id TEXT)')
    assert business_fingerprint(file)==before
    with sqlite3.connect(file) as conn:conn.execute("INSERT INTO radar_options VALUES('user_keyword','changed',3)")
    assert business_fingerprint(file)!=before
