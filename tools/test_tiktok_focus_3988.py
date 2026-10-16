"""V3.9.8.8 offline no-cost TikTok-only regression."""
import datetime as dt,json,sys,tempfile
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import db,config,hot_radar as r,creative_production as cp,us_marketing

def main():
 with tempfile.TemporaryDirectory() as td,patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(config,'DB_PATH',Path(td)/'studio.db'):
  db.init_db();r._schema_initialized=False;r.ensure_schema()
  product=r.save_product({'name':'AIR3 camping air pump','keywords':['camping air pump','inflatable mattress','portable camping'],'facts':[{'status':'verified','text':'Small electric pump'}]})
  pid=product['id']; now=dt.datetime(2026,10,12,8,tzinfo=dt.timezone.utc).timestamp()
  fmt=lambda x:dt.datetime.fromtimestamp(now-x*86400,dt.timezone.utc).isoformat()
  # Never collect unrelated networks; never accept forged tiktok host names
  for bad in ['https://www.youtube.com/watch?v=123','https://www.tiktok.com.evil.net/test','file:///tmp/test']:
   try:r.add_manual({'product_id':pid,'url':bad,'title':'test'});assert False,bad
   except ValueError:pass
  manual=r.add_manual({'product_id':pid,'url':'https://www.tiktok.com/@demo/video/001','title':'camping air pump setup','published_at':fmt(2)})
  assert manual['id']
  r.add_item(pid,'news','legacy','https://news.google.com/abc','camping air pump legacy',published_at=fmt(1))
  one=r.scan(pid,now=now)
  assert one['quality_candidates']==1 and one['window']['mode']=='tiktok_recent_7d',one
  assert one['filtered_legacy']==1 and one['paid_ai_calls']==0
  assert len(r.state(pid)['items'])==1
  try:r.script_preview({'product_id':pid,'source_ids':[r.add_item(pid,'news','legacy','https://bbc.com/a','camping air pump',published_at=fmt(1))['id']]});assert False
  except ValueError:pass
  batch={'product_id':pid,'records':[
   {'url':'https://www.tiktok.com/@demo/video/002','title':'inflatable mattress camping gear','published_at':fmt(10),'metrics':{'viewCount':100}},
   {'url':'https://www.youtube.com/watch?v=bad','title':'bad'},
   {'url':'https://www.tiktok.com/@demo/video/003','title':'unrelated dance','published_at':fmt(2)}]}
  imp=r.import_tiktok_batch(batch)
  assert imp['inserted']==2 and len(imp['skipped'])==1,imp
  two=r.scan(pid,now=now)
  assert two['window']['mode']=='tiktok_recent_7d',two
  # When there is no fresh 7d matching input, fallback to 14d
  older=r.save_product({'name':'AIR3 camping air pump','keywords':['camping air pump','inflatable mattress'],'facts':[]})
  p2=older['id']
  r.import_tiktok_batch({'product_id':p2,'records':[{'url':'https://www.tiktok.com/@demo/video/004','title':'camping air pump','published_at':fmt(10)}]})
  three=r.scan(p2,now=now)
  assert three['window']['mode']=='tiktok_extended_14d' and three['quality_candidates']==1,three
  empty=r.save_product({'name':'another pump','keywords':['portable inflator']})
  four=r.scan(empty['id'],now=now)
  assert four['status']=='waiting_source' and four['auto_tiktok_source_connected'] is False,four
  # API model selection: no payment; existing projects preserve the selected model
  ctx=us_marketing.context();doc={'market_country':'US','as_of_us':ctx['as_of_us'],'season':ctx['season'],
   'seasonal_fit_cn':'美国季节适用性待核实','hook_en':'Need a portable camping pump?',
   'hook_zh':'需要便携露营气泵吗？',
   'script_segments':[{'duration_s':5,'narration_en':'Compact air pump for your camping bag.',
     'narration_zh':'适合放进露营背包的小气泵。','visual_cn':'展示经过确认的产品'}]}
  r.ensure_schema()
  with db.tx() as c:c.execute('''INSERT INTO radar_scripts(id,product_id,source_ids_json,model,prompt_hash,status,content_json,created_at,updated_at)
 VALUES(?,?,?,?,?,?,?,?,?)''',('test-script',pid,'[]','test-model','test-hash','completed',json.dumps({'script':doc}),now,now))
  job=cp.from_script('test-script',video_model='viduq3-turbo')
  assert job['model']=='viduq3-turbo'
  job2=cp.from_script('test-script',video_model='minimax-video-h3')
  assert job2['model']=='viduq3-turbo','Duplicate clicks must not replace model'
  job3=cp.select_model({'id':job['id'],'model':'minimax-video-h3'})
  assert job3['model']=='minimax-video-h3'
  assert cp._actual_matches('minimax-video-h3:stable',job3['model'])
  assert not cp._actual_matches('doubao-seedance-2-5-260628:stable',job3['model'])
  cp.select_model({'id':job['id'],'model':'viduq3-turbo'})
  cp.approve_script({'id':job['id'],'confirm':True,'script':doc})
  assert cp._get(job['id'])['state']=='await_h3_test', 'New workflow must skip the redundant concept approval'
  activated=cp.activate_model(job['id'])
  assert activated['paid_request_submitted'] is False and 'viduq3' in activated['effective_model'],activated
  prompt=cp.test_handoff(job['id'])
  assert prompt['model']=='viduq3-turbo' and prompt['no_paid_generation_performed'] is True
  try:cp.select_model({'id':job['id'],'model':'unknown-model'});assert False
  except ValueError:pass
  print('PASS: TikTok-only import, 7/14d fallback, no cross-platform sources, honest no-provider state, model dropdown persistence, no paid requests')
if __name__=='__main__':main()
