"""No-network test of per-task model binding, global model settings and paid-gate."""
import sys, tempfile, threading, json, urllib.request, urllib.error
from pathlib import Path
from unittest.mock import patch
from http.server import ThreadingHTTPServer
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import db,config,settings,creative_production,openlux
from app import Handler

def run():
 with tempfile.TemporaryDirectory(prefix='modelchoice_') as td, patch.object(config,'DB_PATH',Path(td)/'studio.db'),patch.object(db,'DB_PATH',Path(td)/'studio.db'),patch.object(openlux,'list_models',return_value=['viduq3-turbo','minimax-video-h3']):
  db.init_db()
  svr=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  thread=threading.Thread(target=svr.serve_forever,daemon=True);thread.start()
  base=f'http://127.0.0.1:{svr.server_address[1]}'
  def req(url,d=None):
   u=urllib.request.Request(base+url,data=json.dumps(d).encode() if d is not None else None,headers={'Content-Type':'application/json'} if d is not None else {},method='POST' if d is not None else 'GET')
   try:
    with urllib.request.urlopen(u,timeout=8) as resp: return resp.status,json.load(resp)
   except urllib.error.HTTPError as e:return e.code,json.load(e)
  try:
   st=200 and settings.get_all();assert st['video_base_model']=='doubao-seedance-2-5-260628',st['video_base_model']
   code,r=req('/api/settings',{'video_base_model':'viduq3-turbo'});assert code==200,r
   assert settings.get_all()['video_base_model']=='viduq3-turbo'
   code,r=req('/api/video/direct-preview',{'prompt':'Camping air pump','video_model':'viduq3-turbo','generate_audio':False});assert code==200,r
   assert r['data']['model']=='viduq3-turbo'
   code,r=req('/api/video/direct-preview',{'prompt':'Camping air pump','video_model':'minimax-video-h3','resolution':'2K','generate_audio':False});assert code==200,r
   assert r['data']['model']=='minimax-video-h3'
   code,r=req('/api/video/direct-preview',{'prompt':'Camping air pump','video_model':'wan-3.0'});assert code==409,r
   code,r=req('/api/video/direct-preview',{'prompt':'Camping air pump','video_model':'doubao-seedance-2-5-260628','video_mode':'text','duration':5,'ratio':'9:16','resolution':'720p'});
   assert code==200,r
   token=r['data']['preview_token']; assert r['data']['model'].startswith('doubao-seedance-2-5-260628'),r
   row=db.one('SELECT payload_json FROM generation_previews WHERE token=?',(token,));assert 'doubao-seedance-2-5-260628' in row['payload_json']
   assert settings.get_all()['video_base_model']=='viduq3-turbo','task-local selection modified global settings'
   assert not db.all_('SELECT * FROM tasks WHERE kind="video"')
   code,r=req('/api/settings',{'video_base_model':'doubao-seedance-2-5-260628'});assert code==200,r
   assert settings.get_all()['video_base_model']=='doubao-seedance-2-5-260628'
   print('PASS: global model saved, task override isolated, Vidu/H3 independent adapters and Wan safely blocked, Seedance preview binds actual model, no paid jobs')
  finally:svr.shutdown();svr.server_close();thread.join(2)
if __name__=='__main__':run()
