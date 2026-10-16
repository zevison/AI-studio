from __future__ import annotations
import hashlib, hmac, mimetypes, os, queue, re, shutil, subprocess, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from . import config
from . import settings as user_settings

_lock=threading.RLock(); _proc=None; _url=None; _selected_url=None; _selected_provider=None; _last_error=None; _reader_thread=None; _line_queue=queue.Queue(); _asset_server=None; _asset_thread=None


def _secret():
    seed=(config.OPENLUX_API_KEY or str(config.ROOT.resolve())+'|seedance-local')+'|asset-link-v241'
    return hashlib.sha256(seed.encode('utf-8')).digest()


def _sign(exp:int, name:str):
    msg=f'{exp}:{name}'.encode('utf-8')
    return hmac.new(_secret(),msg,hashlib.sha256).hexdigest()[:32]


def signed_path(name:str, ttl_minutes:int|None=None):
    ttl=int(ttl_minutes or user_settings.get('asset_link_ttl_minutes',60))
    exp=int(time.time()+max(5,ttl)*60); sig=_sign(exp,name)
    return f'/public/assets/{exp}.{sig}/{urllib.parse.quote(name)}'


def validate_token(token:str,name:str):
    try: exp_s,sig=token.split('.',1); exp=int(exp_s)
    except Exception:return False,'格式错误'
    if exp<int(time.time()):return False,'链接已过期'
    if not hmac.compare_digest(sig,_sign(exp,name)):return False,'签名错误'
    return True,''


def public_asset_url(base_url:str, asset:dict, ttl_minutes:int|None=None):
    return base_url.rstrip('/')+signed_path(asset['stored_name'],ttl_minutes)


class _AssetHandler(BaseHTTPRequestHandler):
    server_version='SeedanceAssetServer/2.4.2'
    def log_message(self, fmt, *args): pass
    def _reject(self,status=404):
        self.send_response(status);self.send_header('Content-Length','0');self.end_headers()
    def _resolve(self):
        path=urllib.parse.urlparse(self.path).path
        if path=='/health': return 'health',None
        m=re.match(r'^/public/assets/([^/]+)/(.+)$',path)
        if not m:return None,None
        token,name_enc=m.groups(); name=urllib.parse.unquote(name_enc)
        ok,_=validate_token(token,name)
        if not ok:return 'forbidden',None
        p=(config.ASSET_DIR/name).resolve()
        if config.ASSET_DIR.resolve() not in p.parents or not p.is_file(): return None,None
        return 'asset',p
    def _send_asset(self,head_only=False):
        kind,p=self._resolve()
        if kind=='health':
            body=b'ok';self.send_response(200);self.send_header('Content-Type','text/plain');self.send_header('Content-Length',str(len(body)));self.end_headers()
            if not head_only:self.wfile.write(body)
            return
        if kind=='forbidden':return self._reject(403)
        if kind!='asset' or not p:return self._reject(404)
        size=p.stat().st_size; start=0; end=size-1; status=200
        rh=self.headers.get('Range','')
        if rh:
            m=re.match(r'bytes=(\d*)-(\d*)$',rh.strip())
            if not m:return self._reject(416)
            a,b=m.groups()
            if a:start=int(a)
            if b:end=min(int(b),size-1)
            elif not a:return self._reject(416)
            if start<0 or start>=size or end<start:return self._reject(416)
            status=206
        length=end-start+1; ctype=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        self.send_response(status);self.send_header('Content-Type',ctype);self.send_header('Accept-Ranges','bytes');self.send_header('Content-Length',str(length));self.send_header('Cache-Control','private, max-age=60');self.send_header('X-Content-Type-Options','nosniff')
        if status==206:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
        self.end_headers()
        if head_only:return
        try:
            with p.open('rb') as f:
                f.seek(start); remaining=length
                while remaining>0:
                    b=f.read(min(1024*1024,remaining))
                    if not b:break
                    self.wfile.write(b); remaining-=len(b)
        except BrokenPipeError:pass
    def do_GET(self): self._send_asset(False)
    def do_HEAD(self): self._send_asset(True)
    def do_POST(self): self._reject(405)
    def do_PUT(self): self._reject(405)
    def do_DELETE(self): self._reject(405)


def _ensure_asset_server():
    global _asset_server,_asset_thread
    if _asset_server:return
    _asset_server=ThreadingHTTPServer((config.ASSET_HOST,config.ASSET_PORT),_AssetHandler); _asset_thread=threading.Thread(target=_asset_server.serve_forever,daemon=True); _asset_thread.start()


def _candidate():
    if config.CLOUDFLARED_PATH:
        p=Path(config.CLOUDFLARED_PATH)
        if p.exists():return str(p)
    for local in [config.ROOT/'cloudflared.exe',config.ROOT/'tools'/'cloudflared.exe',config.ROOT/'cloudflared']:
        if local.exists():return str(local)
    return shutil.which('cloudflared')


