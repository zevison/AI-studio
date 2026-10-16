"""No-pay regression of simplified model selection and preserved source assets."""
import sys, tempfile, json, threading, urllib.request, urllib.error
from pathlib import Path
from html.parser import HTMLParser
from unittest.mock import patch
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,settings,intelligent_router
from app import Handler

class Elements(HTMLParser):
 def __init__(self):super().__init__();self.ids=[]
 def handle_starttag(self,tag,attrs):
  d=dict(attrs)
  if 'id' in d:self.ids.append(d['id'])

def main():
 parsed=Elements();parsed.feed((ROOT/'static/index.html').read_text(encoding='utf8'))
 forbidden={'videoTaskType','videoRoutingMode','videoMotionFidelity','videoApproxConsent','replicaMotionFidelity','replicaApproxConsent','replicaRoutingMode','videoResetAuto','videoRouteStatus'}
 assert not forbidden.intersection(parsed.ids),forbidden.intersection(parsed.ids)
 assert {'videoModelChoice','replicaModelChoice','videoResolution','replicaResolution'}.issubset(parsed.ids)
 js=(ROOT/'static/app.js').read_text(encoding='utf8')
 assert '音频自动处理' in (ROOT/'static/index.html').read_text(encoding='utf8')
 for k in ['#videoTaskType','#videoRoutingMode','#videoMotionFidelity','#videoApproxConsent']:
  assert k not in js,k
 with tempfile.TemporaryDirectory(prefix='simple39820_') as td,patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(db,'DB_PATH',Path(td)/'studio.db'):
  db.init_db()
  def reject_auto(*args,**kwargs):raise AssertionError('removed intelligent_router.choose invoked unexpectedly')
  svr=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=svr.serve_forever,daemon=True);thread.start()
  base=f'http://127.0.0.1:{svr.server_address[1]}'
  def post(path,data):
   req=urllib.request.Request(base+path,data=json.dumps(data).encode('utf8'),headers={'Content-Type':'application/json'},method='POST')
   try:
    with urllib.request.urlopen(req,timeout=12) as r:return r.status,json.load(r)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  try:
   with patch.object(intelligent_router,'choose',reject_auto):
    common={'prompt':'Show a portable air pump outdoors','asset_ids':[],'video_mode':'text','duration':5,'resolution':'720p','ratio':'9:16','audio_mode':'silent'}
    code,resp=post('/api/video/direct-preview',common)
    assert code==200,resp
    assert resp['data']['router_decision'] is None
    assert resp['data']['model'].startswith('doubao-seedance-2-5')
    code,other=post('/api/video/direct-preview',{**common,'video_model':'doubao-seedance-2-5-260628'})
    assert code==200,other
    assert other['data']['model'].startswith('doubao-seedance-2-5')
    assert settings.get_all()['video_base_model']=='doubao-seedance-2-5-260628'
   count=db.one('SELECT COUNT(*) AS n FROM generation_previews')['n']
   assert count==2,count
   assert not db.all_('SELECT * FROM tasks WHERE kind="video"')
   print('PASS: UI removes routing controls; API previews via direct chosen/global model; no intelligent-router call; existing audio and resolution controls; zero paid calls')
  finally:svr.shutdown();svr.server_close();thread.join(3)
if __name__=='__main__':main()
