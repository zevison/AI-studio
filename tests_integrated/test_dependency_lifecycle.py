"""Offline supply-chain checks and isolated failed-upgrade recovery rehearsals."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import pytest

_path = Path(__file__).resolve().parents[1] / 'tools' / 'dependency_audit.py'
_spec = importlib.util.spec_from_file_location('dependency_audit', _path)
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)

@pytest.mark.parametrize('url,family', [
    ('http://nodejs.org/dist/file.zip', 'node'), ('https://nodejs.org.evil.example/file.zip', 'node'),
    ('https://user:token@nodejs.org/dist/file.zip', 'node'), ('file:///C:/secret', 'node'),
    ('https://storage.googleapis.com/attacker-bucket/file.zip', 'browser'),
    ('https://raw.githubusercontent.com/attacker/project/main/a.json', 'node'),
    ('https://registry.npmjs.org:444/package.tgz', 'npm')])
def test_unapproved_sources_are_rejected(url, family):
    with pytest.raises(ValueError): audit.validate_source(url, family)

def test_artifact_tamper_cannot_replace_a_hashed_runtime(tmp_path):
    binary = tmp_path / 'runtime.zip'; binary.write_bytes(b'original-runtime')
    record = {'url': 'https://nodejs.org/dist/v22.23.3/node-v22.23.3-win-x64.zip', 'sha256': audit.sha256_file(binary)}
    assert audit.verify_artifact(binary, record, 'node')['status'] == 'PASS'
    binary.write_bytes(b'tampered-runtime')
    with pytest.raises(ValueError, match='mismatch'): audit.verify_artifact(binary, record, 'node')

def test_missing_hash_is_not_reproducibility(tmp_path):
    binary = tmp_path / 'runtime.zip'; binary.write_bytes(b'payload')
    with pytest.raises(ValueError, match='pin'): audit.verify_artifact(binary, {'url': 'https://nodejs.org/dist/a.zip'}, 'node')
    with pytest.raises(ValueError, match='incomplete'): audit.verify_wheelhouse({'packages': [{'name': 'example'}], 'complete': False}, tmp_path)

def test_malicious_wheel_filename_does_not_escape_wheelhouse(tmp_path):
    manifest = {'complete': True, 'packages': [{'name': 'example', 'filename': '../escape.whl'}]}
    with pytest.raises(ValueError, match='filename'): audit.verify_wheelhouse(manifest, tmp_path)

def test_even_all_passing_gates_never_auto_promote_candidate():
    manifest = {'channel': 'candidate', 'gates': {key: 'PASS' for key in audit.REQUIRED_GATES}}
    result = audit.candidate_decision(manifest)
    assert result['eligible_for_human_promotion'] is True
    assert result['automatic_promotion'] is False
    assert result['stable_baseline_unchanged'] is True
    manifest['gates']['clean_windows'] = 'NOT TESTED'
    assert audit.candidate_decision(manifest)['eligible_for_human_promotion'] is False

def database(path):
    connection = sqlite3.connect(path)
    connection.execute('PRAGMA journal_mode=WAL')
    connection.executescript("CREATE TABLE app_meta(key TEXT PRIMARY KEY,value TEXT); INSERT INTO app_meta VALUES('schema_version','390'); CREATE TABLE tasks(id TEXT PRIMARY KEY,status TEXT); INSERT INTO tasks VALUES('retained-task','submission_unknown');")
    connection.commit()
    return connection

def test_dependency_upgrade_failure_and_future_schema_keep_old_baseline_usable(tmp_path):
    original = tmp_path / 'old' / 'studio.db'; original.parent.mkdir()
    source = database(original)
    command = [sys.executable, '-c', "import os,sqlite3; c=sqlite3.connect(os.environ['SEEDANCE_CANDIDATE_DB']); c.execute(\"UPDATE app_meta SET value='9999' WHERE key='schema_version'\"); c.commit(); raise RuntimeError('simulated incompatible dependency import')"]
    result = audit.probe_candidate_database(original, tmp_path / 'isolated-candidate', command)
    assert result['status'] == 'FAIL' and result['original_unchanged']
    assert source.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0] == '390'
    assert source.execute('SELECT id,status FROM tasks').fetchone() == ('retained-task', 'submission_unknown')
    assert source.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    candidate = sqlite3.connect(result['candidate_database'])
    assert candidate.execute("SELECT value FROM app_meta WHERE key='schema_version'").fetchone()[0] == '9999'
    candidate.close(); source.close()
    assert result['real_installer_rollback'] == 'NOT TESTED'

def test_hung_candidate_has_a_finite_deadline_and_keeps_baseline(tmp_path):
    original = tmp_path / 'old' / 'studio.db'; original.parent.mkdir()
    source = database(original)
    result = audit.probe_candidate_database(original, tmp_path / 'isolated-candidate', [sys.executable, '-c', 'import time;time.sleep(60)'], timeout=0.1)
    assert result['status'] == 'FAIL' and result['exit_code'] is None
    assert source.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 1
    source.close()

def test_candidate_cannot_write_into_original_data_directory(tmp_path):
    original = tmp_path / 'studio.db'; source = database(original)
    with pytest.raises(ValueError, match='separate'): audit.probe_candidate_database(original, tmp_path, [sys.executable, '-c', 'pass'])
    source.close()

def test_metadata_failure_saves_evidence_and_fails_monitor_instead_of_false_green(tmp_path, monkeypatch):
    root = tmp_path / 'source'
    provider = root / 'tools' / 'hypit_provider'; provider.mkdir(parents=True)
    (provider / 'package-lock.json').write_text('{"lockfileVersion":3,"packages":{}}', encoding='utf-8')
    (root / 'tools' / 'windows-runtime.lock.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(audit, 'python_inventory', lambda: {'packages': []})
    monkeypatch.setattr(audit, 'upstream_metadata', lambda: {'required_source': {'status': 'BLOCKED'}})
    report = tmp_path / 'report.json'
    assert audit.main(['--root', str(root), '--refresh', '--output', str(report)]) == 2
    assert json.loads(report.read_text(encoding='utf-8'))['upstream']['required_source']['status'] == 'BLOCKED'
