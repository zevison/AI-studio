from __future__ import annotations
import mimetypes, re, time, urllib.request, urllib.error, uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from . import settings
from . import secrets_store

try:
    import alibabacloud_oss_v2 as oss_sdk
except Exception:  # install may not have run yet
    oss_sdk = None

_last_cleanup = 0.0


def _normalize_endpoint(v: str) -> str:
    v = str(v or '').strip().rstrip('/')
    if not v:
        return ''
    if not v.startswith(('http://', 'https://')):
        v = 'https://' + v
    return v


def _endpoint_host(endpoint: str) -> str:
    if not endpoint:
        return ''
    return urlparse(endpoint if '://' in endpoint else 'https://' + endpoint).netloc or endpoint.replace('https://','').replace('http://','')


def _derive_region(endpoint: str) -> str:
    host = _endpoint_host(endpoint).lower()
    # Standard public endpoint: oss-cn-hangzhou.aliyuncs.com / oss-ap-southeast-1.aliyuncs.com
    m = re.search(r'(?:^|\.)oss-([a-z0-9-]+)\.aliyuncs\.com$', host)
    if m and m.group(1) not in ('accelerate', 'accelerate-overseas'):
        return m.group(1)
    return ''


def _cfg() -> dict:
    c = settings.get_all()
    endpoint = _normalize_endpoint(c.get('oss_endpoint'))
    return {
        'endpoint': endpoint,
        'endpoint_host': _endpoint_host(endpoint),
        'region': _derive_region(endpoint),
        'bucket': str(c.get('oss_bucket') or '').strip(),
        'prefix': str(c.get('oss_object_prefix') or 'seedance-studio').strip().strip('/'),
        'ttl_minutes': int(c.get('asset_link_ttl_minutes') or 120),
        'auto_cleanup': bool(c.get('oss_auto_cleanup', True)),
        'delete_after_hours': int(c.get('oss_delete_after_hours') or 24),
        'access_key_id': secrets_store.get('oss_access_key_id','').strip(),
        'access_key_secret': secrets_store.get('oss_access_key_secret','').strip(),
    }


def configured() -> bool:
    c = _cfg()
    return bool(oss_sdk and c['endpoint'] and c['region'] and c['bucket'] and c['access_key_id'] and c['access_key_secret'])


def status() -> dict:
    c = _cfg(); sec = secrets_store.status()
    return {
        'configured': configured(),
        'sdk_available': bool(oss_sdk),
        'endpoint': c['endpoint'] or None,
        'region': c['region'] or None,
        'bucket': c['bucket'] or None,
        'prefix': c['prefix'],
        'ttl_minutes': c['ttl_minutes'],
        'auto_cleanup': c['auto_cleanup'],
        'delete_after_hours': c['delete_after_hours'],
        **sec,
    }


def _client(*, bounded=False):
    c = _cfg()
    if not oss_sdk:
        raise RuntimeError('缺少 alibabacloud-oss-v2。请重新运行 01_INSTALL.cmd 安装阿里云 OSS Python SDK V2。')
    missing=[]
    if not c['endpoint']: missing.append('Endpoint')
    if not c['region']: missing.append('可识别的 OSS Region（请使用标准公网 Endpoint）')
    if not c['bucket']: missing.append('Bucket')
    if not c['access_key_id']: missing.append('AccessKey ID')
    if not c['access_key_secret']: missing.append('AccessKey Secret')
    if missing:
        raise RuntimeError('阿里云 OSS 尚未配置完整：' + '、'.join(missing))
    if '-internal.aliyuncs.com' in c['endpoint']:
        raise RuntimeError('当前 Endpoint 是 internal 内网地址，OpenLux / Seedance 无法从公网读取。请改为公网 OSS Endpoint。')
    provider = oss_sdk.credentials.StaticCredentialsProvider(
        access_key_id=c['access_key_id'],
        access_key_secret=c['access_key_secret'],
    )
    cfg = oss_sdk.config.load_default()
    cfg.credentials_provider = provider
    cfg.region = c['region']
    # Alibaba Cloud SDK V2 examples pass the public endpoint directly (including https://).
    cfg.endpoint = c['endpoint']
    if bounded:
        cfg.connect_timeout = 10
        cfg.readwrite_timeout = 60
        cfg.retry_max_attempts = 1
        cfg.enabled_redirect = False
    return oss_sdk.Client(cfg)


