"""Product state, admission, material integrity and restart tests. No live API."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

import pytest
from core import config, db
from core.hypit_account import token_quote
from core.hypit_studio import HypitStudio


@pytest.fixture
def product(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', tmp_path/'studio.db')
    monkeypatch.setattr(config, 'DATA_DIR', tmp_path)
    monkeypatch.setattr(config, 'ASSET_DIR', tmp_path/'assets')
    monkeypatch.setattr(config, 'OUTPUT_DIR', tmp_path/'outputs')
    config.ASSET_DIR.mkdir();config.OUTPUT_DIR.mkdir()
    db.init_db()
    file=config.ASSET_DIR/'reference.mp4';file.write_bytes(b'bounded test asset')
    db.execute('INSERT INTO assets(id,path,kind,sha256,duration) VALUES(?,?,?,?,?)',
        ('asset_test',str(file),'video',hashlib.sha256(file.read_bytes()).hexdigest(),2))
    instance=HypitStudio(root=tmp_path/'projects')
    monkeypatch.setattr('core.hypit_studio.media.ffmpeg_path',lambda:'ffmpeg.exe')
    monkeypatch.setattr('core.hypit_studio.media.ffprobe_path',lambda:'ffprobe.exe')
    # This fixture tests admission/state behavior. Native media decoding is
    # separately exercised by the actual Runtime installation smoke tests.
    monkeypatch.setattr('core.hypit_studio.media.probe',lambda path:{'ok':True,'duration':2,'width':320,'height':240,
        'has_audio':Path(path).suffix in {'.mp3','.wav','.m4a'}})
    monkeypatch.setattr('core.hypit_studio.settings.effective_limits',lambda:{'max_concurrent_video_tasks':10})
    return instance


def test_chained_local_build_keeps_child_worker_registered(product,monkeypatch):
    pid='hp_worker_fixture'
    monkeypatch.setattr(product,'get',lambda _: {'id':pid,'metadata':{}})
    advance=threading.Event();started=threading.Event();release=threading.Event()
    def child():
        started.set()
        assert release.wait(3)
    def parent():
        assert advance.wait(3)
        product._spawn(pid,'child',child)
    product._spawn(pid,'parent',parent)
    owner=product.threads[pid]
    advance.set()
    try:
        assert started.wait(3)
        owner.join(3)
        assert not owner.is_alive()
        next_worker=product.threads[pid]
        assert next_worker is not owner and next_worker.is_alive()
    finally:
        release.set()
        for worker in list(product.threads.values()):worker.join(3)


def create(product):
    return product.create({'objective':'Keep first second', 'asset_ids':['asset_test']})


def test_material_fingerprint_binds_copy(product):
    row=create(product)
    copied=product.path(row['id'])/'assets/material-1.mp4'
    assert hashlib.sha256(copied.read_bytes()).hexdigest()==row['metadata']['inventory'][0]['sha256']


def test_rechecking_unchanged_finished_project_preserves_completed_status(product,monkeypatch):
    from core.hypit_production_gate import source_digest
    row=create(product);pid=row['id'];source=product.path(pid)/'main.svml'
    source.write_text('<svml/>')
    class Tools:
        def call(self,*args):
            return {'ok':True,'document':{'providers':[{'use':'@hypit/media-local'}]}}
    monkeypatch.setattr(product,'tools',lambda _:Tools())
    monkeypatch.setattr(product,'generation_source_preflight',lambda _:{'ok':True})
    output=config.OUTPUT_DIR/'finished.mp4';output.write_bytes(b'local rendered fixture')
    product.update(pid,status='completed',metadata={'validation':{'source_hash':source_digest(product.path(pid))},
        'result':{'local_path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}})
    assert product.validate(pid)['ok']
    assert product.get(pid)['status']=='completed'
    source.write_text('<svml><!-- changed --></svml>')
    assert product.validate(pid)['ok']
    assert product.get(pid)['status']=='ready'


def test_paid_reference_preflight_requires_explicit_audio_semantics(product):
    row=create(product);folder=product.path(row['id'])
    source='''<?svml using="@hypit/markup@1"?><svml>
    <import as="media" from="@hypit/media@1"/><import as="copy" from="@hypit/text@1"/>
    <import as="seedance" from="@hypit/seedance@1"/>
    <media:Video id="source" src="./assets/material-1.mp4"/><copy:Value id="direction">Blue background</copy:Value>
    <seedance:ReferenceVideo id="final" model="2.5" prompt={direction} duration="-1" aspect-ratio="adaptive" resolution="720p">
    <seedance:Reference video={source} person-reference="false"/></seedance:ReferenceVideo></svml>'''
    (folder/'main.svml').write_text(source,encoding='utf8')
    assert product.generation_source_preflight(row['id'])['ok'] is False
    (folder/'main.svml').write_text(source.replace('resolution="720p"','resolution="720p" generate-audio="false"'),encoding='utf8')
    assert product.generation_source_preflight(row['id'])['ok'] is True


def test_charge_estimate_survives_local_failure_and_recovery_without_duplicate(product):
    row=create(product);tid=product._claim(row['id'],'building',{'prepared'})
    payload={'model':'verified-model'};quote={'estimated_usd':'1.25','request_hash':'same-request'}
    product._acknowledge_generation(tid,'remote1',payload,quote)
    db.task_update(tid,status='failed',error='local renderer failed after supplier acknowledgement')
    product._acknowledge_generation(tid,'remote1',payload,quote)
    events=db.all_("SELECT * FROM usage_events WHERE task_id=? AND kind='hypit_openlux'",(tid,))
    assert len(events)==1 and events[0]['estimated_cost_usd']==1.25 and events[0]['actual_cost_usd'] is None
    with pytest.raises(PermissionError):product._acknowledge_generation(tid,'different-remote',payload,quote)


def test_changed_material_is_rejected_and_cleaned(product):
    (config.ASSET_DIR/'reference.mp4').write_bytes(b'replaced')
    with pytest.raises(ValueError,match='Material changed'):create(product)
    assert list(product.root.iterdir())==[]
    assert product.list()==[]


@pytest.mark.parametrize('pid',['../../secrets','hp_'+'a'*32+'/..','hp_'+'a'*31,'hp_'+'z'*32])
def test_project_id_cannot_escape(product,pid):
    with pytest.raises(ValueError):product.path(pid)


def test_audio_options_require_audio(product):
    with pytest.raises(ValueError,match='没有音频'):
        product.create({'objective':'test','asset_ids':['asset_test'],'audio_mode':'reference_voice'})


def test_concurrent_claim_does_not_duplicate_task(product):
    row=create(product)
    def attempt(_):
        try:return product._claim(row['id'],'authoring',{'prepared'})
        except PermissionError:return None
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(attempt,range(8)))
    assert sum(bool(r) for r in results)==1
    assert db.one('SELECT COUNT(*) n FROM tasks')['n']==1


def test_global_limit_includes_legacy_tasks(product):
    row=create(product)
    for i in range(10):db.task_create('video',str(i),{'mode':'direct'})
    with pytest.raises(PermissionError,match='上限'):product._claim(row['id'],'authoring',{'prepared'})
    assert product.get(row['id'])['status']=='prepared'


def test_restart_isolation_does_not_call_provider(product):
    row=create(product);tid=product._claim(row['id'],'authoring',{'prepared'})
    product.recover()
    assert product.get(row['id'])['status']=='interrupted'
    # No video submit occurred: authoring interruption must not permanently
    # consume a remote-video slot. Any LLM uncertainty remains in its own bank.
    assert db.one('SELECT * FROM tasks WHERE id=?',(tid,))['status']=='failed'


def test_corrupt_paid_gateway_is_quarantined_and_recovery_continues(product):
    row=create(product);tid=product._claim(row['id'],'building',{'prepared'})
    product.update(row['id'],metadata={'video_operation':{'task_id':tid,'quote':{'account':'test','request_hash':'abc'}}})
    ledger=product.video_directory(row['id'])/'video-gateway.db'
    ledger.write_bytes(b'not a sqlite database')
    second=create(product);second_tid=product._claim(second['id'],'authoring',{'prepared'})
    product.recover()
    assert db.one('SELECT * FROM tasks WHERE id=?',(tid,))['status']=='submission_unknown'
    assert db.one('SELECT * FROM tasks WHERE id=?',(second_tid,))['status']=='failed'
    assert product.get(row['id'])['metadata']['remote_video_task']['recovery_error']=='gateway_ledger_unreadable'


def test_worker_failure_survives_unreadable_secret_store(product,monkeypatch):
    row=create(product);tid=product._claim(row['id'],'authoring',{'prepared'})
    def unreadable():raise OSError('DPAPI unavailable')
    monkeypatch.setattr('core.hypit_studio.profile_store.read_secrets',unreadable)
    def fail():raise RuntimeError('https://private.example/path?token=sensitive')
    product._spawn(row['id'],tid,fail)
    import time
    deadline=time.monotonic()+3
    while product.get(row['id'])['status']=='authoring' and time.monotonic()<deadline:time.sleep(.01)
    assert product.get(row['id'])['status']=='blocked'
    error=product.get(row['id'])['error']
    assert 'sensitive' not in error and 'private.example' not in error
    assert db.one('SELECT * FROM tasks WHERE id=?',(tid,))['status']=='failed'


def test_unknown_hypit_post_without_acknowledgement_keeps_global_slot(product):
    row=create(product)
    for i in range(10):
        tid=db.task_create('video','unknown-hypit-'+str(i),{'mode':'hypit'})
        db.task_update(tid,status='submission_unknown')
    with pytest.raises(PermissionError):product._claim(row['id'],'authoring',{'prepared'})


def test_cancellation_not_lost_when_worker_updates(product):
    row=create(product);product._claim(row['id'],'authoring',{'prepared'})
    product.cancel(row['id'])
    product.update(row['id'],metadata={'token_calls':1})
    with pytest.raises(InterruptedError):product._cancelled(row['id'])


def test_explicit_token_consent_precedes_all_io(product):
    row=create(product)
    with patch.object(product,'quote',side_effect=AssertionError('Unexpected network')):
        with pytest.raises(PermissionError):product.author(row['id'],{'max_calls':4,'limit_usd':'0.5'})
    assert product.get(row['id'])['status']=='prepared'


def test_prices_require_exact_account_currency_and_all_groups():
    vault={'openlux_api_key':'test-key','openlux_account_access_token':'test-account'}
    docs={
      '/api/status':{'data':{'currency_type':'USD','quota_per_unit':500000}},
      '/api/pricing':{'data':[{'model_name':'gpt-6-luna','quota_type':0,'model_ratio':0.05,
        'completion_ratio':5,'enable_groups':['a','b'],'supported_endpoint_types':['openai']}]},
      '/api/user/self/groups':{'data':{'ratios':{'a':0.5,'b':1}}},
      '/v1/models':{'data':[{'id':'gpt-6-luna'}]}}
    def reader(path,*args):return docs[path]
    result=token_quote(vault,reader=reader)
    assert result['input_usd_per_million']=='0.10'
    assert result['output_usd_per_million']=='0.50'
    del docs['/api/user/self/groups']['data']['ratios']['b']
    with pytest.raises(ValueError,match='incomplete'):token_quote(vault,reader=reader)


def test_unknown_currency_cannot_enable_token_calls():
    def reader(path,*args):return {'data':{'currency_type':'CNY'}} if path=='/api/status' else {'data':[]}
    with pytest.raises(PermissionError,match='USD'):
        token_quote({'openlux_api_key':'x','openlux_account_access_token':'y'},reader=reader)
