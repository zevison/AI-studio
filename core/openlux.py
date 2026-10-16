from __future__ import annotations
import base64, json, mimetypes, time, urllib.error, urllib.parse, urllib.request, uuid
from pathlib import Path
from . import config, tunnel, settings
from .utils import find_url

_ROUTE_AUTO = object()

class ApiError(RuntimeError):
    def __init__(self, message, status=None, body=None, retryable=False):
        super().__init__(message); self.status=status; self.body=body; self.retryable=retryable


def _headers(extra=None):
    h={'Authorization':f'Bearer {config.OPENLUX_API_KEY}','User-Agent':f'SeedanceStudio/{config.APP_VERSION}'}
    if extra: h.update(extra)
    return h


def _url(path):
    path=str(path)  # WindowsPath/PurePath are accepted from saved settings and Windows callers.
    if path.startswith(('http://','https://')): return path
    base=config.OPENLUX_BASE_URL.rstrip('/')
    if base.lower().endswith('/v1') and path.startswith(('/v1/', '/api/')): base=base[:-3]
    return base + ('/' if not path.startswith('/') else '') + path


def request_json(method, path, payload=None, timeout=None, retries=0, idempotency_key=None):
    if not config.OPENLUX_API_KEY:
        raise ApiError('未配置 OPENLUX_API_KEY。请先填写根目录 .env。')
    data=None; headers=_headers({'Content-Type':'application/json'})
    if idempotency_key: headers['Idempotency-Key']=idempotency_key
    if payload is not None: data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    last=None
    for attempt in range(retries+1):
        try:
            req=urllib.request.Request(_url(path),data=data,headers=headers,method=method)
            with urllib.request.urlopen(req,timeout=timeout or config.HTTP_TIMEOUT) as r:
                raw=r.read(); txt=raw.decode('utf-8','replace')
                return json.loads(txt) if txt.strip() else {}
        except urllib.error.HTTPError as e:
            body=e.read().decode('utf-8','replace'); retryable=e.code in (408,429,500,502,503,504)
            last=ApiError(f'接口返回 HTTP {e.code}',e.code,body,retryable)
            if attempt<retries and retryable:
                time.sleep(min(2**attempt,4)); continue
            raise last
        except (urllib.error.URLError,TimeoutError,OSError) as e:
            last=ApiError(f'网络请求失败：{e}',None,None,True)
            if attempt<retries:
                time.sleep(min(2**attempt,4)); continue
            raise last
        except json.JSONDecodeError as e:
            raise ApiError(f'接口返回了无法解析的 JSON：{e}')
    raise last or ApiError('未知接口错误')


def request_json_with_token(method, path, auth_token, payload=None, timeout=None, retries=0, user_id=None):
    if not auth_token:
        raise ApiError('未配置 OpenLux 账号访问令牌。')
    headers={
        'Authorization':f'Bearer {auth_token}',
        'Content-Type':'application/json',
        'User-Agent':f'SeedanceStudio/{config.APP_VERSION}',
    }
    if user_id:
        headers['New-Api-User']=str(user_id)
        headers['New-API-User']=str(user_id)
    data=json.dumps(payload,ensure_ascii=False).encode('utf-8') if payload is not None else None
    last=None
    for attempt in range(retries+1):
        try:
            req=urllib.request.Request(_url(path),data=data,headers=headers,method=method)
            with urllib.request.urlopen(req,timeout=timeout or config.HTTP_TIMEOUT) as r:
                txt=r.read().decode('utf-8','replace')
                return json.loads(txt) if txt.strip() else {}
        except urllib.error.HTTPError as e:
            body=e.read().decode('utf-8','replace')
            retryable=e.code in (408,429,500,502,503,504)
            last=ApiError(f'账号接口返回 HTTP {e.code}',e.code,body,retryable)
            if attempt<retries and retryable:
                time.sleep(min(2**attempt,4)); continue
            raise last
        except (urllib.error.URLError,TimeoutError,OSError) as e:
            last=ApiError(f'账号接口网络请求失败：{e}',None,None,True)
            if attempt<retries:
                time.sleep(min(2**attempt,4)); continue
            raise last
        except json.JSONDecodeError as e:
            raise ApiError(f'账号接口返回了无法解析的 JSON：{e}')
    raise last or ApiError('账号接口未知错误')

