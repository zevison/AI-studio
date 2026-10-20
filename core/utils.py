from __future__ import annotations
import hashlib, json, mimetypes, os, re, urllib.parse
from email import policy
from email.parser import BytesParser
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
    # MIME framing consumes exactly its CRLF; strip/rstrip corrupts real bytes.
    if '\r' in content_type or '\n' in content_type:raise ValueError('无效 multipart 类型')
    message=BytesParser(policy=policy.default).parsebytes(
        ('Content-Type: '+content_type+'\r\nMIME-Version: 1.0\r\n\r\n').encode('utf-8')+body)
    if message.get_content_type()!='multipart/form-data' or not message.get_boundary():
        raise ValueError('缺少 multipart boundary')
    if message.defects or not message.is_multipart():raise ValueError('multipart 请求不完整')
    parts=[]
    for part in message.iter_parts():
        if part.is_multipart() or part.defects:raise ValueError('不支持嵌套或损坏的 multipart')
        data=part.get_payload(decode=True)
        if not isinstance(data,bytes):raise ValueError('上传内容无效')
        parts.append({'name':part.get_param('name',header='content-disposition') or '',
                      'filename':part.get_filename(),
                      'content_type':part.get('Content-Type','application/octet-stream'), 'data':data})
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
