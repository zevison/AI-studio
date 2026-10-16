"""V4.2.8.10 local regression: no real external GPT/Bright Data/video charge."""
from __future__ import annotations
import json, subprocess, sys, tempfile, hashlib
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1];sys.path.insert(0,str(P))
from core import db,config,hot_radar,viral_review,model_pricing,us_marketing,openlux,secrets_store,creative_production
from core import media

def main():
 with tempfile.TemporaryDirectory() as root:
  base=Path(root);db_path=base/'studio.db'
  with patch.object(db,'DB_PATH',db_path),patch.object(config,'DB_PATH',db_path),patch.object(config,'CACHE_DIR',base/'cache'):
   db.init_db();hot_radar._schema_initialized=False;hot_radar.ensure_schema();viral_review.schema()
   product=hot_radar.save_product({'name':'Demo air pump','keywords':['portable air pump'],
     'facts':[{'text':'Pumps compatible inflatables','status':'verified','source':'manual'}]})
   src={'url':'https://www.tiktok.com/@test/video/77777770000000001','title':'camping air pump','kind':'video',
        'region':'US','media_url':'https://v16-webapp-prime.us.tiktok.com/video/example.mp4',
        'cover_url':'https://p16.tiktokcdn.com/cover.jpg','viewCount':120000}
   assert hot_radar.import_tiktok_batch({'product_id':product['id'],'records':[src]})['inserted']==1
   item=hot_radar.items(product['id'])[0]
   assert item['metadata']['media_url']==src['media_url']
   assert not item['video_review']['ready']
   try:hot_radar.script_preview({'product_id':product['id'],'source_ids':[item['id']]})
   except ValueError as e:assert '视频' in str(e)
   else:raise AssertionError('GPT text-only should have been blocked')
   from shutil import which
   if not which('ffmpeg') or not which('ffprobe'):
    print('PARTIAL PASS: media guard and record import; FFmpeg absent on this test machine')
    return
   file=base/'sample.mp4'
   subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','testsrc=size=160x288:rate=5',
                   '-t','2.1','-pix_fmt','yuv420p','-y',str(file)],check=True,timeout=30)
   md=media.probe(file)
   db.execute('''INSERT INTO assets(id,original_name,stored_name,kind,mime,path,sha256,size_bytes,duration,width,height,fps,has_audio,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',('asset_test','sample.mp4','sample.mp4','video','video/mp4',str(file),hashlib.sha256(file.read_bytes()).hexdigest(),file.stat().st_size,md['duration'],md['width'],md['height'],md['fps'],0,1))
   bound=viral_review.bind({'item_id':item['id'],'asset_id':'asset_test'})
   assert bound['ready']
   evidence=viral_review.visual_evidence(item['id'],3)
   assert len(evidence['frames'])>=2
   with patch.object(model_pricing,'get',return_value={'available':True,'input_usd_per_million':2,'output_usd_per_million':8,'warning':'TEST'}):
    p=hot_radar.script_preview({'product_id':product['id'],'source_ids':[item['id']],'model':'gpt-6.1-sol'})
    assert p['visual_source_confirmed'] and p['estimated_cost_usd']>0
    hot_radar.save_options({'daily_llm_usd_limit':1})
    market=us_marketing.context()
    doc={'market_country':'US','as_of_us':market['as_of_us'],'season':market['season'],
         'seasonal_fit_cn':'使用场景不要求特定天气','title_cn':'测试版','reason_cn':'镜头看到了桌面',
         'hook_en':'Check this out!','hook_zh':'看看这个！',
         'video_breakdown':[{'time_seconds':evidence['frames'][0]['time'],'visual_cn':'视频中的测试色块','creative_insight_cn':'仅供测试'}],
         'script_segments':[{'duration_s':4,'narration_en':'Check this compact pump.','narration_zh':'看看这款便携气泵','visual_cn':'真实商品演示','source_ids':[item['id']]}],
         'risk_notes_cn':['确认商品真实性']}
    called=[]
    def fake_request(method,path,request,**kwargs):
     assert method=='POST'
     chunks=request['messages'][1]['content']
     assert sum(1 for x in chunks if x.get('type')=='image_url')>=2
     called.append(1)
     return {'choices':[{'message':{'content':json.dumps(doc,ensure_ascii=False)}}], 'usage':{'total_tokens':1100}}
    with patch.object(secrets_store,'get',return_value='test_openlux_token'),patch.object(openlux,'request_json',side_effect=fake_request):
     result=hot_radar.generate_script({'approve_paid':True,'product_id':product['id'],'source_ids':[item['id']],
                  'model':'gpt-6.1-sol','max_cost_usd':0.3})
    assert result['status']=='completed' and len(called)==1
    history=hot_radar.scripts(product['id']);assert history[0]['content']['visual_evidence']['asset_id']=='asset_test'
    job=creative_production.from_script(result['id'])
    assert job['state']=='await_script_approval'
    saved=creative_production.approve_script({'id':job['id'],'script':doc,'confirm':True})
    assert saved['state']=='await_h3_test'
    handed=creative_production.test_handoff(saved['id'])
    assert handed['reference_asset_id']=='asset_test'
    print('PASS: import->manual video->real frames->GPT multi-image mock->script->one confirmation->handoff; charged API calls=0')
if __name__=='__main__':main()
