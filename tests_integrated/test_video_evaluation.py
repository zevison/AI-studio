import json
import math
from pathlib import Path
import pytest
from core import config,db,video_evaluation as ve,intelligent_router as router,production_system as ps

@pytest.fixture
def sample(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'studio.db');db.init_db();ve.schema()
    out=tmp_path/'outputs';out.mkdir();monkeypatch.setattr(config,'OUTPUT_DIR',out)
    p=out/'sample.mp4';p.write_bytes(b'offline-fixture-not-a-real-video')
    req={'model':'doubao-seedance-2-5-260628:stable','mode':'text','prompt':'A red cup','ratio':'9:16','resolution':'720p','duration':5}
    result={'local_path':str(p),'quality_check':{'ok':True,'technical_status':'passed','issues':[]}}
    task=db.task_create('video','fixture',req);db.task_update(task,status='completed',result_json=result)
    return task,req,result,p

def payload(task):
    record=ve.overview()['tasks'][0]
    return {'task_id':task,'identity':record['identity'],'scores':{k:4 for k in ve.DIMENSIONS},'facts_verified':True,'usable':True,'notes':'Offline unit-test review, not actual product quality'}

def test_review_persists_but_never_invents_unknown_cost(sample):
    task,*_=sample;ve.review(payload(task));info=ve.overview()
    assert info['tasks'][0]['review']['usable']
    assert info['comparisons'][0]['cost_per_usable_video_usd'] is None
    assert info['comparisons'][0]['usable_rate']==1
    assert info['comparisons'][0]['evidence_level']=='pilot'

def test_task_or_artifact_change_invalidates_review(sample):
    task,req,result,p=sample;old=payload(task);ve.review(old)
    p.write_bytes(b'changed artifact content')
    assert ve.overview()['tasks'][0]['review_stale']
    with pytest.raises(ValueError,match='变化'):ve.review(old)

@pytest.mark.parametrize('change',[{'facts_verified':False},{'scores':{k:2 for k in ve.DIMENSIONS}}, {'scores':{k:True for k in ve.DIMENSIONS}}])
def test_review_cannot_bypass_facts_or_score_rules(sample,change):
    task,*_=sample;p=payload(task);p.update(change)
    with pytest.raises(ValueError):ve.review(p)

@pytest.mark.parametrize('status',['failed','incomplete','not_checked'])
def test_technical_failure_or_missing_check_cannot_be_marked_usable(sample,status):
    task,req,result,_=sample;result['quality_check']['technical_status']=status
    if status=='not_checked':result.pop('quality_check')
    db.task_update(task,result_json=result)
    with pytest.raises(ValueError,match='质检'):ve.review(payload(task))

def test_path_escape_refused(sample,tmp_path):
    task,req,result,_=sample;p=tmp_path/'outside.mp4';p.write_bytes(b'outside')
    result['local_path']=str(p);db.task_update(task,result_json=result)
    with pytest.raises(ValueError,match='保存到本地'):ve.review(payload(task))

def test_failed_attempts_count_toward_cost_but_missing_reviews_block_ranking():
    base={'local_only':False,'case_key':'same','model':'A','status':'completed','review':{'usable':True},'actual_cost_usd':2}
    rows=[base,{**base,'status':'failed','review':None,'actual_cost_usd':3}]
    assert ve.summarize(rows)[0]['cost_per_usable_video_usd']==5
    rows[1]['status']='completed'
    assert ve.summarize(rows)[0]['cost_per_usable_video_usd'] is None
    rows[1]['actual_cost_usd']=None
    assert ve.summarize(rows)[0]['total_actual_usd'] is None


def test_unknown_paid_outcome_cannot_disappear_from_cost_denominator():
    base={'local_only':False,'case_key':'same','model':'A','status':'completed','review':{'usable':True},'actual_cost_usd':1}
    result=ve.summarize([base,{**base,'status':'submission_unknown','review':None}])[0]
    assert result['attempts']==2 and result['unsettled_attempts']==1
    assert result['total_actual_usd'] is None and result['cost_per_usable_video_usd'] is None

