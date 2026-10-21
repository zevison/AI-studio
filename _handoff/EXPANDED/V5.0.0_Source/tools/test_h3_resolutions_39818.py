"""H3 native resolution mapping + local 1080 export + no paid requests."""
from pathlib import Path
import sys,tempfile,subprocess,json,urllib.request,urllib.error,threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import config,db,video_adapters as a,intelligent_router as router,audio_output,media
from app import Handler


def run():
 for ui,render in [('480p','768P'),('720p','768P'),('1080p','2K'),('2K','2K')]:
  plan=a.h3_resolution_plan(ui);assert plan['provider_resolution']==render
  r=router.choose({'task_type':'hq' if ui in ('1080p','2K') else 'bulk','mode':'text',
                   'prompt':'Test product video','duration':5,'ratio':'9:16','resolution':ui,
                   'images':0,'videos':0,'audios':0,'native_audio':False},override=a.H3)
  assert r['selected_model']==a.H3,r
  path,query,payload=a.build(a.H3,'product video',[],'9:16',render,5,False,'https://example.org')
  assert payload['resolution']==render,(ui,payload)
  print('H3 mapping',ui,'->',render)
 with tempfile.TemporaryDirectory(prefix='h3_export_') as td:
  tmp=Path(td)
  ff=media.ffmpeg_path();assert ff
  raw=tmp/'h3_native.mp4'; source=tmp/'source.mp4'
  for name,freq in ((raw,880),(source,440)):
   subprocess.run([ff,'-y','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=black:s=1200x2134:r=5:d=1.4',
    '-f','lavfi','-i',f'sine=frequency={freq}:sample_rate=44100:duration=1.4','-c:v','libx264','-threads','2',
    '-preset','ultrafast','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(name)],check=True,timeout=45)
  get=lambda x:{'id':x,'kind':'video','path':str(source),'has_audio':1}
  original=audio_output.apply(raw,'original','ref',get,target_resolution='1080p')
  mute=audio_output.apply(raw,'silent',None,get,target_resolution='1080p')
  model=audio_output.apply(raw,'model',None,get,target_resolution='1080p')
  for x,has_audio in [(original,True),(mute,False),(model,True)]:
   info=media.probe(x);assert min(info['width'],info['height'])==1080 and info['has_audio']==has_audio,(x,info)
  assert raw.exists() and media.probe(raw)['width']==1200
  with patch.object(config,'DB_PATH',tmp/'studio.db'),patch.object(db,'DB_PATH',tmp/'studio.db'),patch.object(a,'model_diagnostic',return_value={'available':True,'note':'mock directory'}):
   db.init_db();svr=ThreadingHTTPServer(('127.0.0.1',0),Handler);thr=threading.Thread(target=svr.serve_forever,daemon=True);thr.start()
   def preview(ui):
    url=f'http://127.0.0.1:{svr.server_address[1]}/api/video/direct-preview'
    payload={'prompt':'Simple product shot','video_model':a.H3,'video_mode':'text','ratio':'9:16',
             'resolution':ui,'duration':5,'audio_mode':'silent','task_type':'hq' if ui in ('1080p','2K') else 'bulk'}
    req=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'},method='POST')
    with urllib.request.urlopen(req,timeout=15) as response:result=json.load(response)
    assert result['ok'],result
    dat=result['data']; assert dat['resolution']==ui and dat['provider_resolution']==a.h3_resolution_plan(ui)['provider_resolution']
    dbpayload=db.loads(db.one('SELECT payload_json FROM generation_previews WHERE token=?',(dat['preview_token'],))['payload_json'],{})
    assert dbpayload['output_resolution']==ui and dbpayload['resolution']==dat['provider_resolution']
    assert (bool(dbpayload.get('local_export_resolution'))==(ui!='2K'))
    return dat
   try:
    for ui in ('720p','1080p','2K'):preview(ui)
    assert not db.all_('SELECT * FROM tasks WHERE kind="video"')
   finally:svr.shutdown();svr.server_close();thr.join(3)
 print('PASS H3: 768P and 2K valid API tiers; 1080P local downscale + audio original/model/silent; preview shows tier and no paid calls')
if __name__=='__main__':run()
