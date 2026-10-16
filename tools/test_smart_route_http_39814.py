"""Real localhost HTTP handlers + SQLite preview, all provider calls mocked/read-only."""
import json,sys,tempfile,threading,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import db,config,openlux
from app import Handler

def run():
 with tempfile.TemporaryDirectory(prefix='routerhttp_') as td,patch.object(db,'DB_PATH',Path(td)/'db.sqlite'),patch.object(config,'DB_PATH',Path(td)/'db.sqlite'),patch.object(openlux,'list_models',return_value=['viduq3-turbo','MiniMax-H3']):
  db.init_db();srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);thr=threading.Thread(target=srv.serve_forever,daemon=True);thr.start()
  url=f'http://127.0.0.1:{srv.server_address[1]}'
  def call(path,body=None):
   request=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {},method='POST' if body is not None else 'GET')
   try:
    with urllib.request.urlopen(request,timeout=8) as h:return h.status,json.load(h)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  try:
   code,listing=call('/api/video/smart-model-catalog');assert code==200 and len(listing['data']['models'])>=10,listing
   common={'prompt':'US camping air pump demo','video_mode':'text','asset_ids':[],'duration':5,'ratio':'9:16','resolution':'720p','generate_audio':False,'video_model':'auto'}
   code,auto=call('/api/video/direct-preview',{**common,'routing_mode':'auto'})
   assert code==200,auto
   assert auto['data']['router_decision']['selected_model'].startswith('doubao-seedance-2-5')
   code,cheap=call('/api/video/direct-preview',{**common,'routing_mode':'cheap'})
   assert code==200,cheap
   assert cheap['data']['router_decision']['selected_model']=='viduq3-turbo',cheap
   assert cheap['data']['router_decision']['quote']['estimated_total_usd'] is None
   assert cheap['data']['model']=='viduq3-turbo'
   tid=cheap['data']['preview_token'];row=db.one('SELECT payload_json FROM generation_previews WHERE token=?',(tid,))
   payload=json.loads(row['payload_json']);assert payload['router_decision']['selection_reason']
   # Explicitly impossible motion transfer: refuse, not silent fallback.
   code,motion=call('/api/video/direct-preview',{**common,'task_type':'motion'})
   assert code==409 and '动作迁移' in motion['error'],motion
   assert not db.all_("SELECT * FROM tasks WHERE kind='video'")
   print('PASS: catalog HTTP, auto Seedance, cheap Vidu, preview metadata persisted, no motion fallback or paid provider call')
  finally:srv.shutdown();srv.server_close();thr.join(3)
if __name__=='__main__':run()