def effective_url():
    if _selected_url:
        return _selected_url.rstrip('/')
    strategy=user_settings.get('public_asset_strategy','auto')
    fixed=user_settings.get('fixed_public_asset_base_url','')
    quick=_url if (_proc and _proc.poll() is None and _url) else None
    if strategy=='fixed_only': return fixed.rstrip('/') if fixed else None
    if strategy=='quick_only': return quick
    return quick or (fixed.rstrip('/') if fixed else None)


def status():
    running=bool(_proc and _proc.poll() is None)
    fixed=user_settings.get('fixed_public_asset_base_url','')
    strategy=user_settings.get('public_asset_strategy','auto')
    quick=_url if running else None
    return {'running':running,'url':quick,'effective_url':effective_url(),'fixed_url':fixed or None,'strategy':strategy,
            'selected_provider':_selected_provider,'error':_last_error,'binary':_candidate(),
            'asset_server':f'http://{config.ASSET_HOST}:{config.ASSET_PORT}' if _asset_server else None,
            'scope':'只读临时素材端口（签名链接 + 过期时间 + HEAD / Range）'}

def _read_output(proc):
    try:
        if proc.stdout:
            for line in iter(proc.stdout.readline,''):
                if not line:break
                _line_queue.put(line)
    except Exception:pass


def start():
    global _proc,_url,_last_error,_reader_thread,_line_queue
    with _lock:
        fixed=user_settings.get('fixed_public_asset_base_url',''); strategy=user_settings.get('public_asset_strategy','auto')
        try:_ensure_asset_server()
        except OSError as e:
            _last_error=f'只读素材端口 {config.ASSET_PORT} 启动失败：{e}'; return status()
        if strategy=='fixed_only':
            _last_error=None if fixed else '已选择固定通道，但尚未配置固定 HTTPS 地址。'; return status()
        if _proc and _proc.poll() is None and _url:return status()
        exe=_candidate()
        if not exe:
            _last_error='未找到 cloudflared。运行 01_INSTALL.cmd 自动下载，或把 cloudflared.exe 放到 tools 文件夹。'; return status()
        cmd=[exe,'tunnel','--url',f'http://{config.ASSET_HOST}:{config.ASSET_PORT}','--no-autoupdate']
        try:
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0) if os.name=='nt' else 0
            _proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=creationflags)
            _url=None;_last_error=None;_line_queue=queue.Queue(); _reader_thread=threading.Thread(target=_read_output,args=(_proc,),daemon=True);_reader_thread.start()
            deadline=time.time()+18;pattern=re.compile(r'https://[-a-z0-9]+\.trycloudflare\.com',re.I);captured=[]
            while time.time()<deadline:
                if _proc.poll() is not None:break
                try:line=_line_queue.get(timeout=.25)
                except queue.Empty:continue
                captured.append(line.strip());m=pattern.search(line)
                if m:_url=m.group(0);break
            if not _url:
                tail=' | '.join(captured[-3:]);_last_error='Quick Tunnel（快速隧道）没有取得公网地址。'+(f' 最近输出：{tail}' if tail else '')
        except Exception as e:_last_error=f'Quick Tunnel 启动失败：{e}'
        return status()


