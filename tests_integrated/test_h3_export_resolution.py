"""H3 export tiers: 1080P is a 2K request plus a local downscale, never a native tier.

Regression targets:
  * casing differences between the UI menu and API callers must not reject 1080P;
  * when the provider returns a clip smaller than the requested export tier the
    paid result is still delivered at native size, with a truthful warning,
    instead of failing after the charge.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import db, openlux, video_adapters

ROOT = Path(__file__).resolve().parents[1]


def app_function(name, **bindings):
    tree = ast.parse((ROOT / 'app.py').read_text('utf8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    scope = {'db': db, **bindings}
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'app.py', 'exec'), scope)
    return scope[name]


@pytest.fixture
def attach(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'studio.db')
    db.init_db()
    return app_function('attach_provider_task',
                        task_observability=SimpleNamespace(record=lambda *a, **k: None),
                        log=SimpleNamespace(warning=lambda *a, **k: None))


def _task(task_id, status, provider_id=None):
    db.execute('INSERT INTO tasks(id,kind,status,provider_task_id,created_at,updated_at) VALUES(?,?,?,?,?,?)',
               (task_id, 'video', status, provider_id, db.now(), db.now()))


def test_attach_provider_task_recovers_an_unknown_paid_submission(attach):
    _task('local-1', 'submission_unknown')
    row = attach('local-1', ' task_abc123 ')
    assert row['provider_task_id'] == 'task_abc123'
    assert row['status'] == 'running'
    assert row['last_polled_at'] == 0


def test_attach_provider_task_rejects_unsafe_or_duplicate_ids(attach):
    _task('local-1', 'submission_unknown')
    _task('local-2', 'running', 'task_taken')
    with pytest.raises(ValueError, match='格式'):
        attach('local-1', 'task id/../x')
    with pytest.raises(ValueError, match='另一个'):
        attach('local-1', 'task_taken')
    _task('local-3', 'completed')
    with pytest.raises(ValueError, match='已经完成'):
        attach('local-3', 'task_abc123')


def test_restart_does_not_leave_video_slots_stuck(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'studio.db')
    db.init_db()
    recover = app_function('recover_interrupted_video_tasks',
                           task_observability=SimpleNamespace(record=lambda *a, **k: None),
                           log=SimpleNamespace(warning=lambda *a, **k: None))
    db.execute("INSERT INTO tasks(id,kind,status,request_json,created_at,updated_at) VALUES('dead','video','pending',?,?,?)",
               ('{"mode":"direct"}', db.now(), db.now()))
    db.execute("INSERT INTO tasks(id,kind,status,provider_task_id,created_at,updated_at) VALUES('alive','video','running','task_1',?,?)",
               (db.now(), db.now()))
    db.execute("INSERT INTO tasks(id,kind,status,request_json,created_at,updated_at) VALUES('hypit','video','running',?,?,?)",
               ('{"mode":"hypit"}', db.now(), db.now()))

    assert recover() == 1
    assert db.one('SELECT status FROM tasks WHERE id=?', ('dead',))['status'] == 'submission_unknown'
    assert db.one('SELECT status FROM tasks WHERE id=?', ('alive',))['status'] == 'running'
    assert db.one('SELECT status FROM tasks WHERE id=?', ('hypit',))['status'] == 'running'


@pytest.mark.parametrize('resp,expected', [
    ({'status': 'succeeded'}, 'completed'),
    ({'task': {'status': 'failed'}}, 'failed'),
    ({'task': {'status': 'success'}, 'status': 'ok'}, 'completed'),
    ({'data': {'task': {'status': 'processing'}}}, 'running'),
])
def test_nested_provider_task_status_is_read(resp, expected):
    assert openlux.extract_status(resp) == expected


@pytest.mark.parametrize('value,native,resize', [
    ('1080P', '2K', True), ('1080p', '2K', True), ('2K', '2K', False),
    ('2k', '2K', False), ('768P', '768P', False), ('720p', '768P', True),
])
def test_h3_export_tier_is_case_insensitive(value, native, resize):
    plan = video_adapters.h3_resolution_plan(value)
    assert plan['provider_resolution'] == native
    assert plan['local_downscale'] is resize


def test_h3_accepts_the_mapped_native_tier_only():
    video_adapters.validate('minimax-video-h3', 'reference', '9:16', '2K', 10, [{'kind': 'video'}])
    with pytest.raises(ValueError, match='768P和2K'):
        video_adapters.validate('minimax-video-h3', 'reference', '9:16', '1080p', 10, [{'kind': 'video'}])


def test_undersized_paid_result_keeps_native_resolution(tmp_path, monkeypatch):
    import app

    raw = tmp_path / 'paid.mp4'
    raw.write_bytes(b'paid-result')
    monkeypatch.setattr(app.media, 'probe', lambda _p: {'width': 720, 'height': 1280, 'duration': 10})

    result = app._finish_audio_for_result(
        {'generated_raw_path': str(raw)},
        {'audio_mode': 'model', 'local_export_resolution': '1080p',
         'output_resolution': '1080p', 'resolution': '2K'},
    )

    assert result['export_resolution_fallback'] is True
    assert result['local_downscale_applied'] is False
    assert result['local_path'] == str(raw)
    assert '720x1280' in result['export_resolution_note']
    assert result['audio_status'] == 'ready'
