"""Offline HTTP preview: mode persisted, original requires video, no paid task."""
import json,threading,urllib.request,urllib.error,sys,tempfile
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app import Handler
from core import config,db,smart_analysis,media_preflight

def run():
 with tempfile.TemporaryDirectory(prefix='audio_preview_') as td,patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(smart_analysis,'analyze_references',return_value={'used':False,'ok':True}),patch.object(smart_analysis,'augment_prompt',side_effect=lambda p,analysis:p),patch.object(media_preflight,'preview_specs',return_value={'ok':True,'warnings':[]}):
  db.init_db()
  sample=Path(td)/'video.mp4';sample.write_bytes(b'fake sample for metadata-only preview')
  db.execute('INSERT INTO assets(id,original_name,stored_name,kind,mime,path,sha256,size_bytes,duration,width,height,fps,has_audio,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',('aud-video','video.mp4','video.mp4','video','video/mp4',str(sample),'a'*64,50,5,1080,1920,30,1,db.now()))
  server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  root=f'http://127.0.0.1:{server.server_address[1]}'
  def call(body):
   req=urllib.request.Request(root+'/api/video/direct-preview',json.dumps(body).encode(),{'Content-Type':'application/json'},method='POST')
   try:
    with urllib.request.urlopen(req,timeout=20) as res:return res.status,json.load(res)
   except urllib.error.HTTPError as err:return err.code,json.load(err)
  try:
   common={'prompt':'US camping sports demo, natural motion','video_mode':'text','asset_ids':[], 'ratio':'9:16','duration':5,'resolution':'720p','video_model':'auto','routing_mode':'auto'}
   status,bad=call({**common,'audio_mode':'original'})
   assert status==409 and '参考视频' in bad['error'],bad
   status,silent=call({**common,'audio_mode':'silent'})
   assert status==200 and silent['data']['audio_mode']=='silent',silent
   tok=silent['data']['preview_token'];saved=db.one('SELECT payload_json FROM generation_previews WHERE token=?',(tok,));payload=json.loads(saved['payload_json'])
   assert payload['generate_audio'] is False and payload['audio_mode']=='silent'
   status,model=call({**common,'audio_mode':'model'})
   assert status==200 and model['data']['audio_mode']=='model',model
   status,orig=call({**common,'audio_mode':'original','asset_ids':['aud-video'],'video_mode':'reference'})
   assert status==200,orig
   payload=json.loads(db.one('SELECT payload_json FROM generation_previews WHERE token=?',(orig['data']['preview_token'],))['payload_json'])
   assert payload['audio_mode']=='original' and payload['audio_source_asset_id']=='aud-video' and payload['generate_audio'] is False,payload
   assert not db.all_("SELECT * FROM tasks WHERE kind='video'")
   print('PASS: HTTP three audio modes; verified original source id, persisted payload and zero paid calls')
  finally:server.shutdown();server.server_close();thread.join(3)
if __name__=='__main__':run()
