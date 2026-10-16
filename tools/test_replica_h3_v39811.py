"""Mock H3 reference recreation via browser HTTP API: never makes a paid network call."""
from __future__ import annotations
import tempfile,json,threading,urllib.request,urllib.error,sys
from unittest.mock import patch
from pathlib import Path
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,openlux,public_assets,media_preflight
from app import Handler

def run():
 with tempfile.TemporaryDirectory(prefix='h3_replica_') as td,patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(openlux,'list_models',return_value=['minimax-video-h3']),patch.object(openlux,'request_json') as api,patch.object(media_preflight,'preview_specs',return_value={'ok':True,'warnings':[]}):
  db.init_db();p=Path(td)/'ref.mp4';p.write_bytes(b'fake mp4 - no real video decoding needed in this mock')
  db.execute('INSERT INTO assets(id,original_name,stored_name,kind,mime,path,sha256,size_bytes,duration,width,height,fps,has_audio,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('asset1','ref.mp4','ref.mp4','video','video/mp4',str(p),'a'*64,100,14.8,1080,1920,30,True,db.now()))
  wf={'duration':14.8,'instruction':'Match original gestures in US TikTok ad','analysis':{},'execution_blueprint':{'goal':'replicate motions','generation_strategy':{'label':'reference reproduction'}}}
  db.execute('INSERT INTO workflows(version_id,group_id,parent_version_id,reference_asset_id,product_asset_ids_json,instruction,workflow_json,confirmed,created_at) VALUES(?,?,?,?,?,?,?,?,?)',('w1','g1',None,'asset1','[]','Reference video',db.dumps(wf),1,db.now()))
  s=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=s.serve_forever,daemon=True);t.start()
  base='http://127.0.0.1:'+str(s.server_address[1])
  def http(path,data):
   req=urllib.request.Request(base+path,json.dumps(data).encode(),{'Content-Type':'application/json'},method='POST')
   try:
    with urllib.request.urlopen(req,timeout=8) as rr:return rr.status,json.load(rr)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  def fake_prepare(specs,**kw):
   return {'ok':True,'specs':[dict(sp,asset=dict(sp['asset'],remote_url='https://example.org/ref.mp4')) for sp in specs],'auto_fixes':[]}
  try:
   code,pv=http('/api/video/preview',{'workflow_version_id':'w1','video_model':'minimax-video-h3'})
   assert code==200,pv
   d=pv['data']; assert d['mode'].startswith('H3 参考重制'),d
   tok=d['preview_token']
   api.return_value={'task_id':'h3-task-123','status':'queued'}
   with patch.object(public_assets,'prepare_specs',side_effect=fake_prepare):
    code,rs=http('/api/video/generate',{'preview_token':tok})
   assert code==200,rs
   assert rs['data']['actual_request_model']=='MiniMax-H3',rs
   assert api.call_args.args[1]=='/minimax/v2/video_generation',api.call_args.args
   payload=api.call_args.args[2]
   assert payload['content'][1]['video_url']['url'].startswith('https://'),payload
   assert payload['content'][1]['role']=='reference_video'
   print('PASS: H3 replica preview -> provider-reference POST -> persisted task, mock/no charge')
  finally:s.shutdown();s.server_close()
if __name__=='__main__':run()
