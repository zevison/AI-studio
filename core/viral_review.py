"""Manual-only real TikTok video verification and timestamped vision evidence."""
from __future__ import annotations
import base64, io, json, time, urllib.parse, urllib.request, urllib.error, threading, uuid, hashlib, shutil
from pathlib import Path
from . import db, config, media, smart_analysis

DOWNLOAD_LOCK=threading.RLock()
DOWNLOAD_ACTIVE=set()
DOMAINS=('tiktok.com','tiktokcdn.com','tiktokcdn-us.com','tiktokcdn-eu.com','tiktokv.com')
MAX_BYTES=min(120*1024*1024,config.MAX_UPLOAD_MB*1024*1024)

def schema():
    with db.tx() as con:
        con.execute("CREATE TABLE IF NOT EXISTS radar_video_sources(item_id TEXT PRIMARY KEY, product_id TEXT NOT NULL, asset_id TEXT NOT NULL, binding_type TEXT NOT NULL, created_at REAL, updated_at REAL)")
        con.execute("""CREATE TABLE IF NOT EXISTS radar_original_downloads(
             item_id TEXT PRIMARY KEY,job_id TEXT NOT NULL,status TEXT NOT NULL,
             mode TEXT NOT NULL,source_url TEXT NOT NULL,media_sha256 TEXT DEFAULT '',
             asset_id TEXT DEFAULT '',error TEXT DEFAULT '',started_at REAL,updated_at REAL)""")

def _item(item_id):
    from . import hot_radar
    row=db.one('SELECT * FROM radar_items WHERE id=?',(str(item_id),))
    if not row or row['kind']!='video' or not hot_radar._is_tiktok_url(row['url']):
        raise ValueError('请选择已收录的 TikTok 视频')
    return row

def statuses(item_ids):
    # One query for a page of videos, avoids repeated schema and SQLite connections.
    ids=[str(x) for x in dict.fromkeys(item_ids) if x]
    if not ids:return {}
    schema()
    q=','.join('?' for _ in ids)
    sql=('SELECT s.item_id,s.binding_type,a.id asset_id,a.duration,a.original_name,a.path,a.kind '
         'FROM radar_video_sources s LEFT JOIN assets a ON a.id=s.asset_id '
         'WHERE s.item_id IN ('+q+')')
    rows=db.all_(sql,tuple(ids));mapping={}
    dls={r['item_id']:r for r in db.all_('SELECT item_id,status,error,mode FROM radar_original_downloads WHERE item_id IN ('+q+')',tuple(ids))}
    for key, job in dls.items():
        if job['status']=='running' and key not in DOWNLOAD_ACTIVE:
            db.execute('UPDATE radar_original_downloads SET status=?,error=?,updated_at=? WHERE item_id=?',
                       ('interrupted','本地服务已重新启动；上次原帖下载未完成，不会自动重复获取。',time.time(),key))
            job['status']='interrupted';job['error']='服务重启中断下载，可手动重新尝试'
    for r in rows:
        if not r.get('asset_id') or r.get('kind')!='video' or not Path(r.get('path') or '').is_file():
            mapping[r['item_id']]={'ready':False,'asset_id':None,'reason':'原视频文件丢失，请重新绑定'}
        else:
            mapping[r['item_id']]={'ready':True,'asset_id':r['asset_id'],'duration':r['duration'],
                                   'asset_name':r['original_name'],'binding_type':r['binding_type']}
    result={key:mapping.get(key,{'ready':False,'asset_id':None}) for key in ids}
    for key in ids:
        t=dls.get(key) or {};result[key]['download_status']=t.get('status') or 'idle';result[key]['download_error']=t.get('error') or ''
    return result

def status(item_id):
    return statuses([item_id])[str(item_id)]

def asset_for_item(item_id):
    s=status(item_id)
    if not s['ready']:raise ValueError('此视频尚未绑定真实媒体文件，请先获取视频或手动上传；不能只凭标题拆解。')
    if not media.ffmpeg_path():raise ValueError('未检测到 FFmpeg，无法解析真实视频')
    return db.one('SELECT * FROM assets WHERE id=?',(s['asset_id'],))

