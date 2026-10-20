"""Actual SQLite handoff binding; fake Hypit host, no vendor calls."""
from concurrent.futures import ThreadPoolExecutor
import json
import hashlib
import time
import pytest
from core import creative_production as creative,db


@pytest.fixture
def pipeline(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'pipeline.db')
    db.init_db()
    monkeypatch.setattr(creative.hot_radar,'ensure_schema',lambda:None)
    creative.ensure_schema()
    db.execute('CREATE TABLE radar_scripts(id TEXT PRIMARY KEY,source_ids_json TEXT,content_json TEXT)')
    db.execute('INSERT INTO radar_scripts VALUES(?,?,?)',('script','["item"]',json.dumps({'script':{'video_breakdown':[{'time_seconds':1,'visual_cn':'杯子特写'}]}})))
    stamp=time.time()
    db.execute('INSERT INTO creative_video_jobs(id,product_id,script_id,state,model,edited_script_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',('job','product','script','await_h3_test','auto','{"hook_en":"A useful cup"}',stamp,stamp))
    product={'id':'product','name':'红色杯子','facts':[{'text':'陶瓷','status':'verified'}],'restrictions':['禁止声称保温12小时']}
    market=creative.us_marketing.context()
    doc={**{k:market[k] for k in ('market_country','as_of_us','season')},'seasonal_fit_cn':'全年适用',
         'hook_en':'A useful cup','hook_zh':'实用杯子','script_segments':[{'duration_s':5,'narration_en':'A ceramic cup','narration_zh':'陶瓷杯','visual_cn':'杯子实拍'}],
         'product_match':{'verdict':'suitable','reason_cn':'同类使用场景','verified_claims_used':['陶瓷'],'transferable_elements_cn':['商品特写']}}
    db.execute('UPDATE creative_video_jobs SET edited_script_json=? WHERE id=?',(json.dumps(doc),'job'))
    monkeypatch.setattr(creative.hot_radar,'products',lambda:[product])
    monkeypatch.setattr('core.viral_review.asset_for_item',lambda _: {'id':'real-video'})
    class Host:
        def __init__(self):self.requests=[];self.projects={}
        def create(self,request):
            self.requests.append(request);row={'id':'hp_test','metadata':{}}
            self.projects[row['id']]=row;return row
        def update(self,pid,metadata):self.projects[pid]['metadata'].update(metadata)
        def get(self,pid):return self.projects[pid]
    return Host(),product


def test_concurrent_handoff_reuses_one_project_and_binds_evidence(pipeline):
    host,_=pipeline
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda _:creative.hypit_handoff({'id':'job'},host),range(4)))
    assert len(host.requests)==1
    assert sum(not r['reused'] for r in results)==1
    assert all(r['paid_calls']==0 for r in results)
    request=host.requests[0]
    assert request['asset_ids']==['real-video']
    assert all(text in request['objective'] for text in ('陶瓷','保温12小时','杯子特写'))
    assert host.projects['hp_test']['metadata']['script_id']=='script'


def test_unverified_product_cannot_be_presented_as_approved(pipeline):
    host,product=pipeline;product['facts'][0]['status']='unverified'
    with pytest.raises(ValueError,match='至少一条'):creative.hypit_handoff({'id':'job'},host)
    assert host.requests==[]


def test_selected_model_never_silently_replaced(pipeline):
    host,_=pipeline
    db.execute("UPDATE creative_video_jobs SET model='minimax-video-h3' WHERE id='job'")
    with pytest.raises(ValueError,match='明确'):creative.hypit_handoff({'id':'job'},host)
    assert host.requests==[]


def test_follow_default_hypit_never_replaces_an_incompatible_global_model(pipeline,monkeypatch):
    from core import settings
    host,_=pipeline
    monkeypatch.setattr(settings,'get_all',lambda:{'video_base_model':'viduq3-turbo'})
    with pytest.raises(ValueError,match='不能静默'):creative.hypit_handoff({'id':'job'},host)
    assert host.requests==[]