def multipart_request(path, fields:dict, file_fields:list[tuple[str,Path,str]], timeout=None, idempotency_key=None):
    if not config.OPENLUX_API_KEY: raise ApiError('未配置 OPENLUX_API_KEY。')
    boundary='----SeedanceStudio'+uuid.uuid4().hex; chunks=[]
    for k,v in fields.items():
        if v is None: continue
        chunks += [f'--{boundary}\r\n'.encode(), f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(), str(v).encode('utf-8'), b'\r\n']
    for field,path,mime in file_fields:
        chunks += [f'--{boundary}\r\n'.encode(), f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'.encode('utf-8'),
                   f'Content-Type: {mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"}\r\n\r\n'.encode(), path.read_bytes(), b'\r\n']
    chunks.append(f'--{boundary}--\r\n'.encode()); body=b''.join(chunks)
    headers=_headers({'Content-Type':f'multipart/form-data; boundary={boundary}'})
    if idempotency_key: headers['Idempotency-Key']=idempotency_key
    try:
        req=urllib.request.Request(_url(path),data=body,headers=headers,method='POST')
        with urllib.request.urlopen(req,timeout=timeout or config.HTTP_TIMEOUT) as r:
            txt=r.read().decode('utf-8','replace'); return json.loads(txt) if txt.strip() else {}
    except urllib.error.HTTPError as e:
        b=e.read().decode('utf-8','replace')
        raise ApiError(f'图片编辑接口返回 HTTP {e.code}',e.code,b,e.code in (408,429,500,502,503,504))
    except (urllib.error.URLError,TimeoutError,OSError) as e:
        raise ApiError(f'图片编辑网络请求失败：{e}',None,None,True)


IMAGE_RATIO_SIZES={
    # OpenLux GPT Image compatible sizes. The user's chosen ratio is also
    # injected into the prompt so composition follows the requested canvas.
    '1:1':'1024x1024',
    '4:5':'1024x1536',
    '3:4':'1024x1536',
    '9:16':'1024x1536',
    '16:9':'1536x1024',
}

def resolve_image_size(ratio='auto'):
    ratio=(ratio or 'auto').strip()
    return 'auto' if ratio=='auto' else IMAGE_RATIO_SIZES.get(ratio,'1024x1536')

def _ratio_prompt(prompt, ratio):
    if not ratio or ratio=='auto': return prompt
    return f'{prompt}\n构图比例要求：{ratio}。主体必须保留在安全区域内，不要贴边，不要裁掉关键产品或人物。'

def image_generate(prompt, ratio='auto', quality='auto', idempotency_key=None):
    size=resolve_image_size(ratio)
    prompt=_ratio_prompt(prompt,ratio)
    payload={'model':config.IMAGE_MODEL,'prompt':prompt,'n':1,'size':size,'quality':quality or 'auto'}
    return request_json('POST',config.IMAGE_CREATE_PATH,payload,retries=config.PAID_CREATE_RETRIES,idempotency_key=idempotency_key)


def image_edit(prompt, source_path:Path, source_mime='image/png', ratio='auto', quality='auto', idempotency_key=None):
    size=resolve_image_size(ratio)
    prompt=_ratio_prompt(prompt,ratio)
    fields={'model':config.IMAGE_MODEL,'prompt':prompt,'n':1,'size':size,'quality':quality or 'auto'}
    return multipart_request(config.IMAGE_EDIT_PATH,fields,[('image',source_path,source_mime)],idempotency_key=idempotency_key)


def _asset_url(asset:dict, public_base_url:str|None):
    if asset.get('remote_url'): return asset['remote_url']
    if not public_base_url: return None
    return tunnel.public_asset_url(public_base_url, asset)


def _content_media(kind:str, url:str, role:str):
    typ={'image':'image_url','video':'video_url','audio':'audio_url'}.get(kind)
    if not typ: return None
    if config.VIDEO_MEDIA_SHAPE == 'legacy_flat':
        return {'type':typ,'url':url,'role':role}
    # Official Ark shape for Seedance content items.
    return {'type':typ, typ:{'url':url}, 'role':role}