def bind(payload):
    item=_item(payload.get('item_id'))
    aid=str(payload.get('asset_id') or '')
    asset=db.one('SELECT * FROM assets WHERE id=?',(aid,))
    if not asset or asset['kind']!='video' or not Path(asset['path']).is_file():
        raise ValueError('请选择已上传的视频素材')
    if not media.ffmpeg_path():raise ValueError('请先安装 FFmpeg，确认本地视频解析能力')
    info=media.probe(Path(asset['path']))
    if not info.get('duration') or not info.get('width'):raise ValueError('视频无法读取时长或画面，请换一个完整文件')
    schema();now=time.time()
    with db.tx() as con:
        con.execute('''INSERT INTO radar_video_sources VALUES(?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET asset_id=excluded.asset_id,binding_type=excluded.binding_type,updated_at=excluded.updated_at''',
                    (item['id'],item['product_id'],aid,'manual_upload',now,now))
    return status(item['id'])

def _allowed(url):
    try:
        x=urllib.parse.urlsplit(url);host=(x.hostname or '').lower().rstrip('.')
        return x.scheme=='https' and not x.username and not x.password and x.port in (None,443) and any(host==d or host.endswith('.'+d) for d in DOMAINS)
    except (ValueError,TypeError):return False

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not _allowed(newurl):raise ValueError('视频链接跳转至未批准域名，已停止')
        return super().redirect_request(req,fp,code,msg,headers,newurl)

