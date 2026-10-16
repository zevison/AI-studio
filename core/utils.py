from __future__ import annotations
import hashlib, json, mimetypes, os, re, urllib.parse
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, chunk=1024*1024) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        while True:
            b=f.read(chunk)
            if not b: break
            h.update(b)
    return h.hexdigest()


def stable_json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def fingerprint(obj) -> str:
    return hashlib.sha256(stable_json(obj).encode('utf-8')).hexdigest()


def safe_name(name: str) -> str:
    name = os.path.basename(name or 'file')
    name = re.sub(r'[^\w.\-()\u4e00-\u9fff]+', '_', name)
    return name[:120] or 'file'


def guess_kind(filename: str, mime: str='') -> str:
    filename=os.fspath(filename)
    mime = str(mime) if mime else mime
    mime = mime or mimetypes.guess_type(filename)[0] or ''
    if mime.startswith('image/'): return 'image'
    if mime.startswith('video/'): return 'video'
    if mime.startswith('audio/'): return 'audio'
    ext=Path(filename).suffix.lower()
    if ext in {'.png','.jpg','.jpeg','.webp','.gif'}: return 'image'
    if ext in {'.mp4','.mov','.m4v','.webm','.avi','.mkv'}: return 'video'
    if ext in {'.mp3','.wav','.m4a','.aac','.flac','.ogg'}: return 'audio'
    return 'file'


def parse_multipart(body: bytes, content_type: str):
    m=re.search(r'boundary=(?:"([^"]+)"|([^;]+))', content_type)
    if not m: raise ValueError('缺少 multipart boundary')
    boundary=(m.group(1) or m.group(2)).encode()
    parts=[]
    for raw in body.split(b'--'+boundary):
        raw=raw.strip(b'\r\n')
        if not raw or raw==b'--': continue
        if raw.endswith(b'--'): raw=raw[:-2]
        head, sep, data=raw.partition(b'\r\n\r\n')
        if not sep: continue
        data=data.rstrip(b'\r\n')
        headers={}
        for line in head.decode('utf-8','replace').split('\r\n'):
            if ':' in line:
                k,v=line.split(':',1); headers[k.lower().strip()]=v.strip()
        cd=headers.get('content-disposition','')
        nm=re.search(r'name="([^"]+)"',cd)
        fn=re.search(r'filename="([^"]*)"',cd)
        parts.append({'name':nm.group(1) if nm else '', 'filename':fn.group(1) if fn else None,
                      'content_type':headers.get('content-type','application/octet-stream'), 'data':data})
    return parts


def json_bytes(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode('utf-8')


def find_url(obj):
    """Best-effort URL extraction across common OpenAI/OpenLux task payloads."""
    if isinstance(obj, str):
        if obj.startswith(('http://','https://')): return obj
        return None
    if isinstance(obj, list):
        for x in obj:
            u=find_url(x)
            if u: return u
        return None
    if isinstance(obj, dict):
        priority=('video_url','url','output_url','file_url','image_url','download_url')
        for k in priority:
            if k in obj:
                u=find_url(obj[k])
                if u: return u
        for k in ('content','data','output','result','results','artifacts','images','videos'):
            if k in obj:
                u=find_url(obj[k])
                if u: return u
    return None
