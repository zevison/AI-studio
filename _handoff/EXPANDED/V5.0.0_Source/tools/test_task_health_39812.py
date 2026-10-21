"""Fully isolated regression: crash recovery, counts, video provider task ID, read-only diagnostics."""
import sys,time,json,tempfile,threading,urllib.request,hashlib
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import db,config,task_observability as obs,hot_radar,product_memory
from app import Handler,active_video_count,global_task_center,task_to_public


def main():
 with tempfile.TemporaryDirectory(prefix='taskhealth_39812_') as td,patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(config,'DATA_DIR',Path(td)),patch.object(product_memory,'SOURCE_DIR',Path(td)/'knowledge'):
  db.init_db();hot_radar._schema_initialized=False;product_memory._initialized=False;obs._initialized=False
  product_memory.ensure_schema();obs.ensure_schema()
  now=time.time()-460
  with db.tx() as c:
   c.execute("INSERT INTO product_analysis_runs(id,product_id,request_hash,model,status,estimated_usd,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",('product1','p1','fpa','gpt-6.1-sol','running',0.11,now,now))
   c.execute("INSERT INTO radar_scripts(id,product_id,source_ids_json,model,prompt_hash,status,estimated_cost_usd,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",('script1','p1','[]','gpt-6.1-sol','fpa','running',0.12,now,now))
   c.execute("INSERT INTO tasks(id,kind,fingerprint,status,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",('image1','image','im1','running','{}',now,now))
   c.execute("INSERT INTO tasks(id,kind,fingerprint,status,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",('video1','video','vid1','running',json.dumps({'model':'viduq3-turbo'}),now,now))
   c.execute("INSERT INTO tasks(id,kind,fingerprint,status,provider_task_id,request_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",('video2','video','vid2','running','vendor_task_2',json.dumps({'model':'minimax-video-h3','_provider_adapter':'minimax-video-h3','_query_path':'/provider/query/{task_id}'}),now,now))
  before=db.one('SELECT COUNT(*) n FROM usage_events')['n']
  r=obs.recover_interrupted_on_start();assert r['reconciled']==4,r
  assert db.one('SELECT status FROM tasks WHERE id=?',('video1',))['status']=='submission_unknown'
  assert db.one('SELECT status FROM tasks WHERE id=?',('video2',))['status']=='running'
  assert active_video_count(refresh=False)==1,'unknown-without-task-id must not occupy a concurrency slot'
  assert global_task_center()['active_count']==1
  assert db.task_recent_by_fp('vid1')['status']=='submission_unknown','dedupe protection remains'
  # claim must still reuse unknown-without-ID tasks for the same fingerprint
  expires=time.time()+300
  with db.tx() as c:
   c.execute('INSERT INTO generation_previews(token,fingerprint,payload_json,expires_at,created_at) VALUES(?,?,?,?,?)',('preview_1','vid1','{}',expires,time.time()))
  result=db.claim_video_submission('preview_1',20,1)
  assert result['state']=='reused' and result['task']['id']=='video1',result
  dashboard=obs.overview(20)
  assert dashboard['attention_count']>=4
  assert len(dashboard['tasks'])>=5
  recovered={e['id']:e for e in dashboard['tasks']}
  assert '待核对' in recovered['product1']['phase']
  assert recovered['video2']['provider_task_id']=='vendor_task_2'
  assert recovered['video2']['model']=='minimax-video-h3'
  assert recovered['video2']['stalled']
  assert recovered['video1']['safe_to_auto_resubmit'] is False
  obs.record('product','product1','等待响应','submission_unknown','Bearer sk-DO-NOT-LEAK')
  diag=obs.diagnostics()
  rendered=json.dumps(diag,ensure_ascii=False)
  assert 'sk-DO-NOT-LEAK' not in rendered
  # Read-only HTTP never calls OpenLux
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   for route in ('/api/task-health/overview','/api/task-health/diagnostics'):
    with urllib.request.urlopen(f'http://127.0.0.1:{server.server_address[1]}{route}',timeout=8) as response:
     value=json.load(response)
     assert value['ok'] and value['data']
  finally:server.shutdown();server.server_close();thread.join(3)
  assert db.one('SELECT COUNT(*) n FROM usage_events')['n']==before
  assert obs.recover_interrupted_on_start()['reconciled']==0,'second restart should be idempotent'
  print('PASS 39812: all modules crash recovery; no-id unknown not in capacity; dedupe protected; provider-ID video kept running; diagnostics redacted; HTTP read-only; no paid calls')
if __name__=='__main__':main()