def fetch_video(payload, save_asset_bytes):
    item=_item(payload.get('item_id'))
    if payload.get('confirmed') is not True:raise ValueError('需要人工确认获取视频')
    url=str(json.loads(item['metadata_json'] or '{}').get('media_url') or '')
    if not _allowed(url):raise ValueError('没有可信的原始视频媒体地址，请手动上传有权使用的文件')
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Accept':'video/mp4,video/*'})
    opener=urllib.request.build_opener(_SafeRedirect())
    try:
        with opener.open(req,timeout=30) as response:
            if not _allowed(response.geturl()):raise ValueError('视频地址不是可信来源')
            if int(response.headers.get('Content-Length','0') or 0)>MAX_BYTES:raise ValueError('视频超过120MB')
            buf=io.BytesIO()
            while True:
                chunk=response.read(min(1024*1024,MAX_BYTES-buf.tell()+1))
                if not chunk:break
                buf.write(chunk)
                if buf.tell()>MAX_BYTES:raise ValueError('视频超过最大下载大小')
            data=buf.getvalue()
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError) as e:
        raise ValueError('视频地址不可访问或已经过期，请手动上传原视频：'+type(e).__name__) from e
    if len(data)<4096 or b'ftyp' not in data[:48]:raise ValueError('返回的不是完整 MP4，请手动上传')
    asset=save_asset_bytes(data,'tiktok_'+item['id']+'.mp4','video/mp4','video')
    result=bind({'item_id':item['id'],'asset_id':asset['id']})
    db.execute('UPDATE radar_video_sources SET binding_type=? WHERE item_id=?',('manual_fetch',item['id']))
    return dict(result,binding_type='manual_fetch',bytes=len(data))

def original_download_status(item_id):
    """Local-only status read. Never launches a downloader or paid API request."""
    schema()
    row=db.one('SELECT * FROM radar_original_downloads WHERE item_id=?',(str(item_id),))
    if row and row['status']=='running' and row['item_id'] not in DOWNLOAD_ACTIVE:
        # A process restart interrupted a local subprocess. We never auto-retry.
        with DOWNLOAD_LOCK:
            if row['item_id'] not in DOWNLOAD_ACTIVE:
                db.execute('UPDATE radar_original_downloads SET status=?,error=?,updated_at=? WHERE item_id=?',
                           ('interrupted','服务已重启，原帖下载未确认完成；可人工重新尝试，不会调用采集或GPT。',time.time(),row['item_id']))
                row=db.one('SELECT * FROM radar_original_downloads WHERE item_id=?',(str(item_id),))
    return dict(row) if row else {'item_id':str(item_id),'status':'idle'}


def downloader_status():
    from . import bright_e2e_media
    result=bright_e2e_media.status()
    result['ffmpeg']=bool(media.ffmpeg_path())
    result['ffprobe']=bool(media.ffprobe_path())
    result['manual_only']=True
    return result


def _download_post_worker(item_id,job_id,post_url,post_id,mode,save_asset_bytes):
    from . import bright_e2e_media
    folder=config.CACHE_DIR/'bright_e2e'/'originals'/job_id
    try:
        response=bright_e2e_media.download_post(post_url,post_id,folder,mode=mode,ffmpeg=media.ffmpeg_path())
        path=Path(response['path'])
        probe=media.probe(path)
        if not probe.get('duration') or not probe.get('width') or not 0.2<float(probe['duration'])<=3600:
            raise ValueError('FFprobe未通过视频时长与画面核验，禁止当作可用视频')
        media_bytes=path.read_bytes()
        digest=hashlib.sha256(media_bytes).hexdigest()
        # The standard Studio asset store hashes bytes, deduplicates and retains metadata.
        saved=save_asset_bytes(media_bytes,'tiktok_'+post_id+path.suffix.lower(),
                               'video/mp4' if path.suffix.lower()=='.mp4' else 'video/'+path.suffix.lower()[1:],'video')
        bind({'item_id':item_id,'asset_id':saved['id']})
        db.execute('UPDATE radar_video_sources SET binding_type=? WHERE item_id=?',('original_post_'+mode,item_id))
        db.execute('UPDATE radar_original_downloads SET status=?,asset_id=?,media_sha256=?,error=?,updated_at=? WHERE item_id=? AND job_id=?',
                   ('done',saved['id'],digest,'',time.time(),item_id,job_id))
    except Exception as exc:
        db.execute('UPDATE radar_original_downloads SET status=?,error=?,updated_at=? WHERE item_id=? AND job_id=?',
                   ('failed',bright_e2e_media.clean_error(str(exc))[:800],time.time(),item_id,job_id))
    finally:
        with DOWNLOAD_LOCK: DOWNLOAD_ACTIVE.discard(item_id)
        # Do not retain an extra copy in cache; the real asset was saved in Studio.
        try:shutil.rmtree(folder,ignore_errors=True)
        except OSError:pass


def start_original_download(payload,save_asset_bytes):
    """Explicitly authorized, single original-post attempt. Never calls Bright Data/OpenLux."""
    from . import bright_e2e_media
    item=_item(payload.get('item_id'))
    if payload.get('confirmed') is not True:raise ValueError('必须人工确认本次原帖下载')
    mode=str(payload.get('mode') or 'public').lower()
    if mode not in ('public','chrome','edge'):raise ValueError('无效下载模式')
    if mode!='public' and payload.get('browser_cookie_consent') is not True:
        raise ValueError('访问浏览器登录会话必须单独明确授权，且不会将Cookie保存到工作台')
    post_url,post_id=bright_e2e_media.valid_post_url(item['url'])
    if not media.ffprobe_path() or not media.ffmpeg_path():raise ValueError('请先安装FFmpeg及FFprobe')
    stat=bright_e2e_media.status()
    if not stat['installed']:raise ValueError('尚未安装原帖下载组件，请关闭工作台后重新运行 01_INSTALL.cmd')
    if not stat['impersonation']['available']:
        raise ValueError('下载组件未通过真实浏览器模拟能力检测：'+stat['impersonation']['reason'][:180])
    schema();now=time.time()
    with DOWNLOAD_LOCK:
        previous=db.one('SELECT * FROM radar_original_downloads WHERE item_id=?',(item['id'],))
        if item['id'] in DOWNLOAD_ACTIVE or (previous and previous['status']=='running' and now-float(previous['updated_at'] or 0)<190):
            return {'item_id':item['id'],'status':'running','job_id':previous['job_id'] if previous else None,'reused':True}
        job_id='original_'+uuid.uuid4().hex[:18]
        db.execute("""INSERT INTO radar_original_downloads(item_id,job_id,status,mode,source_url,started_at,updated_at)
             VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id) DO UPDATE SET job_id=excluded.job_id,status=excluded.status,mode=excluded.mode,
             source_url=excluded.source_url,media_sha256='',asset_id='',error='',started_at=excluded.started_at,updated_at=excluded.updated_at""",
            (item['id'],job_id,'running',mode,post_url,now,now))
        DOWNLOAD_ACTIVE.add(item['id'])
        threading.Thread(target=_download_post_worker,args=(item['id'],job_id,post_url,post_id,mode,save_asset_bytes),
                         daemon=True,name='original-'+job_id).start()
        return {'item_id':item['id'],'status':'running','job_id':job_id,'reused':False}


def visual_evidence(item_id,max_frames=7):
    try:
        from PIL import Image
    except ImportError as exc:
        raise ValueError('缺少 Pillow 图片处理组件，尚未调用 GPT；请关闭工作台并重新运行 01_INSTALL.cmd') from exc
    a=asset_for_item(item_id);path=Path(a['path']);info=media.probe(path)
    duration=float(info.get('duration') or 0)
    if not 0.2<duration<=3600:raise ValueError('视频时长异常，无法拆解真实画面')
    # Bind analysis to the ACTUAL file bytes, not a possibly stale assets-table hash.
    with path.open('rb') as stream:
        h=hashlib.sha256()
        for chunk in iter(lambda:stream.read(1024*1024),b''):h.update(chunk)
    actual_hash=h.hexdigest()
    if a.get('sha256') and a['sha256']!=actual_hash:
        raise ValueError('视频文件哈希与入库记录不一致，必须重新上传或获取原视频；禁止分析已被替换的文件')
    count=max(5,min(int(max_frames),16));root=config.CACHE_DIR/'viral_frames'/str(a.get('sha256') or a['id'])[:28];root.mkdir(parents=True,exist_ok=True)
    # Adaptive sampling: protect the first 3 seconds, then cover the rest of the
    # story. Native video understanding is still not implied by sparse frames.
    if duration>=8 and count>=8:
        hooks=[.4,1.25,2.55]
        remainder=count-len(hooks)
        tail=[3+(duration-3)*(i+1)/(remainder+1) for i in range(remainder)]
        times=hooks+tail
    else:
        times=[(i+.5)*duration/count for i in range(count)]
    times=[max(.02,min(duration-.03,float(t))) for t in times]
    parts=[];frames=[]
    for index,t in enumerate(times):
        dst=root/f'{index:02d}_{int(t*1000)}.jpg'
        if not dst.is_file() and not smart_analysis._extract_frame(path,dst,t):continue
        try:
            with Image.open(dst) as im:
                im.thumbnail((512,768));buf=io.BytesIO();im.convert('RGB').save(buf,'JPEG',quality=72);raw=buf.getvalue()
        except Exception:continue
        frames.append({'time':round(t,2),'frame_no':index+1})
        parts.extend([{'type':'text','text':f'原视频真实画面，第{index+1}帧，{t:.2f}秒'},
                      {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(raw).decode('ascii')}}])
    if len(frames)<2:raise ValueError('实际视频抽帧失败，尚未调用GPT。请检查FFmpeg')
    speech=[];speech_state='无法确认原视频口播和背景声音；严禁补写所谓原声内容'
    try:
        from . import hypit_engine
        r=hypit_engine.transcribe_if_available(path,root/'speech','if_installed')
        speech=[{'start':x.get('start'),'end':x.get('end'),'text':x.get('text')} for x in (r.get('segments') or [])[:35] if isinstance(x,dict)]
        if speech:speech_state='本地语音识别结果（需复核）'
    except Exception:pass
    return {'asset_id':a['id'],'sha256':actual_hash,'duration':round(duration,2),'frames':frames,
            'parts':parts,'speech':speech,'speech_state':speech_state}
