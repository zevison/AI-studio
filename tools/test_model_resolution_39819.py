"""Read-only model resolution menus and no-fee real localhost preview paths."""
import json,sys,tempfile,threading,urllib.request,urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import video_adapters as adapter,db,config
from app import Handler


def main():
 menus=adapter.resolution_menus()
 assert [r['value'] for r in menus[adapter.VIDU]['options']]==['540p','720p','1080p']
 assert {'768P','2K'}==set(menus[adapter.H3]['native_levels'])
 assert next(r for r in menus[adapter.H3]['options'] if r['value']=='1080p')['native']=='2K'
 assert [r['value'] for r in menus[adapter.SEED]['options']]==['480p','720p','1080p','2K']
 with tempfile.TemporaryDirectory(prefix='resmodels39819_') as temp,patch.object(db,'DB_PATH',Path(temp)/'studio.db'),patch.object(config,'DB_PATH',Path(temp)/'studio.db'),patch.object(adapter,'model_diagnostic',return_value={'available':True,'note':'offline stub'}):
  db.init_db();server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
  th=threading.Thread(target=server.serve_forever,daemon=True);th.start()
  base=f'http://127.0.0.1:{server.server_address[1]}'
  def request(path,body=None):
   req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'} if body is not None else {},method='POST' if body is not None else 'GET')
   try:
    with urllib.request.urlopen(req,timeout=18) as response:return response.status,json.load(response)
   except urllib.error.HTTPError as exc:return exc.code,json.load(exc)
  try:
   status,body=request('/api/video/resolution-options')
   assert status==200 and body['ok'] and body['data']['models'][adapter.H3]['options'],body
   common={'prompt':'Product demonstration video, US TikTok','asset_ids':[],'video_mode':'text','ratio':'9:16','duration':5,'audio_mode':'silent'}
   # Vidu's 540P must not get rejected by the legacy shared resolution whitelist.
   status,vidu=request('/api/video/direct-preview',{**common,'video_model':adapter.VIDU,'resolution':'540p','task_type':'bulk'})
   assert status==200,(status,vidu)
   assert vidu['data']['resolution']=='540p' and vidu['data']['model']==adapter.VIDU
   status,h3native=request('/api/video/direct-preview',{**common,'video_model':adapter.H3,'resolution':'768P','task_type':'bulk'})
   assert status==200,(status,h3native)
   assert h3native['data']['provider_resolution']=='768P'
   status,h3export=request('/api/video/direct-preview',{**common,'video_model':adapter.H3,'resolution':'1080p','task_type':'hq'})
   assert status==200,(status,h3export)
   assert h3export['data']['resolution']=='1080p' and h3export['data']['provider_resolution']=='2K'
   assert not db.all_("SELECT * FROM tasks WHERE kind='video'")
  finally:server.shutdown();server.server_close();th.join(3)
 print('PASS: backend model menus, 540P Vidu, native H3 768P, H3 1080P maps to 2K, zero paid calls')
if __name__=='__main__':main()
