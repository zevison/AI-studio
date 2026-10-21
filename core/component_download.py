"""Public, pinned component transport. Mirrors can transport bytes, not choose versions."""
from __future__ import annotations
import hashlib
import http.client
import ipaddress
import os
from pathlib import Path
import re
import socket
import time
import urllib.error
import urllib.request
import uuid
import json
import errno
import shutil
from urllib.parse import urlsplit
from .network_trust import tls_context
from .runtime_layout import native_path


class DownloadCancelled(Exception):
    pass


def digest(path):
    checksum = hashlib.sha256()
    with native_path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def sources_for(row):
    """Only reviewed mappings; upstream SHA remains authoritative even for mutable mirrors."""
    url = row['url']
    if re.fullmatch(r'https://nodejs\.org/dist/v[\d.]+/node-v[\d.]+-win-x64\.zip', url):
        return [url.replace('https://nodejs.org/dist', 'https://cdn.npmmirror.com/binaries/node'), url]
    if re.fullmatch(r'https://storage\.googleapis\.com/chrome-for-testing-public/[\d.]+/win64/chrome-headless-shell-win64\.zip', url):
        return [url.replace('https://storage.googleapis.com/chrome-for-testing-public',
                            'https://cdn.npmmirror.com/binaries/chrome-for-testing'), url]
    match = re.fullmatch(r'https://huggingface\.co/Systran/faster-whisper-small/resolve/[a-f0-9]{40}/(config\.json|model\.bin|tokenizer\.json|vocabulary\.txt)', url)
    if match:
        return ['https://modelscope.cn/models/Systran/faster-whisper-small/resolve/master/' + match[1], url]
    return [url]


def ordered_sources(row,priority='domestic_first'):
    if priority not in {'domestic_first','official_first'}:raise ValueError('组件来源优先级无效')
    urls=sources_for(row)
    return list(reversed(urls)) if priority=='official_first' else urls


def validate_url(url, initial):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443)
            or not parsed.hostname or parsed.fragment):
        raise PermissionError('组件仅允许可信 HTTPS 下载地址')
    if url == initial:
        return
    original = urlsplit(initial).hostname
    host = parsed.hostname
    allowed = {
        'cdn.npmmirror.com': {'cdn.npmmirror.com', 'registry.npmmirror.com'},
        'modelscope.cn': {'modelscope.cn', 'www.modelscope.cn'},
        'github.com': {'release-assets.githubusercontent.com', 'objects.githubusercontent.com'},
        'huggingface.co': {'cdn-lfs.huggingface.co', 'huggingface.co'},
    }
    if host in allowed.get(original, set()):
        return
    if original == 'modelscope.cn' and (host.endswith('.modelscope.cn') or host.endswith('.aliyuncs.com')):
        return
    if original == 'huggingface.co' and host.endswith('.hf.co'):
        return
    raise PermissionError('组件下载重定向超出已审核来源')


class _PublicConnection(http.client.HTTPSConnection):
    def connect(self):
        answers = socket.getaddrinfo(self.host, self.port, type=socket.SOCK_STREAM)
        if not answers or any(not ipaddress.ip_address(row[4][0]).is_global for row in answers):
            raise PermissionError('组件下载地址不是公网地址')
        last = None
        for family, kind, protocol, _, address in answers:
            candidate = socket.socket(family, kind, protocol)
            candidate.settimeout(self.timeout)
            try:
                candidate.connect(address)
                self.sock = self._context.wrap_socket(candidate, server_hostname=self.host)
                return
            except OSError as exc:
                candidate.close()
                last = exc
        raise last or OSError('组件下载连接失败')


def open_source(request, initial):
    validate_url(request.full_url, initial)
    class Handler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_PublicConnection, req, context=self._context)
    class Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            validate_url(newurl, initial)
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    # No credentials, environment proxy, local-network fallback or disabled TLS.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
        Handler(context=tls_context()), Redirect())
    return opener.open(request, timeout=20)