def self_test(asset:dict|None=None, base_url:str|None=None):
    """Verify that a public asset channel is usable by a remote video provider.

    HEAD and byte-range support are useful optimizations, but OpenLux/Seedance
    fundamentally needs a reachable public URL. Some proxies answer ordinary GET
    correctly while rejecting HEAD or Range. Therefore:
      - health + real asset GET is the hard gate;
      - HEAD + Range are reported as compatibility enhancements;
      - HTTP status / error text are preserved for diagnosis.
    """
    base=(base_url or effective_url() or '').rstrip('/')
    if not base:
        return {'ok':False,'optimized':False,'base_url':None,'checks':[],'error':'没有可用公网素材地址'}
    checks=[]

    def req(url,method='GET',headers=None,read_bytes=64):
        hdr={'User-Agent':'SeedanceStudio-SelfTest/3.9.0'}
        if headers: hdr.update(headers)
        try:
            r=urllib.request.Request(url,method=method,headers=hdr)
            with urllib.request.urlopen(r,timeout=20) as x:
                body=x.read(read_bytes) if method!='HEAD' and read_bytes else b''
                return True,x.status,dict(x.headers),body,None
        except urllib.error.HTTPError as e:
            try: body=e.read(min(read_bytes or 256,256))
            except Exception: body=b''
            return False,e.code,dict(e.headers or {}),body,f'HTTP {e.code}: {e.reason}'
        except urllib.error.URLError as e:
            return False,None,{},b'',f'连接失败: {getattr(e,"reason",e)}'
        except Exception as e:
            return False,None,{},b'',str(e)

    ok_h,s_h,h_h,b_h,e_h=req(base+'/health')
    health_ok=bool(ok_h and s_h==200)
    checks.append({'name':'HTTPS 健康检查','ok':health_ok,'required':True,'status':s_h,'error':e_h})

    asset_fetch_ok=True
    head_ok=True
    range_ok=True
    asset_url=None
    if asset:
        asset_url=public_asset_url(base,asset)

        ok1,s1,h1,b1,e1=req(asset_url,'HEAD')
        head_ok=bool(ok1 and s1==200)
        checks.append({'name':'HEAD 资源探测','ok':head_ok,'required':False,'status':s1,
                       'content_type':h1.get('Content-Type'),'content_length':h1.get('Content-Length'),'error':e1})

        ok2,s2,h2,b2,e2=req(asset_url,'GET',{'Range':'bytes=0-1023'},64)
        range_ok=bool(ok2 and s2==206 and len(b2)>0)
        checks.append({'name':'Range 分段读取','ok':range_ok,'required':False,'status':s2,
                       'content_range':h2.get('Content-Range'),'bytes_read':len(b2),'error':e2})

        # Hard-gate fallback: prove that the actual signed asset URL can be read.
        # This is closer to what the video provider ultimately needs than requiring
        # every proxy to implement HEAD and Range identically.
        if range_ok:
            asset_fetch_ok=True
            checks.append({'name':'实际素材 GET 可读','ok':True,'required':True,'status':s2,
                           'content_type':h2.get('Content-Type'),'bytes_read':len(b2),
                           'note':'Range 已成功，等价证明素材可读取'})
        else:
            ok3,s3,h3,b3,e3=req(asset_url,'GET',None,64)
            ctype=(h3.get('Content-Type') or '').lower()
            asset_fetch_ok=bool(ok3 and s3 in (200,206) and len(b3)>0)
            checks.append({'name':'实际素材 GET 可读','ok':asset_fetch_ok,'required':True,'status':s3,
                           'content_type':h3.get('Content-Type'),'content_length':h3.get('Content-Length'),
                           'bytes_read':len(b3),'error':e3})

    hard_ok=health_ok and asset_fetch_ok
    optimized=hard_ok and head_ok and range_ok
    warning=None
    if hard_ok and not optimized:
        warning='素材公网 URL 已可读取；HEAD / Range 未完全支持。允许继续，但兼容性不如完整模式。'
    error=None if hard_ok else '公网通道或实际素材 URL 无法从公网读取，已阻止付费提交。'
    return {'ok':hard_ok,'optimized':optimized,'base_url':base,'asset_url':asset_url,
            'checks':checks,'warning':warning,'error':error}

def ensure_ready(asset:dict|None=None):
    global _selected_url,_selected_provider,_last_error
    strategy=user_settings.get('public_asset_strategy','auto')
    fixed=(user_settings.get('fixed_public_asset_base_url','') or '').rstrip('/')
    selftest=bool(user_settings.get('tunnel_selftest_before_paid',True))
    attempts=[]

    def try_base(provider,base):
        if not base:return None
        test=self_test(asset,base) if selftest else {'ok':True,'skipped':True,'base_url':base}
        attempts.append({'provider':provider,'base_url':base,'test':test})
        if test.get('ok'):
            _selected_url=base; _selected_provider=provider; _last_error=None
            return {'ok':True,'base_url':base,'provider':provider,'status':status(),'test':test,'attempts':attempts}
        return None

    _selected_url=None; _selected_provider=None
    if strategy in ('auto','quick_only'):
        if not (_proc and _proc.poll() is None and _url) and user_settings.get('tunnel_auto_start',True):
            start()
        quick=_url if (_proc and _proc.poll() is None and _url) else None
        got=try_base('quick_tunnel',quick)
        if got:return got
        if strategy=='quick_only':
            _last_error='Quick Tunnel 自检未通过或未取得地址。'
            return {'ok':False,'base_url':None,'provider':None,'status':status(),'test':attempts[-1]['test'] if attempts else {'ok':False,'error':_last_error},'attempts':attempts}

    if strategy in ('auto','fixed_only'):
        got=try_base('fixed_https',fixed)
        if got:return got

    _last_error='没有通过自检的公网素材通道。自动模式已依次检查 Quick Tunnel 与固定 HTTPS 备用地址。'
    return {'ok':False,'base_url':None,'provider':None,'status':status(),'test':attempts[-1]['test'] if attempts else {'ok':False,'error':_last_error},'attempts':attempts}

def stop():
    global _proc,_url,_selected_url,_selected_provider,_asset_server,_asset_thread
    with _lock:
        if _proc and _proc.poll() is None:
            try:_proc.terminate();_proc.wait(timeout=3)
            except Exception:
                try:_proc.kill()
                except Exception:pass
        _proc=None;_url=None;_selected_url=None;_selected_provider=None
        if _asset_server:
            try:_asset_server.shutdown();_asset_server.server_close()
            except Exception:pass
        _asset_server=None;_asset_thread=None
        return status()
