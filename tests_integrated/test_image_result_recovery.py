"""Model receipt vs local save are separate stages; retries never generate again."""
import io
from pathlib import Path
import pytest
from PIL import UnidentifiedImageError
from core import db,config


@pytest.fixture
def isolated(tmp_path,monkeypatch):
    import app
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'studio.db');db.init_db()
    for key,name in [('ASSET_DIR','assets'),('OUTPUT_DIR','outputs')]:
        path=tmp_path/name;path.mkdir();monkeypatch.setattr(config,key,path)
    monkeypatch.setattr(app.task_observability,'record',lambda *args:None)
    return app


def test_image_receipt_survives_local_save_failure(isolated,monkeypatch):
    app=isolated;calls=[];response={'data':[{'url':'https://fixture.example/result.png'}]}
    monkeypatch.setattr(app.openlux,'image_generate',lambda *a,**k:calls.append(1) or response)
    monkeypatch.setattr(app,'save_generated_image',lambda _:(_ for _ in ()).throw(TimeoutError('download timeout')))
    class Request:
        def json_in(self):return {'prompt':'fixture picture','ratio':'1:1'}
        def ok(self,value=None,**kwargs):return value
        def err(self,*args,**kwargs):raise AssertionError('Unexpected hard failure')
    result=app.Handler._image_generate(Request())
    assert result['local_save_pending'] is True
    row=db.one("SELECT * FROM tasks WHERE kind='image'")
    assert row['status']=='result_pending' and db.loads(row['response_json'])==response
    assert db.one("SELECT COUNT(*) n FROM usage_events WHERE kind='image'")['n']==1
    with pytest.raises(TimeoutError):app.finalize_image_task(row['id'])
    assert len(calls)==1 and db.one('SELECT status FROM tasks WHERE id=?',(row['id'],))['status']=='result_pending'


def test_completed_image_recovery_is_idempotent_and_does_not_call_model(isolated,monkeypatch):
    app=isolated
    task=db.task_create('image','fixture',{})
    db.task_update(task,status='completed',result_json={'version_id':'already-saved'})
    monkeypatch.setattr(app,'save_generated_image',lambda _:pytest.fail('completed response saved twice'))
    assert db.loads(app.finalize_image_task(task)['result_json'])['version_id']=='already-saved'


def test_invalid_base64_image_never_becomes_published_output(isolated):
    with pytest.raises(UnidentifiedImageError):isolated.save_generated_image({'data':[{'b64_json':'bm90IGFuIGltYWdl'}]})
    assert not list(config.OUTPUT_DIR.iterdir()) and not db.all_('SELECT * FROM assets')


def test_unknown_image_without_receipt_cannot_trigger_model_retry(isolated,monkeypatch):
    task=db.task_create('image','unknown',{});db.task_update(task,status='submission_unknown')
    monkeypatch.setattr(isolated.openlux,'image_generate',lambda *a,**k:pytest.fail('unexpected new generation'))
    with pytest.raises(ValueError):isolated.finalize_image_task(task)


def test_observation_failure_does_not_undo_saved_image(isolated,monkeypatch):
    app=isolated;task=db.task_create('image','fixture-telemetry',{'prompt':'fixture'})
    db.task_update(task,status='result_pending',response_json={'data':[{'url':'https://fixture/image.png'}]})
    monkeypatch.setattr(app,'save_generated_image',lambda _:({'id':'a','stored_name':'fixture.png','width':32,'height':32},'fixture.png'))
    monkeypatch.setattr(app.task_observability,'record',lambda *a:(_ for _ in ()).throw(OSError('diagnostic unavailable')))
    assert app.finalize_image_task(task)['status']=='completed'
    assert app.finalize_image_task(task)['status']=='completed'
    assert db.one('SELECT COUNT(*) n FROM generation_versions')['n']==1


def test_image_recovery_list_never_loads_or_exposes_base64_receipts(isolated):
    task=db.task_create('image','fixture-list',{})
    db.task_update(task,status='result_pending',response_json={'data':[{'b64_json':'fixture-payload'}]})
    class Request:
        path='/api/image/recovery'
        def ok(self,value):return value
    rows=isolated.Handler._get(Request())
    assert len(rows)==1 and rows[0]['id']==task
    assert set(rows[0])=={'id','status','created_at'}


def test_usage_write_failure_keeps_known_image_receipt_recoverable(isolated,monkeypatch):
    app=isolated;calls=[];response={'data':[{'url':'https://fixture.example/result.png'}]}
    monkeypatch.setattr(app.openlux,'image_generate',lambda *a,**k:calls.append(1) or response)
    monkeypatch.setattr(db,'usage_add',lambda *a,**k:(_ for _ in ()).throw(OSError('database interrupted')))
    class Request:
        def json_in(self):return {'prompt':'fixture usage failure','ratio':'1:1'}
    with pytest.raises(OSError):app.Handler._image_generate(Request())
    row=db.one("SELECT * FROM tasks WHERE kind='image'")
    assert row['status']=='result_pending' and db.loads(row['response_json'])==response and len(calls)==1