@pytest.mark.parametrize('mismatch',[False,True])
def test_follow_default_binds_saved_preview_model_without_retired_router(pipeline,tmp_path,monkeypatch,mismatch):
    monkeypatch.setattr(creative.config,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr('core.viral_review.status',lambda _: {'ready':True,'asset_id':'real-video'})
    output=tmp_path/'follow-default.mp4';output.write_bytes(b'offline-binding-fixture')
    prompt=creative.test_handoff('job')['video_prompt']
    request={'prompt':prompt,'model':'doubao-seedance-2-5-260628:stable',
             '_request_model':'viduq3-turbo' if mismatch else 'doubao-seedance-2-5-260628:stable'}
    task=db.task_create('video','follow-default-fixture',request)
    db.task_update(task,status='completed',result_json={'local_path':str(output)})
    if mismatch:
        with pytest.raises(ValueError,match='一致的生成前模型'):creative.bind_h3_result({'id':'job','task_id':task})
        assert creative._get('job')['state']=='await_h3_test'
    else:assert creative.bind_h3_result({'id':'job','task_id':task})['state']=='await_first_cut_review'


def exported_fixture(pipeline,tmp_path,monkeypatch):
    host,_=pipeline
    creative.hypit_handoff({'id':'job'},host)
    monkeypatch.setattr(creative.config,'OUTPUT_DIR',tmp_path)
    output=tmp_path/'first-cut.mp4';output.write_bytes(b'synthetic-export-no-provider')
    artifact={'project_id':'hp_test','local_path':str(output),'sha256':hashlib.sha256(output.read_bytes()).hexdigest()}
    tid=db.task_create('video','synthetic',{'mode':'hypit','project_id':'hp_test'})
    db.task_update(tid,status='completed',result_json=artifact)
    host.projects['hp_test'].update(status='completed',task_id=tid)
    host.projects['hp_test']['metadata']['result']=artifact
    return host,output,tid


def test_hypit_export_returns_to_review_and_rejection_can_be_revised(pipeline,tmp_path,monkeypatch):
    host,output,tid=exported_fixture(pipeline,tmp_path,monkeypatch)
    job=creative.bind_hypit_result({'id':'job'},host)
    assert job['state']=='await_first_cut_review' and job['test_task_id']==tid
    assert job['first_cut_url']=='/outputs/first-cut.mp4'
    creative.review_first_cut({'id':'job','approve':False,'reason':'需要更换商品特写'})
    with pytest.raises(ValueError,match='需要确认'):creative.reopen_review({'id':'job'})
    revised=creative.reopen_review({'id':'job','confirm':True})
    assert revised['state']=='await_script_approval' and not revised['hypit_project_id'] and not revised['test_task_id']
    assert revised['review_history'][0]['hypit_project_id']=='hp_test'
    assert revised['review_history'][0]['test_task_id']==tid
    assert revised['review_history'][0]['rejection_reason']=='需要更换商品特写'
    assert output.is_file() and db.one('SELECT status FROM tasks WHERE id=?',(tid,))['status']=='completed'
    assert len(host.requests)==1  # review/reopen cannot start another project or provider call


@pytest.mark.parametrize('damage',['file','owner','task','unfinished','missing'])
def test_mismatched_or_tampered_hypit_export_cannot_be_bound(pipeline,tmp_path,monkeypatch,damage):
    host,output,tid=exported_fixture(pipeline,tmp_path,monkeypatch)
    if damage=='file':output.write_bytes(b'tampered')
    if damage=='owner':host.projects['hp_test']['metadata']['product_id']='other-product'
    if damage=='task':db.task_update(tid,request_json={'mode':'hypit','project_id':'other-project'})
    if damage=='unfinished':host.projects['hp_test']['status']='building'
    if damage=='missing':output.unlink()
    with pytest.raises(ValueError):creative.bind_hypit_result({'id':'job'},host)
    assert creative._get('job')['state']=='await_h3_test'


def test_repeated_hypit_bind_cannot_duplicate_review(pipeline,tmp_path,monkeypatch):
    host,_,_=exported_fixture(pipeline,tmp_path,monkeypatch)
    creative.bind_hypit_result({'id':'job'},host)
    with pytest.raises(ValueError,match='阶段'):creative.bind_hypit_result({'id':'job'},host)


def test_script_approval_rechecks_revoked_product_facts(pipeline):
    _,product=pipeline
    market=creative.us_marketing.context()
    doc={**{k:market[k] for k in ('market_country','as_of_us','season')},'seasonal_fit_cn':'全年适用',
        'hook_en':'A useful cup','hook_zh':'实用杯子','script_segments':[{'duration_s':5,'narration_en':'A ceramic cup','narration_zh':'陶瓷杯','visual_cn':'杯子实拍'}],
        'product_match':{'verdict':'suitable','reason_cn':'同类使用场景','verified_claims_used':['陶瓷'],'transferable_elements_cn':['商品特写']}}
    db.execute("UPDATE creative_video_jobs SET state='await_script_approval' WHERE id='job'")
    product['facts'][0]['status']='unverified'
    with pytest.raises(ValueError,match='商品匹配复核'):creative.approve_script({'id':'job','script':doc,'confirm':True})
    assert creative._get('job')['state']=='await_script_approval'
    product['facts'][0]['status']='verified'
    assert creative.approve_script({'id':'job','script':doc,'confirm':True})['state']=='await_h3_test'


def test_existing_hypit_cannot_bypass_revoked_claims(pipeline):
    host,product=pipeline
    creative.hypit_handoff({'id':'job'},host)
    product['facts'][0]['status']='unverified'
    with pytest.raises(ValueError,match='至少一条'):creative.hypit_handoff({'id':'job'},host)
    with pytest.raises(ValueError,match='至少一条'):creative.test_handoff('job')
    assert len(host.requests)==1


def test_changed_product_constraints_cannot_reuse_old_project(pipeline):
    host,product=pipeline
    creative.hypit_handoff({'id':'job'},host)
    product['restrictions'].append('禁止热水使用场景')
    with pytest.raises(ValueError,match='不能复用'):creative.hypit_handoff({'id':'job'},host)
    assert len(host.requests)==1


def test_unrelated_video_with_same_model_cannot_be_bound(pipeline,tmp_path,monkeypatch):
    _,_=pipeline
    monkeypatch.setattr(creative.config,'OUTPUT_DIR',tmp_path)
    monkeypatch.setattr('core.viral_review.status',lambda _: {'ready':True,'asset_id':'real-video'})
    db.execute("UPDATE creative_video_jobs SET model='viduq3-turbo' WHERE id='job'")
    path=tmp_path/'unrelated.mp4';path.write_bytes(b'fixture')
    tid=db.task_create('video','unrelated',{'prompt':'another product','model':'viduq3-turbo'})
    db.task_update(tid,status='completed',result_json={'local_path':str(path)})
    with pytest.raises(ValueError,match='未绑定当前商品'):creative.bind_h3_result({'id':'job','task_id':tid})
    assert creative._get('job')['state']=='await_h3_test'
    prompt=creative.test_handoff('job')['video_prompt']
    assert '陶瓷' in prompt and '保温12小时' in prompt and 'Product: product' in prompt
    db.task_update(tid,request_json={'prompt':prompt,'model':'viduq3-turbo'})
    assert creative.bind_h3_result({'id':'job','task_id':tid})['state']=='await_first_cut_review'
