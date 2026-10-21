"""V5.0.2: persistent hybrid workflow + local deterministic video producer.

OpenLux generation deliberately goes through the existing paid preview/commit task
endpoints. This module NEVER makes a paid request or retries one.
Official Hypit CLI bridge is optional; native local render works without it.
"""
from __future__ import annotations
import hashlib, json, os, re, shutil, subprocess, threading, time, uuid
from pathlib import Path
from . import config, db, media

SCHEMA='seedance-hybrid/v1'
HOME=config.DATA_DIR/'hybrid_workflow'
PROJECTS=HOME/'projects'
JOBS=HOME/'jobs'
CACHE=HOME/'render_cache'
for folder in (PROJECTS,JOBS,CACHE): folder.mkdir(parents=True,exist_ok=True)
LOCK=threading.RLock()
ACTIVE=set()
MAX_LOCAL_RENDERS=2
CHROME_FAILED=False  # Failed browser launches are not retried once per caption.


def _id():return uuid.uuid4().hex[:14]
def _read(p):
    if not p.is_file():return None
    return json.loads(p.read_text(encoding='utf-8'))
def _write(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_suffix(p.suffix+'.'+_id()+'.tmp')
    try:
        tmp.write_text(json.dumps(v,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(tmp,p)
    finally:
        if tmp.exists():tmp.unlink()
def _proj(pid):return PROJECTS/f'{pid}.json'
def _job(jid):return JOBS/f'{jid}.json'
def _valid_id(s):
    if not re.fullmatch('[a-f0-9]{14}',str(s or '')):raise ValueError('工程或任务编号无效')
    return s

def new_project(name='新建混合视频'):
    now=time.time(); pid=_id()
    p={'schema':SCHEMA,'id':pid,'name':str(name or '未命名工程')[:80],
       'size':'720x1280','fps':24,'shots':[], 'music_asset_id':None,
       'created_at':now,'updated_at':now,'revision':1}
    _write(_proj(pid),p);return p

def project(pid):
    p=_read(_proj(_valid_id(pid)))
    if not p:raise ValueError('工程不存在')
    return p

def list_projects():
    ans=[]
    for path in PROJECTS.glob('*.json'):
        try:
            p=_read(path)
            if p:ans.append({'id':p['id'],'name':p['name'],'updated_at':p['updated_at'],
                         'shots':len(p.get('shots') or []),'revision':p.get('revision')})
        except (OSError,ValueError,KeyError):pass
    return sorted(ans,key=lambda x:x['updated_at'],reverse=True)[:100]

def _asset(asset_id,kind=None):
    a=db.one('SELECT * FROM assets WHERE id=?',(str(asset_id or ''),))
    if not a:raise ValueError(f'素材 {asset_id} 已不存在')
    if kind and a['kind'] not in kind:raise ValueError(f'素材 {asset_id} 类型不匹配')
    path=Path(a['path']).resolve()
    if not path.is_file() or not path.is_relative_to(config.ASSET_DIR.resolve()):
        raise ValueError(f'素材 {asset_id} 不在受管理的本地素材目录中')
    return a,path

def _task_video(tid):
    t=db.one('SELECT * FROM tasks WHERE id=?',(str(tid or ''),))
    if not t:raise ValueError(f'OpenLux 任务 {tid} 不存在')
    if t['kind']!='video' or t['status']!='completed':
        raise ValueError(f'AI 镜头任务 {tid} 尚未完成；不会自动再次生成或重复扣费')
    result=db.loads(t.get('result_json'),{}) or {}
    filename=result.get('local_path') or result.get('generated_raw_path') or result.get('output_path')
    if not filename:raise ValueError('AI 任务尚无已下载的本地视频；请先在任务中心恢复下载')
    p=Path(filename).resolve()
    if not p.is_file() or not p.is_relative_to(config.OUTPUT_DIR.resolve()):
        raise ValueError('AI 任务的下载文件不存在或不属于受管理的输出目录')
    return p

def _shot(raw):
    if not isinstance(raw,dict):raise ValueError('镜头格式不正确')
    mode=raw.get('mode')
    if mode not in ('asset','ai','graphic'):raise ValueError('镜头类型必须为本地素材、AI 结果或图文卡片')
    duration=float(raw.get('duration') or 0)
    if not 0.5<=duration<=120:raise ValueError('每个镜头时长必须介于 0.5～120 秒')
    trim=float(raw.get('trim') or 0)
    if not 0<=trim<=21600:raise ValueError('镜头裁剪起点无效')
    s={'id':str(raw.get('id') or _id())[:48],'mode':mode,'duration':round(duration,3),
       'trim':round(trim,3),'caption':str(raw.get('caption') or '')[:160],
       'prompt':str(raw.get('prompt') or '')[:5000],
       'asset_id':str(raw.get('asset_id') or '')[:128],
       'task_id':str(raw.get('task_id') or '')[:128],
       'title':str(raw.get('title') or '')[:160]}
    if mode=='asset' and not s['asset_id']:raise ValueError('本地素材镜头缺少素材 ID')
    if mode=='ai' and not (s['task_id'] or s['prompt']):raise ValueError('AI 镜头需要提示词或已完成的生成任务')
    return s

def save_project(raw):
    if not isinstance(raw,dict):raise ValueError('工程数据格式错误')
    with LOCK:
        p=project(raw.get('id')); given_rev=int(raw.get('revision') or 0)
        if given_rev!=p['revision']:raise ValueError('工程已在其他页面更新，请先刷新，不覆盖其他编辑结果')
        ss=raw.get('shots')
        if not isinstance(ss,list) or len(ss)>40:raise ValueError('每个工程最多 40 个镜头')
        shots=[_shot(x) for x in ss]
        ids=[x['id'] for x in shots]
        if len(set(ids))!=len(ids):raise ValueError('镜头 ID 重复')
        name=str(raw.get('name') or '新建混合视频').strip()[:80]
        if raw.get('size') not in ('720x1280','1280x720','720x720'):
            raise ValueError('当前支持竖屏、横屏和方形 720p')
        music=raw.get('music_asset_id') or None
        if music:_asset(music,('audio',))
        p.update(name=name,shots=shots,size=raw['size'],music_asset_id=music,
                 updated_at=time.time(),revision=p['revision']+1)
        _write(_proj(p['id']),p)
        return p

def plan(pid):
    p=project(pid); ff=media.ffmpeg_path(); browser=find_chromium()
    shots=[]; total=0; problems=[]; ai_pending=0; ai_ready=0; local=0
    for i,s in enumerate(p.get('shots') or [],1):
        x={'index':i,'id':s['id'],'mode':s['mode'],'duration':s['duration'],'ready':True,'note':''}
        total+=s['duration']
        try:
            if s['mode']=='asset':
                a,path=_asset(s['asset_id'],('video','image'))
                if a['kind']=='video':
                    duration=(a.get('duration') or media.probe(path).get('duration') or 0)
                    if duration+0.25 < s['trim']+s['duration']:raise ValueError('素材时长不足，请缩短镜头或调整裁剪起点')
                local+=1
            elif s['mode']=='ai':
                if not s['task_id']:raise ValueError('需要先明确确认并生成 AI 镜头')
                src=_task_video(s['task_id'])
                meta=media.probe(src)
                if (meta.get('duration') or 0)+0.25<s['trim']+s['duration']:
                    raise ValueError('AI 素材实际时长不足')
                ai_ready+=1
            elif s['mode']=='graphic':
                local+=1
        except ValueError as e:
            x['ready']=False;x['note']=str(e);problems.append(f'镜头 {i}：{e}')
            if s['mode']=='ai':ai_pending+=1
        shots.append(x)
    if not shots:problems.append('请先添加镜头')
    if not ff:problems.append('未检测到 FFmpeg；本地渲染不可用')
    if p.get('music_asset_id'):
        try:_asset(p['music_asset_id'],('audio',))
        except ValueError as e:problems.append(str(e))
    return {'project_id':p['id'],'shots':shots,'duration':round(total,3),
            'local_count':local,'ai_ready_count':ai_ready,'ai_pending_count':ai_pending,
            'paid_create_calls':0,'new_openlux_cost_usd':0 if not ai_pending else None,
            'cost_note':'本地渲染不会调用 OpenLux。AI 镜头的预览和付费创建必须单独确认；未确认的镜头费用未知。',
            'ffmpeg':bool(ff),'chromium':bool(browser),'ready':not problems,'problems':problems}

def find_chromium():
    candidates=[shutil.which(x) for x in ('chromium','chromium-browser','google-chrome','chrome','msedge')]
    if os.name=='nt':
        roots=[Path(os.environ.get(x) or '') for x in ('PROGRAMFILES','PROGRAMFILES(X86)','LOCALAPPDATA')]
        for root in roots:
            if not str(root) or str(root)=='.':continue
            for suffix in ('Google/Chrome/Application/chrome.exe','Microsoft/Edge/Application/msedge.exe','Chromium/Application/chrome.exe'):
                candidates.append(str(root/suffix))
        local=config.ROOT/'tools'/'chromium'/'chrome.exe'
        candidates.append(str(local))
    return next((str(x) for x in candidates if x and Path(x).is_file()),None)

def _popen(cmd,timeout):
    opts={}
    if os.name=='nt':opts['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0)
    try:
        q=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=timeout,**opts)
    except subprocess.TimeoutExpired:raise RuntimeError('本地命令超时；没有发生 OpenLux 调用')
    if q.returncode:
        msg=(q.stderr or q.stdout).decode('utf-8','replace')[-1100:]
        raise RuntimeError(f'本地命令执行失败（{q.returncode}）：{msg}')

def _pillow_png(title,subtitle,w,h,out):
    """Verified CPU fallback: do not hang local production when Chrome is broken."""
    from PIL import Image,ImageDraw,ImageFont
    overlay=subtitle=='__OVERLAY__'
    im=Image.new('RGB',(w,h),'#00ff00' if overlay else '#111827')
    draw=ImageDraw.Draw(im)
    size=round(w*.06) if overlay else round(w*.071)
    fonts=[]
    if os.name=='nt':
        fonts=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/name for name in
               ('msyh.ttc','simhei.ttf','arial.ttf')]
    else:fonts=[Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font=None
    for f in fonts:
        try:font=ImageFont.truetype(str(f),size);break
        except (OSError,ValueError):continue
    font=font or ImageFont.load_default()
    words=str(title or '').strip();lines=[];cur=''
    # Keep English words together; wrap CJK text by characters.
    tokens=re.findall(r'[A-Za-z0-9]+(?:[-_][A-Za-z0-9]+)*\s*|.',words,re.S)
    for token in tokens:
        trial=cur+token
        if draw.textbbox((0,0),trial,font=font)[2]>w*.83 and cur.strip():
            lines.append(cur.rstrip());cur=token.lstrip()
        else:cur=trial
        # Pathological token longer than frame width, split safely by character.
        while cur and draw.textbbox((0,0),cur,font=font)[2]>w*.83 and len(cur)>1:
            lines.append(cur[:-1].rstrip());cur=cur[-1]
    if cur:lines.append(cur)
    lines=lines[:5]
    spacing=int(size*.28)
    row_h=int(size*1.32)
    block=len(lines)*row_h
    top=round(h*.82-block*.5) if overlay else round((h-block)/2)
    for i,line in enumerate(lines):
        bbox=draw.textbbox((0,0),line,font=font,stroke_width=0)
        x=(w-(bbox[2]-bbox[0]))//2
        draw.text((x,top+i*row_h),line,font=font,fill='white',stroke_width=max(1,int(size*.08)),stroke_fill='#10121a')
    if subtitle and not overlay:
        try:sf=ImageFont.truetype(str(fonts[0]),max(14,round(size*.5)))
        except (OSError,ValueError):sf=font
        sub=str(subtitle)[:70]
        bbox=draw.textbbox((0,0),sub,font=sf)
        draw.text(((w-(bbox[2]-bbox[0]))//2,min(h-35,top+block+25)),sub,font=sf,fill='#cbd5e1')
    im.save(out)

def _html_png(title,subtitle,w,h,out):
    global CHROME_FAILED
    browser=None if CHROME_FAILED else find_chromium()
    if not browser:
        _pillow_png(title,subtitle,w,h,out)
        return
    import html
    page=out.with_suffix('.html')
    # Green chroma for overlays. Graphic cards use the same capture but fill background.
    is_overlay=subtitle=='__OVERLAY__'
    fg='#FFFFFF'; bg='#00ff00' if is_overlay else '#111827'
    size=round(w*0.06) if is_overlay else round(w*.071)
    y='82%' if is_overlay else '50%'
    style=f'''html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden;background:{bg};}}
    body{{display:flex;align-items:center;justify-content:center;font-family:"Microsoft YaHei","Segoe UI",sans-serif;color:{fg};}}
    .a{{position:absolute;top:{y};width:89%;text-align:center;transform:translateY(-50%);font-size:{size}px;font-weight:800;line-height:1.25;overflow-wrap:anywhere;{('text-shadow:0 3px 7px #050505,0 0 2px #050505;' if is_overlay else '')}}}
    small{{display:block;font-size:{round(size*.5)}px;margin-top:14px;color:#ccd5df;}}'''
    page.write_text('<!doctype html><meta charset="utf-8"><style>'+style+'</style><div class="a">'+html.escape(title)+('' if is_overlay else '<small>'+html.escape(subtitle)+'</small>')+'</div>',encoding='utf-8')
    cmd=[browser,'--headless','--disable-gpu','--hide-scrollbars','--no-first-run','--no-default-browser-check',
         '--force-device-scale-factor=1',f'--window-size={w},{h}',f'--screenshot={out}',page.as_uri()]
    if os.name!='nt':cmd.insert(1,'--no-sandbox')
    try:
        _popen(cmd,15)
        if not out.is_file() or out.stat().st_size<100:raise RuntimeError('Chromium 未生成图片')
    except (OSError,ValueError,RuntimeError) as exc:
        CHROME_FAILED=True
        # Browser is an optional rendering backend; Pillow can still produce
        # the same local PNG, with no model call or silent lost captions.
        _pillow_png(title,subtitle,w,h,out)
    finally:page.unlink(missing_ok=True)

def _render_shot(s,p):
    ff=media.ffmpeg_path()
    w,h=map(int,p['size'].split('x'))
    src=None; is_image=False
    if s['mode']=='graphic':
        # Normalize Chromium image to target dimensions in FFmpeg
        ident=hashlib.sha256(json.dumps([s,p['size']],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:20]
        src=CACHE/f'card_{ident}.png'
        if not src.exists():_html_png(s['title'] or s['caption'] or '标题','',w,h,src)
        is_image=True
    elif s['mode']=='ai':src=_task_video(s['task_id'])
    else:
        a,src=_asset(s['asset_id'],('video','image'));is_image=a['kind']=='image'
    if not src:raise ValueError('缺少有效镜头素材')
    stamp=f'{src.stat().st_size}_{src.stat().st_mtime_ns}'
    digest=hashlib.sha256(json.dumps([s,p['size'],p['fps'],stamp],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:26]
    output=CACHE/f'part_{digest}.mp4'
    if output.is_file() and output.stat().st_size>1024:return output,True
    tmp=CACHE/f'{digest}_{_id()}.mp4'
    caption=s['caption'] if s['mode']!='graphic' else ''
    chroma=CACHE/f'caption_{hashlib.sha256((caption+p["size"]).encode()).hexdigest()[:22]}.png'
    if caption and not chroma.exists():_html_png(caption,'__OVERLAY__',w,h,chroma)
    vf=f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={p["fps"]},format=yuv420p'
    cmd=[ff,'-hide_banner','-loglevel','error','-y']
    if is_image:cmd+=['-loop','1','-framerate',str(p['fps']),'-i',str(src)]
    else:cmd+=['-ss',str(s['trim']),'-i',str(src)]
    has_audio=(not is_image and bool(media.probe(src).get('has_audio')))
    # Audio stream always present in segments, including silent images/cards.
    if not has_audio:cmd+=['-f','lavfi','-i','anullsrc=r=48000:cl=stereo']
    if caption:
        cmd+=['-loop','1','-i',str(chroma)]
        ix=1 if has_audio else 2
        filt=f'[0:v]{vf}[v0];[{ix}:v]scale={w}:{h},format=rgba,colorkey=0x00ff00:0.20:0.07[cap];[v0][cap]overlay=0:0:shortest=1[v]'
        cmd+=['-filter_complex',filt,'-map','[v]']
    else:cmd+=['-vf',vf,'-map','0:v:0']
    cmd+=['-map',('0:a:0' if has_audio else '1:a:0'),'-t',str(s['duration']),
          '-c:v','libx264','-preset','veryfast','-crf','21','-r',str(p['fps']),
          '-c:a','aac','-b:a','128k','-ar','48000','-ac','2','-pix_fmt','yuv420p','-movflags','+faststart',str(tmp)]
    try:
        _popen(cmd,max(90,int(s['duration']*30)+90))
        if not tmp.exists() or tmp.stat().st_size<1024:raise RuntimeError('FFmpeg 输出片段为空')
        os.replace(tmp,output)
        return output,False
    finally:tmp.unlink(missing_ok=True)

def _set_job(jid,**kwargs):
    with LOCK:
        info=_read(_job(jid))
        if info:info.update(kwargs,updated_at=time.time());_write(_job(jid),info)

def _worker(jid, p):
    try:
        shots=p['shots']; parts=[]; cache_hits=0
        for i,s in enumerate(shots):
            _set_job(jid,step=f'合成镜头 {i+1}/{len(shots)}',progress=round(80*i/len(shots)))
            part,hit=_render_shot(s,p);parts.append(part);cache_hits+=int(hit)
        work=CACHE/f'concat_{jid}.txt'
        try:
            # ffmpeg concat demuxer quoting; cache paths are controlled by us.
            work.write_text(''.join("file '"+str(x).replace('\\','/').replace("'","'\\''")+"'\n" for x in parts),encoding='utf-8')
            dst=config.OUTPUT_DIR/f'hybrid_{p["id"]}_{jid}.mp4'
            tmp=dst.with_name(dst.stem+'_tmp.mp4')
            _set_job(jid,step='合并镜头与音轨',progress=85)
            ff=media.ffmpeg_path()
            base=[ff,'-hide_banner','-loglevel','error','-y','-f','concat','-safe','0','-i',str(work)]
            if p.get('music_asset_id'):
                _,music=_asset(p['music_asset_id'],('audio',))
                cmd=base+['-stream_loop','-1','-i',str(music),'-filter_complex','[0:a][1:a]amix=inputs=2:duration=first:weights=1 0.35[a]','-map','0:v:0','-map','[a]','-c:v','copy','-c:a','aac','-b:a','160k','-t',str(round(sum(s['duration'] for s in shots),3)),'-movflags','+faststart',str(tmp)]
            else:cmd=base+['-c','copy','-movflags','+faststart',str(tmp)]
            try:
                _popen(cmd,max(120,int(sum(x['duration'] for x in shots)*6)+90))
                if not tmp.is_file() or tmp.stat().st_size<1024:raise RuntimeError('本地成片为空')
                os.replace(tmp,dst)
            finally:tmp.unlink(missing_ok=True)
            _set_job(jid,status='completed',step='本地合成完成',progress=100,
                     output='/outputs/'+dst.name,output_path=str(dst),cache_hits=cache_hits)
        finally:work.unlink(missing_ok=True)
    except Exception as e:
        _set_job(jid,status='failed',step='本地渲染失败',error=f'{type(e).__name__}: {e}'[:1600])
    finally:
        with LOCK:ACTIVE.discard(jid)

def start_render(pid):
    with LOCK:
        p=project(pid); checked=plan(pid)
        if not checked['ready']:raise ValueError('预检未通过：'+'；'.join(checked['problems']))
        if len(ACTIVE)>=MAX_LOCAL_RENDERS:raise ValueError('已有 2 个本地合成任务运行，请稍后再执行')
        jid=_id();now=time.time()
        job={'id':jid,'project_id':pid,'revision':p['revision'],'status':'running',
             'step':'本地渲染已创建','progress':0,'created_at':now,'updated_at':now,
             'error':None,'output':None,'cache_hits':0,'openlux_create_calls':0}
        _write(_job(jid),job);ACTIVE.add(jid)
        threading.Thread(target=_worker,args=(jid,json.loads(json.dumps(p))),daemon=True,name=f'hybrid-render-{jid}').start()
        return job

def get_job(jid):
    j=_read(_job(_valid_id(jid)))
    if not j:raise ValueError('渲染任务不存在')
    if j['status']=='running' and jid not in ACTIVE:
        j['status']='interrupted';j['error']='工作台重启或进程已终止；不会自动发起 AI 付费调用，可重新点击本地渲染（已有缓存复用）。'
        _write(_job(jid),j)
    return j

def jobs(pid):
    _valid_id(pid)
    output=[]
    for f in JOBS.glob('*.json'):
        try:
            j=_read(f)
            if j.get('project_id')==pid:output.append(get_job(j['id']))
        except (ValueError,KeyError,TypeError):pass
    return sorted(output,key=lambda x:x['created_at'],reverse=True)[:15]

def diagnostics():
    cli=shutil.which('hypit')
    info={'native':'ready' if media.ffmpeg_path() else 'missing_ffmpeg',
          'ffmpeg':media.ffmpeg_path(),'ffprobe':media.ffprobe_path(),
          'chromium':find_chromium(),'chromium_failed':CHROME_FAILED,'chromium_fallback':'Pillow 本地备用渲染，浏览器无响应时自动回退，不产生云端费用','hypit_cli':cli,
          'hypit_mode':'可选官方 CLI 桥接；当前可视化工程由 Studio 本地引擎编排，不会假称官方 Hypit 已执行',
          'paid_model':'OpenLux 经工作台现有预览→用户确认→防重提交 API 执行；本模块不会收费',
          'project_dir':str(HOME)}
    return info

def hypit_plan(project_id,source='build.svrun'):
    """Read-only call to the *real* Hypit CLI, when installed.

    Users place an actual .svrun and its dependencies inside the allowlisted
    /data/hybrid_workflow/hypit_projects/<project-id>/ directory. CLI owns source grammar.
    """
    cli=shutil.which('hypit')
    if not cli:raise ValueError('尚未安装 Hypit 官方 CLI。可继续使用本地 FFmpeg / Chromium 工作流。')
    _valid_id(project_id)
    if not re.fullmatch('[A-Za-z0-9._/-]{1,100}',source) or '..' in Path(source).parts or not source.endswith('.svrun'):
        raise ValueError('Run Source 必须是工程内的 .svrun 文件')
    root=HOME/'hypit_projects'/project_id
    src=(root/source).resolve()
    if not src.is_relative_to(root.resolve()) or not src.is_file():
        raise ValueError('未找到 Hypit 的 .svrun 工程文件；不能凭空生成并提交实际工作流')
    opts={'cwd':str(root)}
    if os.name=='nt':opts['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0)
    try:p=subprocess.run([cli,'plan',str(src)],capture_output=True,timeout=45,**opts)
    except subprocess.TimeoutExpired:raise ValueError('Hypit 计划预检超时')
    return {'ok':p.returncode==0,'exit_code':p.returncode,
            'output':(p.stdout+p.stderr).decode('utf-8','replace')[-12000:],
            'submitted':False,'paid_create_calls':0}


# V5.0.3 simplified viral replica UI. Hypit-style analysis runs via the
# existing /api/workflow/analyze endpoint; the paid preview/commit firewall is
# unchanged. These helpers link the durable paid task to local FFmpeg rendering.
def simple_create(reference_asset_id, product_asset_ids=None, instruction=''):
    products=list(dict.fromkeys(str(x) for x in (product_asset_ids or []) if x))
    if len(products)>30:raise ValueError('商品参考图最多 30 张')
    reference,ref_path=_asset(reference_asset_id,('video',))
    meta=media.probe(ref_path)
    duration=float(meta.get('duration') or reference.get('duration') or 0)
    if not 4<=duration<=30.5:
        raise ValueError(f'参考视频约 {duration:.1f} 秒；当前复刻只支持 4～30 秒，请先剪短参考视频，未产生 OpenLux 费用')
    if not meta.get('width') or not meta.get('height'):
        raise ValueError('无法识别参考视频尺寸，请检查 FFmpeg，未产生 OpenLux 费用')
    for aid in products:_asset(aid,('image',))
    p=new_project('复刻 · '+str(reference.get('original_name') or '视频')[:55])
    width=int(meta['width']);height=int(meta['height'])
    size='720x1280' if height>width else ('1280x720' if width>height else '720x720')
    # Do not write private credentials or reference file data into JSON.
    p['size']=size
    p['simple']={'reference_asset_id':str(reference_asset_id),'product_asset_ids':products,
                 'instruction':str(instruction or '')[:3000],
                 'source_duration':round(duration,3),'workflow_version_id':None,
                 'created_at':time.time()}
    _write(_proj(p['id']),p)
    return p

def simple_link(pid, workflow_version_id):
    with LOCK:
        p=project(pid); simple=p.get('simple')
        if not simple:raise ValueError('此工程不是简易复刻工程')
        if simple.get('workflow_version_id') and simple['workflow_version_id']!=workflow_version_id:
            raise ValueError('工程已关联其他分析版本，请新建复刻任务')
        row=db.one('SELECT * FROM workflows WHERE version_id=?',(str(workflow_version_id or ''),))
        if not row or not row['confirmed']:
            raise ValueError('分析工作流尚未确认，不能进入付费预览')
        if row['reference_asset_id']!=simple['reference_asset_id'] or (db.loads(row['product_asset_ids_json'],[]) or [])!=simple['product_asset_ids']:
            raise ValueError('分析任务素材与当前工程不符，已阻止错误绑定')
        if str(row.get('instruction') or '').strip()!=simple['instruction'].strip():
            raise ValueError('分析提示词与当前工程不符，已阻止错误绑定')
        simple['workflow_version_id']=str(workflow_version_id)
        p['updated_at']=time.time();p['revision']+=1
        _write(_proj(pid),p)
        return p

def simple_workflow_owner(workflow_version_id):
    """Return single durable V5 Hypit simple project owner, not a UI-limited list.

    If data are ambiguous/corrupt, fail closed instead of silently bypassing
    the paid preflight gate.
    """
    matched=[]
    key=str(workflow_version_id or '')
    for path in PROJECTS.glob('*.json'):
        try:
            record=_read(path)
        except (ValueError,OSError) as exc:
            # Project-data corruption is not permission to bypass paid safeguards.
            raise ValueError('本地复刻工程数据损坏，已阻止付费，请先修复项目：'+path.name) from exc
        if not isinstance(record,dict):continue
        simple=record.get('simple')
        if isinstance(simple,dict) and str(simple.get('workflow_version_id') or '')==key:
            matched.append(record['id'])
    if len(matched)>1:raise ValueError('同一已确认工作流绑定多个复刻工程，禁止重复付费')
    return matched[0] if matched else None


def _simple_task(version_id, rows=None):
    if not version_id:return None
    if rows is None:
        rows=db.all_("SELECT * FROM tasks WHERE kind='video' AND request_json LIKE ? ORDER BY created_at DESC LIMIT 50",('%'+str(version_id)+'%',))
    for task in rows:
        req=db.loads(task.get('request_json'),{}) or {}
        if req.get('workflow_version_id')==version_id and req.get('mode')=='replica':return task
    # The project may be older than the rolling in-memory task window.
    # Never mistake that for "no paid submission" and allow a new charge.
    if rows is not None:
        older=db.all_("SELECT * FROM tasks WHERE kind='video' AND request_json LIKE ? ORDER BY created_at DESC LIMIT 50",('%'+str(version_id)+'%',))
        for task in older:
            req=db.loads(task.get('request_json'),{}) or {}
            if req.get('workflow_version_id')==version_id and req.get('mode')=='replica':return task
    return None

def _simple_status(pid, rows=None, auto_render=True):
    with LOCK:
        p=project(pid); simple=p.get('simple')
        if not simple:raise ValueError('这不是简易复刻项目')
        task=_simple_task(simple.get('workflow_version_id'),rows)
        native=None
        native_error=None
        if task and task.get('status')=='completed' and auto_render:
            try:
                from . import hypit_official
                src=_task_video(task['id'])
                native=hypit_official.auto_resume(pid,str(src))
            except Exception as exc:
                native_error=str(exc)
                try:
                    from . import hypit_official
                    native=hypit_official.original_project_for_replica(pid)
                except Exception:pass
        elif auto_render:
            try:
                from . import hypit_official
                native=hypit_official.original_project_for_replica(pid)
            except Exception:pass
        js=jobs(pid)
        # V5.1+: the new one-menu path MUST use the genuine Hypit CLI. Legacy local
        # FFmpeg jobs remain READABLE for old tasks, but are never newly submitted.
        if False and task and task.get('status')=='completed' and not js and auto_render and not native:
            phase='download'
            try:
                src=_task_video(task['id'])
                phase='render'
                secs=float(media.probe(src).get('duration') or 0)
                if secs<.5:raise ValueError('生成结果视频时长读取失败，无法本地合成')
                if not p.get('shots'):
                    p['shots']=[_shot({'mode':'ai','duration':min(120,round(secs,3)),
                                      'task_id':task['id'],'prompt':simple.get('instruction') or '爆款复刻',
                                      'trim':0,'caption':''})]
                    p['updated_at']=time.time();p['revision']+=1;_write(_proj(pid),p)
                start_render(pid)
                js=jobs(pid)
            except Exception as exc:
                missing=phase=='download'
                return {'project_id':pid,'project_name':p['name'],
                        'stage':'download_error' if missing else 'render_error',
                        'message':('AI 视频已生成，但本地视频文件尚不可用；请点击恢复下载，不会重新生成付费视频。' if missing else '本地合成未完成：'+str(exc)[:240]),
                        'reference_asset_id':simple['reference_asset_id'],
                        'product_asset_ids':simple.get('product_asset_ids',[]),
                        'instruction':simple.get('instruction',''),
                        'task_id':task['id'],'task_status':'completed','output':None}
        job=js[0] if js else None
        stage='draft'; message='上传参考视频和商品图，然后点击开始复刻'
        if simple.get('workflow_version_id'):stage='ready';message='已解析参考视频，可预览本次生成费用'
        if task:
            status=task['status']
            if status in ('pending','running','result_pending'):stage='generating';message='AI 视频生成中，任务会在后台继续运行'
            elif status=='submission_unknown':stage='uncertain';message='提交结果不确定，请在任务中心核对，禁止重复付费提交'
            elif status=='failed':stage='failed';message='AI 视频生成失败，请查看任务中心；不会自动重试扣费'
            elif status=='completed':stage='composing';message='AI 视频已完成，正在本地制作成片'
        if job:
            if job['status']=='completed':stage='completed';message='视频已完成，可预览或下载'
            elif job['status']=='failed':stage='render_error';message='本地合成失败；不涉及新的模型调用'
            elif job['status']=='interrupted':stage='interrupted';message='本地合成被中断，可重新合成，已完成的 AI 视频不会重收费'
            else:stage='composing';message='正在本地合成视频'
        if native:
            ns=native.get('status','')
            official_steps={
                'waiting_for_openlux':('ready','等待 OpenLux 视频生成'),
                'needs_runtime':('needs_setup','Hypit 官方运行时依赖尚未就绪'),
                'ready_to_plan':('needs_setup','已准备官方工程；等待 Hypit CLI 编译'),
                'building':('composing','官方 Hypit Runtime 正在构建工程'),
                'submitted':('composing','官方 Hypit Build 已提交；等待完成'),
                'completed':('completed','已由官方 Hypit Runtime 生成可编辑工程与成片'),
                'build_unknown':('manual_review','官方提交状态不明，请核对 Build ID，禁止重复提交'),
                'source_invalid':('manual_review','官方 Hypit 编译失败，需检查工程'),
                'manual_review':('manual_review','官方 Hypit 计划中存在可能的付费请求，已停止'),
                'failed':('render_error','官方 Hypit 构建失败'),
                'export_error':('render_error','Hypit 已构建但导出失败'),
            }
            if ns in official_steps:stage,message=official_steps[ns]
            if ns=='ready_to_plan':
                try:
                    from . import hypit_official
                    if hypit_official.health().get('installed'):
                        stage='composing';message='官方 Hypit 已就绪，正在检查工程并准备构建'
                except Exception:pass
            if native.get('error'):message=(message+'：'+str(native['error']))[:500]
        if task and task.get('status')=='completed' and native is None and native_error:
            stage='manual_review';message='已生成 OpenLux 视频，但无法创建官方 Hypit 工程：'+native_error[:280]
        return {'project_id':pid,'project_name':p['name'], 'stage':stage,'message':message,
                'reference_asset_id':simple['reference_asset_id'],
                'product_asset_ids':simple.get('product_asset_ids',[]),
                'instruction':simple.get('instruction',''),
                'source_duration':simple.get('source_duration'),
                'workflow_version_id':simple.get('workflow_version_id'),
                'task_id':task['id'] if task else None,'task_status':task['status'] if task else None,
                'progress':(job or {}).get('progress',0),'render_job_id':(job or {}).get('id'),
                'output':(native or {}).get('output') or (job or {}).get('output'),
                'hypit_project_id':(native or {}).get('id'),
                'hypit_build_id':(native or {}).get('official_build_id'),
                'hypit_status':(native or {}).get('status'),
                'error':(job or {}).get('error') or (task or {}).get('error'),
                'cache_hits':(job or {}).get('cache_hits',0)}

def simple_status(pid):return _simple_status(pid)

def simple_projects():
    return [p for p in list_projects() if (project(p['id']).get('simple'))][:25]

def simple_retry_local_render(pid):
    with LOCK:
        status=_simple_status(pid,auto_render=False)
        if status.get('task_status')!='completed':raise ValueError('AI 视频尚未生成完成，不能开始本地合成')
        if status['stage'] not in ('interrupted','render_error'):raise ValueError('当前不需要重新合成')
        raise ValueError('新版复刻只允许官方 Hypit 构建；旧版任务可在历史记录中读取成片，不自动触发仿制本地渲染')

def simple_auto_tick():
    """Called by the existing background worker; NEVER initiates a paid request."""
    try:
        pp=simple_projects()
        if not pp:return
        rows=db.all_("SELECT * FROM tasks WHERE kind='video' ORDER BY created_at DESC LIMIT 180")
        for one in pp:
            try:_simple_status(one['id'],rows,auto_render=True)
            except Exception:pass
    except Exception:pass
