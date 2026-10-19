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