def fetch(row, cache, *, progress=lambda **_: None, cancelled=lambda: False,
          opener=open_source, deadline=1800,source_order='domestic_first',attempts_per_source=2):
    expected, length = row['sha256'], row['bytes']
    if not re.fullmatch('[a-f0-9]{64}', expected) or type(length) is not int or not 0 < length <= 800 * 1024**2:
        raise ValueError('组件必须锁定 SHA-256 和大小')
    cache = native_path(cache)
    target, partial = cache / expected, cache / (expected + '.part')
    metadata=cache/(expected+'.part.json')
    for path in (cache, *cache.parents, target, partial,metadata):
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)():
            raise PermissionError('组件缓存包含链接')
    cache.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.stat().st_size != length or digest(target) != expected:
            target.rename(target.with_name(target.name+'.rejected-'+uuid.uuid4().hex))
            raise PermissionError('已有组件缓存校验失败，原文件已隔离；可点击重试')
        return target
    if partial.exists() and partial.stat().st_size > length:
        raise PermissionError('组件断点文件大小异常，原文件已保留')
    started = time.monotonic()
    errors = []
    if source_order not in {'domestic_first','official_first'} or type(attempts_per_source) is not int or not 1<=attempts_per_source<=3:
        raise ValueError('组件来源优先级或重试次数无效')
    urls=ordered_sources(row,source_order)
    def reset_partial():
        for path in (partial,metadata):
            if path.exists():path.rename(path.with_name(path.name+'.rejected-'+uuid.uuid4().hex))
    def checkpoint(value):
        temporary=cache/(metadata.name+'.'+uuid.uuid4().hex+'.tmp')
        with temporary.open('x',encoding='utf8') as stream:
            json.dump(value,stream);stream.flush();os.fsync(stream.fileno())
        temporary.replace(metadata)
    integrity_failure=False
    for url in urls:
        validate_url(url,url)
        for attempt in range(attempts_per_source):
            if cancelled():raise DownloadCancelled()
            if time.monotonic()-started>deadline:raise TimeoutError('组件下载总时限已到，断点已保留')
            previous={}
            if metadata.is_file():
                try:
                    previous=json.loads(metadata.read_text('utf8'))
                    if (not isinstance(previous,dict) or previous.get('sha256')!=expected or previous.get('bytes')!=length):
                        raise ValueError('断点身份不匹配')
                    if any(not isinstance(previous.get(name,''),str) for name in ('source','etag','last_modified')):
                        raise ValueError('断点实体标识无效')
                except (ValueError,UnicodeError):reset_partial();previous={}
            if previous.get('source') and previous['source']!=url:
                # A different mirror can have a different entity validator.
                # Preserve rather than append an unverifiable mixed response.
                reset_partial();previous={}
            offset=partial.stat().st_size if partial.exists() else 0
            if offset==length:
                if digest(partial)==expected:
                    partial.replace(target);metadata.unlink(missing_ok=True);return target
                reset_partial();offset=0;integrity_failure=True
            if shutil.disk_usage(cache).free<length-offset+512*1024**2:
                raise OSError(errno.ENOSPC,'磁盘空间不足，已保留断点并为主程序保留空间')
            headers={'User-Agent':'SeedanceStudio-Components/1','Accept-Encoding':'identity'}
            if offset:
                headers['Range']=f'bytes={offset}-'
                validator=previous.get('etag') or previous.get('last_modified')
                if validator:headers['If-Range']=validator
            progress(source=urlsplit(url).hostname,downloaded=offset,total=length)
            try:
                with opener(urllib.request.Request(url,headers=headers),url) as response:
                    etag=(response.headers.get('ETag') or '')[:512]
                    modified=(response.headers.get('Last-Modified') or '')[:128]
                    if response.status==206:
                        match=re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)',response.headers.get('Content-Range',''))
                        if (not match or tuple(map(int,(match[1],match[3])))!=(offset,length)
                            or int(match[2])<offset or int(match[2])>=length):
                            raise ValueError('组件断点响应与锁定大小不一致')
                        if offset and ((previous.get('etag') and etag!=previous['etag'])
                            or (not previous.get('etag') and previous.get('last_modified') and modified!=previous['last_modified'])):
                            reset_partial()
                            errors.append(urlsplit(url).hostname+': 实体标识已变化，断点已重置')
                            continue
                    elif response.status==200:
                        if offset:reset_partial()
                        offset=0
                    else:raise OSError('组件 HTTP 状态异常')
                    if response.headers.get('Content-Encoding','identity')!='identity':raise ValueError('组件下载编码不受支持')
                    checkpoint({'sha256':expected,'bytes':length,'source':url,'etag':etag,'last_modified':modified})
                    with partial.open('ab' if offset else 'wb') as outgoing:
                        disk_checked=offset
                        while True:
                            if cancelled():raise DownloadCancelled()
                            if time.monotonic()-started>deadline:raise TimeoutError('组件下载总时限已到')
                            block=response.read(128*1024)
                            if not block:break
                            offset+=len(block)
                            if offset>length:raise ValueError('组件响应超出锁定大小')
                            if offset-disk_checked>=8*1024**2:
                                if shutil.disk_usage(cache).free<512*1024**2:raise OSError(errno.ENOSPC,'磁盘可用空间低于安全预留，下载已停止')
                                disk_checked=offset
                            outgoing.write(block)
                            progress(source=urlsplit(url).hostname,downloaded=offset,total=length)
                        outgoing.flush();os.fsync(outgoing.fileno())
                if offset!=length:raise OSError('组件下载未完成')
                if cancelled():raise DownloadCancelled()
                progress(source='SHA-256',downloaded=length,total=length)
                if digest(partial)!=expected:
                    reset_partial();integrity_failure=True
                    errors.append(urlsplit(url).hostname+': SHA-256 不匹配，已拒绝此来源')
                    break  # Try a separately reviewed mirror, never install these bytes.
                partial.replace(target);metadata.unlink(missing_ok=True);return target
            except (urllib.error.URLError,OSError,http.client.HTTPException) as exc:
                if isinstance(exc,PermissionError) or getattr(exc,'errno',None) in {errno.ENOSPC,errno.EACCES,errno.EPERM}:raise
                errors.append(urlsplit(url).hostname+': '+type(exc).__name__)
                permanent=isinstance(exc,urllib.error.HTTPError) and 400<=exc.code<500 and exc.code not in {408,429}
                if permanent or attempt+1==attempts_per_source:break
                delay=min(2**attempt,4)
                until=min(time.monotonic()+delay,started+deadline)
                while time.monotonic()<until:
                    if cancelled():raise DownloadCancelled()
                    time.sleep(min(.1,max(0,until-time.monotonic())))
    if integrity_failure:raise PermissionError('组件 SHA-256 校验未通过；不可信文件已隔离，没有安装')
    raise OSError('组件下载未完成，可重试并续传；'+'; '.join(errors[-6:]))