def video_create(prompt, media_specs:list[dict], ratio, resolution, duration, generate_audio=True,
                 public_base_url=None, idempotency_key=None, output_format='mp4', model=None, provider=_ROUTE_AUTO,
                 create_path=None, query_path=None, omni_reference_task_type=None):
    cfg=settings.get_all()
    route=settings.route_info(cfg)
    model=model or route['request_model']
    if provider is _ROUTE_AUTO:
        provider=route.get('provider')
    recommended_create,recommended_query=settings.effective_video_paths(cfg)
    create_path=create_path or recommended_create
    query_path=query_path or recommended_query
    content=[{'type':'text','text':prompt}]
    for spec in media_specs:
        a=spec.get('asset') or {}; role=spec.get('role')
        u=_asset_url(a, public_base_url)
        if not u:
            raise ApiError('存在本地参考素材，但没有可用公网 URL。请先在“状态与设置”配置 阿里云 OSS（推荐）或固定 HTTPS 通道。')
        item=_content_media(a.get('kind'),u,role)
        if item: content.append(item)
    payload={
        'model':model,
        'content':content,
        'ratio':ratio,
        'resolution':resolution,
        'duration':int(duration),
        'generate_audio':bool(generate_audio),
        'output_format':output_format,
    }
    if provider:
        payload['provider']=provider
    # Seedance 2.5 all-modal reference has 3 subtypes. Explicitly sending the
    # subtype prevents prompts such as "一比一复刻" from being auto-classified as
    # video edit while the request still carries reference-mode ratio/duration.
    if omni_reference_task_type in ('auto','reference','edit','extend'):
        payload['omni_reference_task_type']=omni_reference_task_type
    resp=request_json('POST',create_path,payload,timeout=int(cfg.get('http_timeout',config.HTTP_TIMEOUT)),retries=config.PAID_CREATE_RETRIES,idempotency_key=idempotency_key)
    payload['_request_model']=model
    payload['_request_provider']=provider
    payload['_create_path']=create_path
    payload['_query_path']=query_path
    return resp, payload


def video_query(task_id, query_path=None):
    cfg=settings.get_all()
    _,default_query=settings.effective_video_paths(cfg)
    path=(query_path or default_query).format(task_id=urllib.parse.quote(str(task_id)))
    return request_json('GET',path,timeout=int(cfg.get('http_timeout',config.HTTP_TIMEOUT)),retries=int(cfg.get('query_retries',config.QUERY_RETRIES)))

def list_models():
    resp=request_json('GET','/v1/models',timeout=30,retries=1)
    rows=[]
    if isinstance(resp,dict):
        data=resp.get('data')
        if isinstance(data,list): rows=data
        elif isinstance(resp.get('models'),list): rows=resp['models']
    out=[]
    for x in rows:
        mid=x.get('id') if isinstance(x,dict) else str(x)
        if mid: out.append(str(mid))
    # Put video-looking models first, but never hide other models because OpenLux model naming can change.
    out=sorted(set(out), key=lambda x:(0 if any(k in x.lower() for k in ('seedance','doubao','video')) else 1, x.lower()))
    return out



def extract_task_id(resp):
    if isinstance(resp,dict):
        for k in ('id','task_id'):
            if resp.get(k): return str(resp[k])
        d=resp.get('data')
        if isinstance(d,dict):
            for k in ('id','task_id'):
                if d.get(k): return str(d[k])
    return None


def extract_status(resp):
    vals=[]
    if isinstance(resp,dict):
        vals += [resp.get('status'),resp.get('task_status'),resp.get('state')]
        d=resp.get('data')
        if isinstance(d,dict): vals += [d.get('status'),d.get('task_status'),d.get('state')]
    s=next((str(x).lower() for x in vals if x is not None),'')
    if s in {'succeeded','success','completed','complete','done'}: return 'completed'
    if s in {'failed','error','expired','cancelled','canceled'}: return 'failed'
    if s in {'queued','pending','submitted','running','processing','in_progress','in-progress'}: return 'running'
    if find_url(resp): return 'completed'
    return 'running'


def extract_result_url(resp): return find_url(resp)


def download(url:str, dest:Path):
    req=urllib.request.Request(url,headers={'User-Agent':f'SeedanceStudio/{config.APP_VERSION}'})
    with urllib.request.urlopen(req,timeout=180) as r, dest.open('wb') as f:
        while True:
            b=r.read(1024*1024)
            if not b: break
            f.write(b)
    return dest