def _object_key(asset: dict) -> str:
    c = _cfg(); sha = str(asset.get('sha256') or '').strip()
    if not sha:
        raise RuntimeError('素材缺少 SHA256，无法生成稳定 OSS 对象键。')
    ext = Path(asset.get('stored_name') or asset.get('original_name') or '').suffix.lower()
    if not ext:
        ext = mimetypes.guess_extension(asset.get('mime') or '') or '.bin'
    prefix = c['prefix'] or 'seedance-studio'
    return f'{prefix}/assets/{sha[:2]}/{sha}{ext}'


def _object_meta(client, bucket: str, key: str):
    try:
        return client.get_object_meta(oss_sdk.GetObjectMetaRequest(bucket=bucket, key=key))
    except Exception as e:
        msg = str(e)
        if '404' in msg or 'NoSuchKey' in msg or 'NotFound' in msg:
            return None
        raise RuntimeError(f'OSS 检查对象失败：{e}')


def _ensure_object(client, asset: dict, key: str) -> dict:
    c = _cfg(); meta = _object_meta(client, c['bucket'], key)
    exists = meta is not None
    if exists and asset.get('size_bytes') and getattr(meta, 'content_length', None) not in (None, int(asset['size_bytes'])):
        exists = False
    if not exists:
        p = Path(asset.get('path') or '')
        if not p.is_file():
            raise RuntimeError(f'本地素材文件不存在：{p}')
        req = oss_sdk.PutObjectRequest(
            bucket=c['bucket'], key=key,
            content_type=asset.get('mime') or mimetypes.guess_type(p.name)[0] or 'application/octet-stream',
        )
        try:
            client.put_object_from_file(req, str(p))
        except Exception as e:
            raise RuntimeError(f'OSS 上传素材失败：{e}')
        meta = _object_meta(client, c['bucket'], key)
    return {'uploaded': not exists, 'etag': getattr(meta, 'etag', None) if meta else None}


def presign_asset(asset: dict, min_ttl_minutes: int = 0) -> dict:
    c = _cfg(); client = _client(); key = _object_key(asset)
    obj = _ensure_object(client, asset, key)
    ttl_minutes=max(int(c['ttl_minutes']), int(min_ttl_minutes or 0))
    ttl = max(60, min(ttl_minutes * 60, 604800))
    try:
        pre = client.presign(
            oss_sdk.GetObjectRequest(bucket=c['bucket'], key=key),
            expires=timedelta(seconds=ttl),
        )
    except Exception as e:
        raise RuntimeError(f'OSS 生成临时签名 GET 链接失败：{e}')
    signed_headers = dict(getattr(pre, 'signed_headers', {}) or {})
    if signed_headers:
        # OpenLux/Seedance can only receive a URL, not custom headers. Default GetObject should not need any.
        raise RuntimeError('OSS 签名链接意外要求额外 Signed Headers，无法作为纯 URL 提交给 OpenLux。请检查 Endpoint / Bucket 配置。')
    return {
        'url': pre.url,
        'object_key': key,
        'bucket': c['bucket'],
        'endpoint': c['endpoint'],
        'region': c['region'],
        'expires_in': ttl,
        'uploaded': obj['uploaded'],
        'etag': obj['etag'],
    }


def test_url(url: str, read_bytes: int = 64) -> dict:
    try:
        req = urllib.request.Request(url, method='GET', headers={'User-Agent':'SeedanceStudio-OSS-SelfTest/3.9.0'})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read(read_bytes)
            ok = r.status in (200,206) and bool(body)
            return {'ok':ok,'status':r.status,'bytes_read':len(body),'content_type':r.headers.get('Content-Type'),'content_length':r.headers.get('Content-Length'),'error':None if ok else '返回内容为空'}
    except urllib.error.HTTPError as e:
        return {'ok':False,'status':e.code,'bytes_read':0,'error':f'HTTP {e.code}: {e.reason}'}
    except Exception as e:
        return {'ok':False,'status':None,'bytes_read':0,'error':str(e)}


def prepare_asset(asset: dict, min_ttl_minutes: int = 0) -> dict:
    info = presign_asset(asset, min_ttl_minutes=min_ttl_minutes); tst = test_url(info['url'])
    if not tst.get('ok'):
        raise RuntimeError('OSS 已生成临时签名链接，但实际 GET 自检失败：' + str(tst.get('error') or tst.get('status')))
    return {**info, 'test': tst}


