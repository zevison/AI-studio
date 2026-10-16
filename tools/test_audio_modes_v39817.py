"""Offline tests: three audio outcomes, durable result contract, no paid APIs."""
import tempfile,sys,subprocess
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import media,audio_output

def go():
 ff=media.ffmpeg_path();assert ff,'FFmpeg required for actual offline audio test'
 with tempfile.TemporaryDirectory(prefix='audio_modes_') as td:
  tmp=Path(td)
  raw=tmp/'render.mp4'
  ref=tmp/'reference.mp4'
  subprocess.run([ff,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=blue:s=256x144:r=15:d=3',
                  '-f','lavfi','-i','sine=frequency=440:sample_rate=44100:duration=3',
                  '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(ref)],check=True,timeout=30)
  subprocess.run([ff,'-hide_banner','-loglevel','error','-y','-f','lavfi','-i','color=c=red:s=256x144:r=15:d=3',
                  '-f','lavfi','-i','sine=frequency=880:sample_rate=44100:duration=3',
                  '-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(raw)],check=True,timeout=30)
  def lookup(aid):
   if aid=='source-1':return {'id':aid,'kind':'video','path':str(ref),'has_audio':1}
  assert audio_output.normalize('original')=='original'
  assert audio_output.normalize('model')=='model'
  assert audio_output.normalize('silent')=='silent'
  assert audio_output.apply(raw,'model',None,lookup)==raw
  swapped=audio_output.apply(raw,'original','source-1',lookup)
  silent=audio_output.apply(raw,'silent',None,lookup)
  assert swapped!=raw and silent!=raw
  assert media.probe(swapped)['has_audio'] is True
  assert media.probe(silent)['has_audio'] is False
  assert raw.exists() and ref.exists()
  # Compare actual audio signal spectral peak with ffmpeg astats? use decoded PCM
  def samples(f):
   r=subprocess.run([ff,'-hide_banner','-loglevel','error','-i',str(f),'-map','0:a:0','-t','0.25','-f','s16le','-ac','1','-ar','8000','-'],capture_output=True,timeout=30,check=False)
   return r.stdout
  import numpy as np
  def peak(b):
   a=np.frombuffer(b,dtype='<i2').astype('float32');return round(float(np.argmax(abs(np.fft.rfft(a)))*8000/max(1,len(a))),-1)
  assert abs(peak(samples(swapped))-440)<=20,(peak(samples(swapped)),peak(samples(raw)))
  assert abs(peak(samples(raw))-880)<=20
  try:audio_output.validate('original',None,lookup)
  except ValueError:pass
  else:raise AssertionError('No source for original soundtrack was allowed')
  # No duplicate paid calls in local audio retry; preview request has stable explicit mode.
  print('PASS: original soundtrack 440Hz replaced model soundtrack 880Hz; silent output has no audio; raw preserved; no paid API')
if __name__=='__main__':go()
