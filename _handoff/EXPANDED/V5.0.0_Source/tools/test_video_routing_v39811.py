"""End-to-end local HTTP mock: no OpenLux or paid provider request."""
import json,sys,tempfile,threading,urllib.request,urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import db,config,settings,openlux
from app import Handler

def test():
 with tempfile.TemporaryDirectory(prefix='v39811_') as td,patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(openlux,'list_models',return_value=['viduq3-turbo','minimax-video-h3']),patch.object(openlux,'request_json') as api:
  db.init_db()
  srv=ThreadingHTTPServer(('127.0.0.1',0),Handler);t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
  base='http://127.0.0.1:%s'%srv.server_address[1]
  def http(path,data):
   raw=json.dumps(data).encode();req=urllib.request.Request(base+path,data=raw,headers={'Content-Type':'application/json'},method='POST')
   try:
    with urllib.request.urlopen(req,timeout=8) as r:return r.status,json.load(r)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  try:
   cases=[('viduq3-turbo','720p','/ent/v2/text2video','viduq3-turbo'),('minimax-video-h3','2K','/minimax/v2/video_generation','MiniMax-H3')]
   for model,res,endpoint,actual in cases:
    code,result=http('/api/video/direct-preview',{'prompt':'TikTok US product demo','video_model':model,'video_mode':'text','duration':5,'ratio':'9:16','resolution':res,'generate_audio':False})
    assert code==200,result
    token=result['data']['preview_token']
    api.return_value={'task_id':'provider_'+model,'status':'queued'}
    code,result=http('/api/video/direct-generate',{'preview_token':token})
    assert code==200,result
    assert result['data']['actual_request_model']==actual,result
    method,path,payload=api.call_args.args[:3]
    assert (method,path)==('POST',endpoint),(method,path)
    assert payload['model']==actual,payload
    assert (isinstance(payload.get('content'),list) if model=='minimax-video-h3' else payload.get('aspect_ratio')=='9:16'),payload
    tid=result['data']['task']['id']; row=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
    req=db.loads(row['request_json'],{})
    assert req['_provider_adapter']==model and req['_query_path'].startswith('/'),req
   code,result=http('/api/video/direct-preview',{'prompt':'bad','video_model':'wan-3.0'})
   assert code==409,result
   assert api.call_count==2,'should make exactly one POST per authorized native model, no other paid requests'
   print('PASS: real app preview -> authorized provider payload -> persistent query route; no external request')
  finally:srv.shutdown();srv.server_close()
if __name__=='__main__':test()