def test_distinct_specs_and_local_composites_are_not_pooled():
    req={'prompt':'cup','duration':5,'mode':'text','ratio':'9:16','resolution':'720p'}
    key=ve.case_key(req)
    for field,val in [('duration',10),('prompt','bottle'),('ratio','16:9'),('resolution','1080p'),('asset_ids',['other'])]:
        assert ve.case_key({**req,field:val})!=key
    assert ve.case_key({**req,'model':'other','_request_model':'other:stable'})==key
    base={'local_only':True,'case_key':key,'model':'A','status':'completed','review':{'usable':True},'actual_cost_usd':0}
    assert ve.summarize([base])==[]

def test_duplicate_billing_records_not_silently_summed_or_zeroed(sample):
    task,*_=sample
    for i in range(2):db.execute('INSERT INTO usage_events(id,task_id,kind,actual_cost_usd,created_at) VALUES(?,?,?,?,?)',(str(i),task,'video',1,db.now()))
    assert ve.overview()['tasks'][0]['actual_cost_usd'] is None

def test_recheck_updates_quality_without_changing_generation_latency(sample,monkeypatch):
    task,*_=sample;old=db.one('SELECT updated_at FROM tasks WHERE id=?',(task,))['updated_at']
    monkeypatch.setattr(ps,'quality_check',lambda *a,**k:{'ok':True,'technical_status':'warning','issues':[]})
    result=ve.check(payload(task));assert result['paid_calls']==0
    assert db.one('SELECT updated_at FROM tasks WHERE id=?',(task,))['updated_at']==old

def test_router_requires_comparable_human_reviewed_outcomes():
    ctx={'mode':'text','prompt':'cup','ratio':'9:16','resolution':'720p','duration':5}
    rows=[{'model':'m','case_key':ve.case_key(ctx),'status':'completed','has_downloadable_video':True,'actual_cost_usd':1,'elapsed_seconds':10} for _ in range(5)]
    assert not router.historical_metrics('m',rows,ctx)['enough_evidence']
    for row in rows:row.update(quality_accepted=True,technical_status='passed')
    assert router.historical_metrics('m',rows,ctx)['cost_per_usable_video_usd']==1
    rows[-1].update(status='failed',quality_accepted=False)
    assert router.historical_metrics('m',rows,ctx)['cost_per_usable_video_usd']==1.25
    rows[0]['actual_cost_usd']=math.nan
    assert router.historical_metrics('m',rows,ctx)['cost_per_usable_video_usd'] is None
    assert not router.historical_metrics('m',rows,{**ctx,'duration':10})['enough_evidence']

def test_technical_check_missing_opencv_is_explicitly_incomplete(monkeypatch,tmp_path):
    monkeypatch.setattr(ps,'tool_status',lambda:{})
    monkeypatch.setattr(ps.media,'probe',lambda p:{'duration':5,'width':720,'height':1280})
    monkeypatch.setattr(ps,'_module',lambda name:False)
    monkeypatch.setattr(ps.smart_analysis,'quick_quality_semantics',lambda p:{'used':False})
    result=ps.quality_check(tmp_path/'no-video',expected={'duration':5,'ratio':'9:16'})
    assert result['technical_status']=='incomplete'
    result=ps.quality_check(tmp_path/'no-video',expected={'duration':10,'ratio':'16:9','generate_audio':True,'resolution':'1080p'})
    assert not result['ok'] and result['technical_status']=='failed'
    assert len([v for v in result['issues'] if v['level']=='error'])==4

def test_frame_decoder_exception_releases_video_capture(monkeypatch,tmp_path):
    import sys,types
    class Capture:
        released=False
        def get(self,k):return 30
        def set(self,*a):pass
        def read(self):return True,object()
        def release(self):self.released=True
    cap=Capture()
    fake=types.SimpleNamespace(VideoCapture=lambda p:cap,CAP_PROP_FRAME_COUNT=1,CAP_PROP_FPS=2,CAP_PROP_POS_FRAMES=3,COLOR_BGR2GRAY=4,
                              cvtColor=lambda *a:(_ for _ in ()).throw(RuntimeError('decoder failed')))
    monkeypatch.setitem(sys.modules,'cv2',fake);monkeypatch.setattr(ps,'tool_status',lambda:{})
    monkeypatch.setattr(ps,'_module',lambda n:True);monkeypatch.setattr(ps.media,'probe',lambda p:{'duration':5})
    monkeypatch.setattr(ps.smart_analysis,'quick_quality_semantics',lambda p:{'used':False})
    assert ps.quality_check(tmp_path/'fake')['technical_status']=='incomplete'
    assert cap.released
