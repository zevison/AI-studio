"""Actual SQLite handoff binding; fake Hypit host, no vendor calls."""
from concurrent.futures import ThreadPoolExecutor
import json
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
