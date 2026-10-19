"""Enforce output semantics that the live vendor did not preserve reliably."""
import hashlib
import json
from pathlib import Path
import os
import subprocess
import tempfile


def sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk:=stream.read(1024*1024):result.update(chunk)
    return result.hexdigest()


def enforce_audio(path, payload, *, ffmpeg, ffprobe):
    path=Path(path)
    if path.is_symlink() or not path.is_file():raise ValueError('Regular collected MP4 required')
    environment={k:v for k,v in os.environ.items() if not any(x in k.upper() for x in ('KEY','TOKEN','SECRET','PASSWORD'))}
    def probe(file):
        process=subprocess.run([str(ffprobe),'-v','error','-show_streams','-show_format','-of','json',str(file)],
            capture_output=True,encoding='utf8',timeout=20,env=environment,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode or len(process.stdout)>500_000:raise RuntimeError('Output policy ffprobe failed')
        doc=json.loads(process.stdout)
        if not any(s.get('codec_type')=='video' for s in doc.get('streams',[])):raise RuntimeError('No playable video stream')
        return doc
    before=probe(path)
    if payload.get('generate_audio') is not False or not any(s.get('codec_type')=='audio' for s in before['streams']):
        return {'changed':False,'sha256':sha(path)}
    original=sha(path)
    fd,name=tempfile.mkstemp(prefix='.no-audio-',suffix='.mp4',dir=path.parent);os.close(fd)
    temporary=Path(name)
    try:
        process=subprocess.run([str(ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-y','-i',str(path),
            '-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(temporary)],
            capture_output=True,timeout=60,env=environment,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode:raise RuntimeError('Local removal of unsolicited vendor audio failed')
        after=probe(temporary)
        first=next(s for s in before['streams'] if s.get('codec_type')=='video')
        second=next(s for s in after['streams'] if s.get('codec_type')=='video')
        if (any(s.get('codec_type')=='audio' for s in after['streams']) or
            any(first.get(k)!=second.get(k) for k in ('width','height','nb_frames','codec_name'))):
            raise RuntimeError('Audio policy changed the video or retained an audio track')
        receipt={'changed':True,'operation':'remove_unsolicited_audio_video_stream_copy',
                 'input_sha256':original,'output_sha256':sha(temporary),'paid_calls':0}
        os.replace(temporary,path)
        path.with_suffix('.policy.json').write_text(json.dumps(receipt,indent=2),encoding='utf8')
        return receipt
    finally:temporary.unlink(missing_ok=True)
