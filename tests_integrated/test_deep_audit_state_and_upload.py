"""Real SQLite + production functions; no networking or model generation."""
import ast
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace
import pytest
from core import db, utils, openlux

ROOT=Path(__file__).resolve().parents[1]


def app_function(name, **bindings):
    tree=ast.parse((ROOT/'app.py').read_text('utf8'))
    node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==name)
    scope={'db':db,**bindings}
    exec(compile(ast.Module(body=[node],type_ignores=[]),'app.py','exec'),scope)
    return scope[name]


@pytest.mark.parametrize('raw',[b'binary\r\n\r\n',b'bytes--',b'before--boundary-inside-after\r\n'])
def test_upload_preserves_every_file_byte(raw):
    body=(b'--boundary\r\nContent-Disposition: form-data; name="file"; filename="x.txt"\r\n'
          b'Content-Type: application/octet-stream\r\n\r\n'+raw+b'\r\n--boundary--\r\n')
    parts=utils.parse_multipart(body,'multipart/form-data; boundary=boundary')
    assert len(parts)==1 and parts[0]['data']==raw


def test_unterminated_upload_is_not_accepted():
    with pytest.raises(ValueError):
        utils.parse_multipart(b'--b\r\nContent-Disposition: form-data; name="f"\r\n\r\ndata',
                              'multipart/form-data; boundary=b')


@pytest.fixture
def database(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'studio.db')
    db.init_db()
    return db


def add_task(key,status='running',provider='remote',created=1,request=None):
    db.execute('INSERT INTO tasks(id,kind,status,provider_task_id,created_at,request_json) VALUES(?,?,?,?,?,?)',
               (key,'video',status,provider,created,db.dumps(request or {})))


def test_unknown_jobs_cannot_starve_acknowledged_tasks(database):
    for i in range(25):add_task('unknown'+str(i),'submission_unknown',None,i)
    add_task('actual',created=100)
    assert [r['id'] for r in db.pollable_video_tasks()]==['actual']


def test_polling_rotates_beyond_batch_limit(database):
    for i in range(35):add_task(str(i),created=i)
    first=db.pollable_video_tasks(20)
    for row in first:db.task_update(row['id'],last_polled_at=time.time())
    second=db.pollable_video_tasks(20)
    assert len({r['id'] for r in first+second})==35


def test_recent_history_limit_keeps_older_active_task(database):
    add_task('still-active',created=1)
    for i in range(180):add_task('done'+str(i),'completed',created=i+2)
    assert 'still-active' in {r['id'] for r in db.video_task_snapshot(80)}


def test_unknown_progress_is_never_failed():
    progress=app_function('_task_progress',config=SimpleNamespace(),_provider_status_text=lambda r:None)
    result=progress({'status':'submission_unknown','created_at':time.time()}, {},{}, {})
    assert not any(x['state']=='failed' for x in result['stages'])
    assert any(x['state']=='unknown' for x in result['stages'])


def test_future_database_version_is_never_downgraded(database):
    db.execute("UPDATE app_meta SET value=? WHERE key='schema_version'",(str(db.CURRENT_SCHEMA_VERSION+1),))
    with pytest.raises(RuntimeError):db.init_db()
    assert int(db.one("SELECT value FROM app_meta WHERE key='schema_version'")['value'])==db.CURRENT_SCHEMA_VERSION+1


def test_failed_schema_migration_rolls_back_ddl(database,monkeypatch):
    monkeypatch.setattr(db,'SCHEMA',['CREATE TABLE half_migration(x TEXT)','THIS IS INVALID SQL'])
    with pytest.raises(sqlite3.OperationalError):db.init_db()
    assert db.one("SELECT name FROM sqlite_master WHERE name='half_migration'") is None


@pytest.mark.parametrize('response',[
    {'status':'unrecognized','image_url':'https://cdn.example/cover.jpg'},
    {'data':{'image_url':'https://cdn.example/cover.jpg'}},
    {'content':[{'type':'video_url','role':'reference_video','video_url':{'url':'https://cdn.example/input.mp4'}}]},
])
def test_reference_and_cover_urls_are_not_completed_video(response):
    assert openlux.extract_status(response)!='completed'
    assert openlux.extract_result_url(response) is None


def test_known_video_result_contract_still_works():
    response={'status':'succeeded','content':{'video_url':'https://cdn.example/output.mp4?signature=fixture'}}
    assert openlux.extract_status(response)=='completed'
    assert openlux.extract_result_url(response)==response['content']['video_url']


def test_one_slow_task_does_not_block_another_task(database,monkeypatch):
    import app,threading
    from concurrent.futures import ThreadPoolExecutor
    keys=['slow',next('fast'+str(i) for i in range(100) if app._task_lock('slow') is not app._task_lock('fast'+str(i)))]
    for key in keys:add_task(key,provider=key)
    entered=threading.Event();release=threading.Event()
    def query(remote,*args):
        if remote=='slow':entered.set();assert release.wait(5)
        return {'id':remote,'status':'running'}
    monkeypatch.setattr(app.openlux,'video_query',query)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(app.refresh_video_task,db.one('SELECT * FROM tasks WHERE id=?',(keys[0],)))
        try:
            assert entered.wait(2)
            second=pool.submit(app.refresh_video_task,db.one('SELECT * FROM tasks WHERE id=?',(keys[1],)))
            assert second.result(timeout=2)['id']==keys[1]
        finally:release.set()
        first.result(timeout=2)


def test_concurrent_status_refresh_queries_same_task_only_once(database,monkeypatch):
    import app,threading
    from concurrent.futures import ThreadPoolExecutor
    add_task('same',provider='same');calls=[];start=threading.Barrier(4)
    def query(*args):calls.append(1);return {'id':'same','status':'running'}
    monkeypatch.setattr(app.openlux,'video_query',query)
    row=db.one("SELECT * FROM tasks WHERE id='same'")
    def refresh():start.wait(timeout=3);return app.refresh_video_task(row)
    with ThreadPoolExecutor(max_workers=4) as pool:answers=list(pool.map(lambda _:refresh(),range(4)))
    assert len(calls)==1 and all(x['status']=='running' for x in answers)
