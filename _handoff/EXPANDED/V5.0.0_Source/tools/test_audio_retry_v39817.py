"""Offline local retry uses previously generated media, never requests paid creation."""
from __future__ import annotations
import tempfile,sys,subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from core import db,config,media
from app import retry_audio_output,task_to_public

def run():
 with tempfile.TemporaryDirectory(prefix='retry_audio_') as td,patch.object(db,'DB_PATH',Path(td)/'db.sqlite'),patch.object(config,'DB_PATH',Path(td)/'db.sqlite'):
  db.init_db();ff=media.ffmpeg_path();raw=Path(td)/'paid_render.mp4';ref=Path(td)/'source.mp4'
  for dst,tone in [(raw,880),(ref,440)]:
   subprocess.run([ff,'-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=red:s=256x144:r=15:d=2',
    '-f','lavfi','-i',f'sine=frequency={tone}:duration=2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest','-y',str(dst)],check=True,timeout=30)
  with db.tx() as c:
   c.execute('INSERT INTO assets(id,kind,path,has_audio) VALUES(?,?,?,?)',('ref-video','video',str(ref),1))
   c.execute('INSERT INTO tasks(id,kind,fingerprint,status,request_json,result_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
    ('paid-1','video','fp1','completed',db.dumps({'audio_mode':'original','audio_source_asset_id':'ref-video','model':'doubao-seedance-2-5-260628'}),
      db.dumps({'generated_raw_path':str(raw),'audio_status':'failed','audio_error':'mock failure'}),db.now(),db.now()))
  done=task_to_public(retry_audio_output('paid-1'))
  assert done['status']=='completed' and done['result']['audio_status']=='ready'
  assert done['result'].get('local_path') and Path(done['result']['local_path']).exists()
  assert raw.exists() and media.probe(Path(done['result']['local_path']))['has_audio']
  again=task_to_public(retry_audio_output('paid-1'))
  assert again['result']['local_path']==done['result']['local_path']
  print('PASS: local audio retry from persisted raw video, preserves paid task id, no paid API calls')
if __name__=='__main__':run()