def prepare_specs(specs: list[dict], min_ttl_minutes: int = 0) -> dict:
    if not specs:
        return {'ok':True,'provider':'none','specs':[],'checks':[],'uploaded_count':0}
    cleanup_if_due(); out=[]; checks=[]; uploaded=0
    for spec in specs:
        a = dict(spec.get('asset') or {}); info = prepare_asset(a, min_ttl_minutes=min_ttl_minutes)
        a['remote_url'] = info['url']; a['remote_provider'] = 'aliyun_oss'; a['remote_object_key'] = info['object_key']
        out.append({'asset':a,'role':spec.get('role')})
        uploaded += 1 if info.get('uploaded') else 0
        checks.append({'asset_id':a.get('id'),'name':a.get('original_name'),'ok':True,'status':info['test'].get('status'),'bytes_read':info['test'].get('bytes_read'),'object_key':info['object_key'],'uploaded':info.get('uploaded',False)})
    return {'ok':True,'provider':'aliyun_oss','specs':out,'checks':checks,'uploaded_count':uploaded}


def test_connection(write_probe: bool = True) -> dict:
    c = _cfg()
    try:
        client = _client(); checks=[]
        if write_probe:
            key=f"{c['prefix'] or 'seedance-studio'}/_health/{uuid.uuid4().hex}.txt"
            try:
                client.put_object(oss_sdk.PutObjectRequest(bucket=c['bucket'], key=key, body=b'SeedanceStudio OSS health check', content_type='text/plain'))
                checks.append({'name':'OSS 写入权限','ok':True,'required':True})
            except Exception as e:
                return {'ok':False,'checks':[{'name':'OSS 写入权限','ok':False,'required':True,'error':str(e)}],'error':f'OSS 写入测试失败：{e}'}
            try:
                pre=client.presign(oss_sdk.GetObjectRequest(bucket=c['bucket'],key=key),expires=timedelta(seconds=300))
                if getattr(pre,'signed_headers',None):
                    return {'ok':False,'checks':checks,'error':'OSS 测试签名链接要求额外 Signed Headers，不适合纯 URL 视频素材链路。'}
                g=test_url(pre.url)
                checks.append({'name':'OSS 临时签名 GET 链接','ok':bool(g.get('ok')),'required':True,'status':g.get('status'),'error':g.get('error')})
            finally:
                try: client.delete_object(oss_sdk.DeleteObjectRequest(bucket=c['bucket'],key=key))
                except Exception: pass
            if not g.get('ok'):
                return {'ok':False,'checks':checks,'error':'OSS 已写入测试对象，但临时签名 GET 链接读取失败。'}
        return {'ok':True,'checks':checks,'endpoint':c['endpoint'],'region':c['region'],'bucket':c['bucket']}
    except Exception as e:
        return {'ok':False,'checks':[],'error':str(e),'endpoint':c.get('endpoint') or None,'region':c.get('region') or None,'bucket':c.get('bucket') or None}


def _last_modified_ts(v) -> float:
    if isinstance(v,(int,float)): return float(v)
    if hasattr(v,'timestamp'):
        try:return float(v.timestamp())
        except Exception:return time.time()
    return time.time()


def cleanup_expired(force: bool = False) -> dict:
    global _last_cleanup
    c = _cfg()
    if not c['auto_cleanup']:
        return {'ok':True,'skipped':True,'reason':'自动清理已关闭','deleted':0}
    now=time.time()
    if not force and now-_last_cleanup < 12*3600:
        return {'ok':True,'skipped':True,'reason':'距离上次清理不足 12 小时','deleted':0}
    client=_client(); prefix=(c['prefix'] or 'seedance-studio').rstrip('/')+'/assets/'; cutoff=now-c['delete_after_hours']*3600; deleted=0
    try:
        paginator=client.list_objects_v2_paginator()
        for page in paginator.iter_page(oss_sdk.ListObjectsV2Request(bucket=c['bucket'], prefix=prefix)):
            for obj in (page.contents or []):
                if _last_modified_ts(getattr(obj,'last_modified',None)) < cutoff:
                    client.delete_object(oss_sdk.DeleteObjectRequest(bucket=c['bucket'], key=obj.key)); deleted += 1
    except Exception as e:
        raise RuntimeError(f'OSS 清理过期素材失败：{e}')
    _last_cleanup=now
    return {'ok':True,'deleted':deleted,'cutoff_hours':c['delete_after_hours']}


def cleanup_if_due():
    try:
        if configured(): cleanup_expired(False)
    except Exception:
        pass
