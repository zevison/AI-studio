from __future__ import annotations
# Seedance V4.2.3 Unified History
import argparse, base64, io, json, logging, mimetypes, re, os, subprocess, sys, threading, time, urllib.parse, uuid, webbrowser, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Write a non-sensitive boot phase before potentially expensive imports.
# 04_DEBUG.cmd uses the same steps; no provider API is called here.
def _boot_phase(stage):
    try:
        bootdir = Path(__file__).resolve().parent / 'data'
        workspace=os.getenv('STUDIO_WORKSPACE_ID','owner')
        if re.fullmatch('[a-f0-9]{24}',workspace):bootdir=bootdir/'workspaces'/workspace
        bootdir.mkdir(parents=True, exist_ok=True)
        (bootdir / 'startup_stage.txt').write_text(
            f'{time.strftime("%Y-%m-%d %H:%M:%S")} | {stage} | pid={os.getpid()}\n', encoding='utf-8')
    except Exception:
        pass

_boot_phase('导入后台模块中')

from core import config, db, media, modes, openlux, tunnel, workflow, settings, oss, secrets_store, public_assets, reference_logic, cost_system, media_preflight, production_system, smart_analysis, system_health, workspace_store, backup_manager
from core import prompt_library, audio_subjects, hot_radar, product_memory, model_pricing, creative_production, video_model_tests, video_adapters, task_observability, intelligent_router, audio_output, ai_assistant, gpt_memory_v4, gpt_pro_v41, bright_tiktok, viral_review, bright_e2e_models, voice_bindings
from core import history_center_v423
from core import local_output, upload_lifecycle, team_safety, storage_manager
from core import video_evaluation
from core.hypit_studio import service as hypit_service
import secrets
_hypit_session_token = secrets.token_urlsafe(32)
from core.utils import fingerprint, guess_kind, json_bytes, parse_multipart, safe_name, sha256_bytes, find_url
_boot_phase("后台模块已加载，准备初始化数据库")

from logging.handlers import RotatingFileHandler
logging.basicConfig(handlers=[RotatingFileHandler(config.LOG_PATH,maxBytes=10*1024*1024,backupCount=3,encoding='utf-8')],level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
log=logging.getLogger('seedance-studio')

# Video task polling is server-owned, not browser-owned. This makes running tasks
# survive page refreshes, browser closes, and local service restarts.
_task_refresh_locks = tuple(threading.RLock() for _ in range(64))
_task_poller_stop = threading.Event()


def _task_lock(task_id):
    # Bounded lock storage, with unrelated tasks free to query or download.
    return _task_refresh_locks[hash(str(task_id)) % len(_task_refresh_locks)]


def ratio_from_dims(w,h):
    if not w or not h:return config.DEFAULT_RATIO
    x=float(w)/float(h)
    opts={'16:9':16/9,'9:16':9/16,'1:1':1.0,'4:3':4/3,'3:4':3/4,'21:9':21/9}
    return min(opts,key=lambda k:abs(opts[k]-x))


def asset_row(asset_id):
    if upload_lifecycle.removed('asset',asset_id):raise ValueError('参考文件已删除，请重新选择并预览')
    return db.one('SELECT * FROM assets WHERE id=?',(asset_id,))


def asset_public(a):
    if not a:return None
    d=dict(a)
    stored=d.get('stored_name') or Path(d.get('path') or '').name
    d['stored_name']=stored
    d['original_name']=d.get('original_name') or stored or d.get('id')
    d['url']='/public/assets/'+urllib.parse.quote(stored, safe='') if stored else None
    return d


def _refresh_asset_media_metadata(a:dict, force=False):
    """Repair stale media rows created while local probing was unavailable.

    Old V2.4/V2.5 rows may contain duration=0 / width=NULL. Re-uploading the same
    SHA used to return that stale row forever. This function re-probes locally and
    updates only media metadata; it never changes the asset ID or user file.
    """
    if not a or a.get('kind') not in ('video','audio'):
        return a
    path=Path(a.get('path') or '')
    if not path.exists():
        return a
    stale=(not a.get('duration')) or (a.get('kind')=='video' and (not a.get('width') or not a.get('height')))
    if not (force or stale):
        return a
    meta=media.probe(path)
    if meta.get('ok') or (a.get('kind')=='audio' and meta.get('duration')):
        db.execute('''UPDATE assets SET duration=?,width=?,height=?,fps=?,has_audio=? WHERE id=?''',
                   (meta.get('duration'),meta.get('width'),meta.get('height'),meta.get('fps'),1 if meta.get('has_audio') else 0,a['id']))
        return asset_row(a['id'])
    return dict(a, _probe_error=meta.get('error',''), _probe_backend=meta.get('backend'))


def save_asset_bytes(data:bytes, original_name:str, mime:str, kind=None):
    kind=kind or guess_kind(original_name,mime)
    if kind=='image':
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:image.verify()
    digest=sha256_bytes(data)
    existing=db.one('SELECT * FROM assets WHERE sha256=? ORDER BY created_at DESC LIMIT 1',(digest,))
    if existing and Path(existing.get('path') or '').exists():
        # Critical migration fix: same-file reuploads must not preserve stale duration=0 metadata.
        return _refresh_asset_media_metadata(existing)
    kind=kind or guess_kind(original_name,mime); aid=db.new_id('asset')
    ext=Path(original_name).suffix.lower()
    if not ext:
        ext=mimetypes.guess_extension(mime or '') or ('.png' if kind=='image' else '.mp4' if kind=='video' else '.bin')
    storage_manager.require_capacity(len(data))
    stored=f'{aid}{ext}'; path=config.ASSET_DIR/stored; path.write_bytes(data)
    storage_manager.invalidate()
    meta=media.probe(path) if kind in ('video','audio') else ({'duration':None,**media.image_probe(path)} if kind=='image' else {'duration':None,'width':None,'height':None})
    db.execute('''INSERT INTO assets(id,original_name,stored_name,kind,mime,path,sha256,size_bytes,duration,width,height,fps,has_audio,created_at)
                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
               (aid,safe_name(original_name),stored,kind,mime or mimetypes.guess_type(stored)[0] or 'application/octet-stream',
                str(path),digest,len(data),meta.get('duration'),meta.get('width'),meta.get('height'),meta.get('fps'),1 if meta.get('has_audio') else 0,db.now()))
    return asset_row(aid)


def _extract_b64(resp):
    if isinstance(resp,dict):
        if isinstance(resp.get('b64_json'),str): return resp['b64_json']
        for k in ('data','output','result','results','images'):
            v=resp.get(k)
            if isinstance(v,list):
                for x in v:
                    z=_extract_b64(x)
                    if z:return z
            elif isinstance(v,dict):
                z=_extract_b64(v)
                if z:return z
    return None


def save_generated_image(resp):
    url=find_url(resp)
    if url:
        ext=Path(urllib.parse.urlparse(url).path).suffix.lower()
        if ext not in ('.png','.jpg','.jpeg','.webp'): ext='.jpg'
        dst=config.OUTPUT_DIR/f'image_{int(time.time())}_{uuid.uuid4().hex[:6]}{ext}'
        openlux.download(url,dst); data=dst.read_bytes()
        a=save_asset_bytes(data,dst.name,mimetypes.guess_type(dst.name)[0] or 'image/jpeg','image')
        return a,str(dst)
    b64=_extract_b64(resp)
    if b64:
        if ',' in b64 and b64.lower().startswith('data:'): b64=b64.split(',',1)[1]
        data=base64.b64decode(b64)
        from PIL import Image
        with Image.open(io.BytesIO(data)) as image:image.verify()
        dst=config.OUTPUT_DIR/f'image_{int(time.time())}_{uuid.uuid4().hex[:6]}.png'; dst.write_bytes(data)
        a=save_asset_bytes(data,dst.name,'image/png','image'); return a,str(dst)
    raise RuntimeError('接口已返回成功响应，但没有找到图片 URL 或 Base64 图片数据。')


def deepen_prompt(prompt, level):
    guide={
        'light':'轻微修改：严格保留主体身份、产品外观、构图与主要光线，只调整我明确要求的细节。',
        'medium':'中等修改：保留主体和产品识别一致性，可调整构图、环境、动作和光线，但不要改变未要求变化的关键外观。',
        'strong':'明显修改：允许较大幅度重构场景、构图和动作，但产品主体必须保持准确可识别，仍只执行用户要求的变化。'
    }.get(level,'中等修改：保留主体与产品一致性，只执行明确要求的变化。')
    return guide+'\n用户修改要求：'+(prompt or '').strip()


def _provider_status_text(resp):
    if not isinstance(resp,dict): return None
    vals=[]
    for k in ('status','task_status','state','phase'):
        if resp.get(k) is not None: vals.append(resp.get(k))
    for k in ('data','result','task'):
        d=resp.get(k)
        if isinstance(d,dict):
            for sk in ('status','task_status','state','phase'):
                if d.get(sk) is not None: vals.append(d.get(sk))
    for v in vals:
        if v not in (None,''): return str(v)
    return None


def _task_progress(t, req, resp, result):
    status=t.get('status') or 'pending'
    pid=t.get('provider_task_id')
    mode=req.get('mode') or 'direct'
    provider_label=str(req.get('_provider_adapter') or req.get('_request_model') or req.get('model') or 'OpenLux 视频模型')
    media=req.get('media') or []
    has_media=bool(media)
    has_remote=bool((result or {}).get('remote_url'))
    has_local=bool((result or {}).get('local_url') or (result or {}).get('local_path'))
    download_error=(result or {}).get('download_error')
    failed=status=='failed'
    uncertain=status=='submission_unknown'
    completed=status=='completed'
    result_pending=status=='result_pending'
    running=status in ('pending','running','result_pending')
    stages=[]
    def add(key,label,state,detail=''):
        stages.append({'key':key,'label':label,'state':state,'detail':detail})
    if mode=='replica':
        add('workflow','工作流确认','done','已确认本次使用的复刻工作流版本')
    else:
        add('confirm','调用确认','done','已完成“预览 → 确认”并创建本地任务')
    if has_media:
        add('assets','参考素材准备','done','公网素材已准备完成，任务创建前已通过本地前置检查')
    else:
        add('assets','参考素材准备','skipped','本次任务无需参考素材')
    if pid:
        add('submit','提交 OpenLux 视频任务','done',f'平台任务号：{pid}')
    elif uncertain:
        add('submit','提交 OpenLux 视频任务','unknown','提交结果待核对；不能认定失败，禁止自动重提')
    elif failed:
        add('submit','提交 OpenLux 视频任务','failed','提交结果失败或不确定，系统不会自动重提')
    else:
        add('submit','提交 OpenLux 视频任务','active','正在提交付费视频任务')
    if uncertain:
        add('generate','视频模型生成','unknown','平台结果尚未核实；保留原任务及费用状态')
    elif failed and pid:
        add('generate','视频模型生成','failed','平台任务返回失败')
    elif completed or result_pending:
        add('generate','视频模型生成','done','平台任务已结束')
    elif pid and running:
        add('generate','视频模型生成','active','正在等待平台生成结果')
    else:
        add('generate','视频模型生成','waiting','等待任务提交成功')
    if uncertain:
        add('result','获取生成结果','unknown','等待查询原任务或人工对账，不创建新任务')
    elif failed:
        add('result','获取生成结果','waiting','任务失败，未进入结果获取')
    elif completed and has_remote:
        add('result','获取生成结果','done','已获得平台结果地址')
    elif result_pending or completed:
        add('result','获取生成结果','active','平台已结束，正在同步最终结果地址')
    else:
        add('result','获取生成结果','waiting','等待所选视频模型完成')
    if has_local:
        add('save','保存到本地','done','结果已保存到本地输出目录')
    elif download_error:
        add('save','保存到本地','failed',str(download_error)[:280])
    elif completed and has_remote:
        add('save','保存到本地','active','正在下载结果到本地输出目录')
    else:
        add('save','保存到本地','waiting','等待平台结果')
    if completed and has_local:
        add('complete','完成','done','视频已成功保存到本地')
    elif completed and has_remote:
        add('complete','完成','active','远端视频已就绪，等待本地下载成功')
    elif uncertain:
        add('complete','完成','unknown','结果尚未核实')
    elif failed:
        add('complete','完成','failed','任务未成功完成')
    else:
        add('complete','完成','waiting','等待前序步骤完成')
    visible=[x for x in stages if x['state']!='skipped']
    done=sum(1 for x in visible if x['state']=='done')
    active=next((x for x in stages if x['state'] in ('active','failed','unknown')),None)
    if not active:
        active=next((x for x in stages if x['state']=='waiting'), stages[-1] if stages else None)
    percent=round((done/max(1,len(visible)))*100)
    if completed and has_local: percent=100
    return {
        'stages':stages,
        'current_key':(active or {}).get('key'),
        'current_label':(active or {}).get('label'),
        'stage_percent':percent,
        'stage_done':done,
        'stage_total':len(visible),
        'note':'这里显示的是本地流程阶段，并非模型内部渲染百分比；实际模型：'+provider_label,
        'provider_status':_provider_status_text(resp),
        'elapsed_seconds':max(0,int((float(t.get('updated_at') or t.get('created_at') or db.now()) if t.get('status') in {'completed','failed','cancelled','canceled','rejected'} else db.now())-float(t.get('created_at') or db.now()))),
        'query_count':int(t.get('query_count') or 0),
        'last_polled_at':t.get('last_polled_at'),
        'last_query_error':t.get('last_query_error')
    }


def _finish_audio_for_result(result, reqmeta):
    """After downloaded: save soundtrack variant without making another paid call."""
    mode=audio_output.normalize(reqmeta.get('audio_mode'),legacy_audio=reqmeta.get('generate_audio',True))
    result['audio_mode']=mode
    raw=result.get('generated_raw_path') or result.get('local_path')
    if not raw:raise ValueError('原始视频尚未下载')
    result['generated_raw_path']=raw
    final=audio_output.apply(raw,mode,reqmeta.get('audio_source_asset_id'),asset_row,
                             source_ids=reqmeta.get('audio_source_asset_ids'),target_resolution=reqmeta.get('local_export_resolution'))
    if reqmeta.get('output_resolution'):
        result['requested_output_resolution']=reqmeta['output_resolution']
        result['provider_resolution']=reqmeta.get('resolution')
        result['local_downscale_applied']=bool(reqmeta.get('local_export_resolution'))
    result['local_path']=str(final)
    result['local_url']='/outputs/'+urllib.parse.quote(final.name)
    result['audio_status']='ready'
    result.pop('audio_error',None)
    return result


def task_to_public(t):
    if not t:return None
    d=dict(t)
    for src,dst in (('request_json','request'),('response_json','response'),('result_json','result')):
        value=db.loads(d.get(src),{})
        d[dst]=value if isinstance(value,dict) else {}
    d['actual_model']=(d.get('request') or {}).get('_request_model') or (d.get('request') or {}).get('model')
    req=d.get('request') or {}
    check=(d.get('result') or {}).get('quality_check')
    if isinstance(check,dict):
        items=[x for x in check.get('issues',[]) if isinstance(x,dict)]
        d['quality_report']={'pass':check.get('technical_status')=='passed',
            'issues':[x.get('message','') for x in items if x.get('level')=='error'],
            'warnings':[x.get('message','') for x in items if x.get('level')!='error']+['自动检查仅覆盖基础技术指标；商品保真和广告可用性仍须人工评审。']+
                (['基础质检未完成；不能视为效果验收通过'] if check.get('technical_status') in (None,'incomplete') else []),
            'created_at':d.get('updated_at'),'technical_status':check.get('technical_status','incomplete')}
    if req.get('mode') == 'hypit':
        d['workspace'] = 'hypit'
        try:phase = hypit_service.get(req.get('project_id'))['status']
        except (ValueError,KeyError,TypeError):phase='工程记录缺失，原任务保留待核对'
        d['progress'] = {'current_label': {'authoring':'Agent 编写工程','ready':'工程校验通过',
            'building':'官方 Runtime 构建','exporting':'导出成片','completed':'已完成',
            'blocked':'需要处理','interrupted':'中断待核对'}.get(phase,phase),
            'stage_percent': 100 if t['status']=='completed' else 0,
            'stage_done': 1 if t['status']=='completed' else 0, 'stage_total':1, 'stages':[]}
        return d
    d['workspace']='replica' if (req.get('mode')=='replica' or req.get('workflow_version_id')) else 'video'
    d['progress']=_task_progress(d,req,d.get('response') or {},d.get('result') or {})
    return d


def refresh_video_task(t, force=False):
    """Refresh one paid video task from OpenLux/Seedance.

    The database is the source of truth. Polling is throttled so a browser refresh,
    a foreground API request and the background watcher cannot create a burst of
    duplicate status queries.
    """
    if not t or t.get('kind')!='video': return t
    if db.loads(t.get('request_json'),{}).get('mode') == 'hypit': return t
    if t.get('status') not in ('pending','running','result_pending','submission_unknown'): return t
    pid=t.get('provider_task_id')
    if not pid:return t
    with _task_lock(t['id']):
        latest=db.one('SELECT * FROM tasks WHERE id=?',(t['id'],)) or t
        if latest.get('status') not in ('pending','running','result_pending','submission_unknown'): return latest
        if not latest.get('provider_task_id'): return latest
        last=float(latest.get('last_polled_at') or 0)
        min_gap=max(2.0,float(config.POLL_INTERVAL or 6)*0.75)
        if not force and last and (db.now()-last)<min_gap:
            return latest
        qcount=int(latest.get('query_count') or 0)+1; polled_at=db.now()
        try:
            reqmeta=db.loads(latest.get('request_json'),{}) or {}
            if reqmeta.get('_provider_adapter'):
                resp=video_adapters.query(latest['provider_task_id'],reqmeta)
                openlux.validate_task_identity(resp,latest['provider_task_id'])
                st=video_adapters.status(resp)
            else:
                resp=openlux.video_query(latest['provider_task_id'], reqmeta.get('_query_path'))
                openlux.validate_task_identity(resp,latest['provider_task_id'])
                st=openlux.extract_status(resp)
            fields={'response_json':resp,'status':st,'query_count':qcount,'last_polled_at':polled_at,'last_query_error':None}
            if st=='completed':
                url=video_adapters.result_url(resp) if reqmeta.get('_provider_adapter') else openlux.extract_result_url(resp)
                previous=db.loads(latest.get('result_json'),{}) or {}
                result=dict(previous)
                if url:
                    result['remote_url']=url
                    try:
                        dst=config.OUTPUT_DIR/f'video_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4'; openlux.download(url,dst)
                        result['generated_raw_path']=str(dst);result.pop('download_error',None)
                        try:
                            _finish_audio_for_result(result,reqmeta)
                        except Exception as audio_exc:
                            result['audio_status']='failed';result['audio_error']=str(audio_exc)[:900]
                            result['unprocessed_local_url']='/outputs/'+urllib.parse.quote(dst.name)
                            result.pop('local_url',None);result.pop('local_path',None)
                        try: result['quality_check']=production_system.quality_check(Path(result.get('local_path') or dst),expected=reqmeta)
                        except Exception as qe: result['quality_check']={'ok':False,'issues':[{'level':'info','message':'自动质检未完成：'+str(qe)[:180]}]}
                    except Exception as e:
                        result['download_error']=str(e)
                    fields['status']='completed'
                else:
                    # Some providers report completed before the result URL becomes visible.
                    # Keep polling without resubmitting the paid create request.
                    fields['status']='result_pending'
                fields['result_json']=result
            elif st=='failed':
                fields['error']=json.dumps(resp,ensure_ascii=False)[:3000]
            db.task_update(latest['id'],**fields)
            if fields.get('status') in ('completed','failed'):
                # 任务完成 / 失败后下一次余额查询必须重新读取 /api/user/self。
                # 这样可以同步扣费或退款后的真实账户余额。
                cost_system.clear_balance_cache()
            return db.one('SELECT * FROM tasks WHERE id=?',(latest['id'],))
        except Exception as e:
            db.task_update(latest['id'],query_count=qcount,last_polled_at=polled_at,last_query_error=str(e)[:1500])
            log.warning('query task failed %s: %s',latest['id'],e)
            return db.one('SELECT * FROM tasks WHERE id=?',(latest['id'],)) or latest


def retry_task_download(task_id:str):
    with _task_lock(task_id):return _retry_task_download_locked(task_id)


def _retry_task_download_locked(task_id:str):
    row=db.one('SELECT * FROM tasks WHERE id=?',(task_id,))
    if not row or row.get('kind')!='video':
        raise ValueError('视频任务不存在')
    result=db.loads(row.get('result_json'),{}) or {}
    url=result.get('remote_url') or openlux.extract_result_url(db.loads(row.get('response_json'),{}) or {})
    if not url:
        raise ValueError('当前任务还没有可重新下载的远程结果地址')
    dst=config.OUTPUT_DIR/f'video_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4'
    try:
        openlux.download(url,dst)
    except Exception as e:
        result['remote_url']=url; result['download_error']=str(e)
        db.task_update(task_id,result_json=result)
        raise RuntimeError(f'重新下载失败：{e}')
    result['remote_url']=url; result['generated_raw_path']=str(dst); result.pop('download_error',None)
    reqmeta=db.loads(row.get('request_json'),{}) or {}
    try:_finish_audio_for_result(result,reqmeta)
    except Exception as audio_exc:
        result['audio_status']='failed';result['audio_error']=str(audio_exc)[:900]
        result['unprocessed_local_url']='/outputs/'+urllib.parse.quote(dst.name)
        result.pop('local_url',None);result.pop('local_path',None)
    try: result['quality_check']=production_system.quality_check(Path(result.get('local_path') or dst),expected=reqmeta)
    except Exception as qe: result['quality_check']={'ok':False,'issues':[{'level':'info','message':'自动质检未完成：'+str(qe)[:180]}]}
    db.task_update(task_id,status='completed',result_json=result,last_query_error=None)
    return db.one('SELECT * FROM tasks WHERE id=?',(task_id,))

def retry_audio_output(task_id):
    with _task_lock(task_id):return _retry_audio_output_locked(task_id)


def _retry_audio_output_locked(task_id):
    row=db.one('SELECT * FROM tasks WHERE id=?',(task_id,))
    if not row or row.get('kind')!='video':raise ValueError('未找到视频任务')
    result=db.loads(row.get('result_json'),{}) or {}
    if row['status']!='completed' or not result.get('generated_raw_path'):
        raise ValueError('尚无已下载的原始生成视频，不能进行本地音轨重试')
    try:_finish_audio_for_result(result,db.loads(row.get('request_json'),{}) or {})
    except Exception as e:
        result['audio_status']='failed';result['audio_error']=str(e)[:900]
        db.task_update(task_id,result_json=result)
        raise RuntimeError('本地音轨处理失败，原视频已保留：'+str(e))
    try:result['quality_check']=production_system.quality_check(Path(result['local_path']),expected=db.loads(row.get('request_json'),{}) or {})
    except Exception as exc:result['quality_check']={'ok':False,'technical_status':'incomplete','issues':[{'level':'info','message':'音轨处理后质检未完成：'+str(exc)[:180]}]}
    db.task_update(task_id,result_json=result)
    return db.one('SELECT * FROM tasks WHERE id=?',(task_id,))


def finalize_image_task(task_id):
    """Save a retained synchronous image response; NEVER call a model here."""
    with _task_lock(task_id):
        row=db.one('SELECT * FROM tasks WHERE id=?',(task_id,))
        if not row or row['kind']!='image':raise ValueError('图片任务不存在')
        if row['status']=='completed':return row
        if row['status']!='result_pending':raise ValueError('只有已保留接口响应的图片任务可以仅重新保存')
        req=db.loads(row['request_json'],{}) or {};response=db.loads(row['response_json'],{}) or {}
        if not isinstance(req,dict) or not isinstance(response,dict) or not response:raise ValueError('原始图片响应不完整；请先核对原任务，不能重新生成')
        asset,out_path=save_generated_image(response)
        parent=req.get('parent')
        previous=db.one('SELECT * FROM generation_versions WHERE version_id=?',(parent,)) if parent else None
        if parent and not previous:raise ValueError('原图片版本缺失；已保留图片，请先核对历史记录')
        group=previous['group_id'] if previous else db.new_id('imggrp');version=db.new_id('imgver')
        source=asset_row(req.get('source_asset_id')) if req.get('source_asset_id') else None
        if previous and previous.get('asset_id'):source=asset_row(previous['asset_id'])
        metadata={'output_path':out_path,'ratio':req.get('ratio'),'request_size':req.get('size'),'quality':req.get('quality'),
            'inherited_ratio':bool(req.get('_inherited_ratio')),'actual_width':asset.get('width'),'actual_height':asset.get('height'),
            'mode':req.get('mode'),'source_asset_id':source.get('id') if source else None}
        result={'version_id':version,'group_id':group,'asset':asset_public(asset),'ratio':req.get('ratio'),'request_size':req.get('size'),
            'quality':req.get('quality'),'inherited_ratio':bool(req.get('_inherited_ratio')),'mode':req.get('mode'),
            'source_asset':asset_public(source) if source else None}
        with db.tx() as c:
            c.execute('INSERT INTO generation_versions(version_id,group_id,parent_version_id,kind,prompt,level,asset_id,metadata_json,confirmed,created_at) VALUES(?,?,?,?,?,?,?,?,0,?)',
                      (version,group,parent,'image',req.get('prompt'),req.get('level'),asset['id'],db.dumps(metadata),db.now()))
            c.execute("UPDATE tasks SET status='completed',result_json=?,error=NULL,updated_at=? WHERE id=?",(db.dumps(result),db.now(),task_id))
        try:task_observability.record('image',task_id,'原始图片响应已保存到本地','completed','没有调用新的生成接口')
        except Exception:log.warning('Image saved; observation write deferred for %s',task_id)
        return db.one('SELECT * FROM tasks WHERE id=?',(task_id,))


def recovery_tasks():
    rows=db.video_task_snapshot(150)
    out=[]
    for row in rows:
        result=db.loads(row.get('result_json'),{}) or {}
        if row.get('status') in ('pending','running','result_pending','submission_unknown') or result.get('download_error'):
            out.append(task_to_public(row))
    return out

def _video_task_mode(row):
    req=db.loads((row or {}).get('request_json'),{}) or {}
    mode=req.get('mode')
    if mode in ('direct','replica','hypit'): return mode
    return 'replica' if req.get('workflow_version_id') else 'direct'


def resumable_video_tasks(refresh=True):
    """Return persisted task slots for page/bootstrap restoration.

    Active tasks are refreshed first. A slot also keeps the most recent terminal
    task, so a task that completed while the browser was closed still reappears
    with its result after the user opens/refreshes the page.
    """
    rows=db.video_task_snapshot(150)
    refreshed=[]
    for r in rows:
        if refresh and r.get('status') in ('pending','running','result_pending','submission_unknown'):
            r=refresh_video_task(r)
        refreshed.append(r)
    public=[task_to_public(r) for r in refreshed]
    active=[x for x in public if x and (x.get('status') in ('pending','running','result_pending') or (x.get('status')=='submission_unknown' and (x.get('provider_task_id') or x.get('workspace')=='hypit')))]
    slots={'direct':None,'replica':None}
    # Prefer an active task for each workspace; otherwise restore the latest result.
    for mode in ('direct','replica'):
        candidates=[x for x in active if _video_task_mode(x)==mode]
        if not candidates:
            candidates=[x for x in public if x and _video_task_mode(x)==mode]
        if candidates: slots[mode]=candidates[0]
    return {'active':active,'slots':slots,'active_count':len(active)}


def active_video_count(refresh=True):
    rows=db.all_("SELECT * FROM tasks WHERE "+db.ACTIVE_VIDEO_WHERE+" ORDER BY created_at ASC")
    if refresh:
        for r in rows[:10]: refresh_video_task(r)
    row=db.one("SELECT COUNT(*) n FROM tasks WHERE "+db.ACTIVE_VIDEO_WHERE)
    return int(row['n'] if row else 0)


def global_task_center():
    """Read-only global task center snapshot.

    This endpoint never creates or retries paid work and does not force provider
    polling. The background watcher owns provider polling, so opening the drawer
    or refreshing the UI cannot produce extra platform traffic.
    """
    rows=db.video_task_snapshot(80)
    public=[task_to_public(x) for x in rows]
    active_states=('pending','running','result_pending','submission_unknown')
    active=[x for x in public if x and (x.get('status') in ('pending','running','result_pending') or (x.get('status')=='submission_unknown' and (x.get('provider_task_id') or x.get('workspace')=='hypit')))]
    recent=[x for x in public if x and (x.get('status') not in active_states or (x.get('status')=='submission_unknown' and not x.get('provider_task_id') and x.get('workspace')!='hypit'))][:8]
    direct=sum(1 for x in active if x.get('workspace')=='video')
    replica=sum(1 for x in active if x.get('workspace')=='replica')
    hypit=sum(1 for x in active if x.get('workspace')=='hypit')
    limit=int(settings.effective_limits().get('max_concurrent_video_tasks',config.MAX_CONCURRENT_VIDEO_TASKS))
    return {'active':active,'recent':recent,'active_count':len(active),'limit':limit,
            'direct_active':direct,'replica_active':replica,'hypit_active':hypit,'free_slots':max(0,limit-len(active))}


def _background_task_poller(stop_event):
    """Continue polling persisted video tasks even when no browser tab is open."""
    # Give the HTTP server a brief moment to finish starting, then recover tasks.
    if stop_event.wait(1.0): return
    while not stop_event.is_set():
        try:
            rows=db.pollable_video_tasks(20)
            for row in rows:
                if stop_event.is_set(): break
                # submission_unknown without a provider id cannot be queried safely.
                if row.get('provider_task_id'):
                    refresh_video_task(row)
        except Exception as e:
            log.warning('background video task poller failed: %s',e)
        stop_event.wait(max(2,int(config.POLL_INTERVAL or 6)))

def specs_from_payload(payload):
    specs=[]
    for item in payload.get('media') or []:
        a=asset_row(item.get('asset_id'))
        if a: specs.append({'asset':a,'role':item.get('role')})
    return specs


def validate_replica_paid_payload(payload, specs):
    # Result-oriented replica uses Seedance video editing semantics:
    # preserve the source timeline while replacing scene / vehicle / product / person.
    # Validate locally BEFORE OSS upload and BEFORE the paid create POST.
    if payload.get('mode')!='replica':
        return
    if payload.get('video_mode')!='edit':
        raise ValueError('爆款复刻本地自检失败：内部模式必须是视频编辑；本次未调用付费接口。')
    if int(payload.get('duration')) != -1:
        raise ValueError('爆款复刻本地自检失败：视频编辑 duration 必须为 -1（继承原视频）；本次未调用付费接口。')
    if payload.get('ratio') != 'adaptive':
        raise ValueError('爆款复刻本地自检失败：视频编辑 ratio 必须为 adaptive（继承原视频画幅）；本次未调用付费接口。')
    videos=[s for s in specs if (s.get('asset') or {}).get('kind')=='video']
    if not videos:
        raise ValueError('爆款复刻本地自检失败：缺少待编辑参考视频；本次未调用付费接口。')
    if float(payload.get('billing_duration') or 0) <= 0:
        raise ValueError('爆款复刻本地自检失败：未读取到原视频有效时长；本次未调用付费接口。')


class Handler(BaseHTTPRequestHandler):
    timeout = 30  # Bound stalled request reads and disconnected client writes.
    server_version=f'SeedanceStudio/{config.APP_VERSION}'

    def log_message(self, fmt, *args): log.info('%s %s',self.address_string(),fmt%args)

    def _send(self,status=200,body=b'',ctype='application/json; charset=utf-8',headers=None):
        if isinstance(body,(dict,list)): body=json_bytes(body)
        elif isinstance(body,str): body=body.encode('utf-8')
        if getattr(self,'_operation_id',None) and not getattr(self,'_operation_finished',False):
            team_safety.finish(self._operation_id,status,body);self._operation_finished=True
        self.send_response(status); self.send_header('Content-Type',ctype); self.send_header('Content-Length',str(len(body)))
        self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
        if headers:
            for k,v in headers.items(): self.send_header(k,v)
        self.end_headers(); self.wfile.write(body)

    def ok(self,data=None,**extra):
        out={'ok':True,'data':data}; out.update(extra); self._send(200,out)

    def err(self,msg,status=400,**extra):
        out={'ok':False,'error':str(msg)}; out.update(extra); self._send(status,out)

    def json_in(self):
        if hasattr(self,'_cached_json'):return self._cached_json
        n=int(self.headers.get('Content-Length','0') or 0)
        if n<0: raise ValueError('请求体长度无效')
        if n>config.MAX_UPLOAD_MB*1024*1024: raise ValueError('请求体过大')
        raw=self.rfile.read(n) if n else b'{}'
        if n and len(raw)!=n:raise ValueError('请求体不完整，未进入业务执行')
        value=json.loads(raw.decode('utf-8') or '{}')
        if not isinstance(value,dict):raise ValueError('请求参数必须是 JSON 对象')
        if not urllib.parse.urlparse(self.path).path.startswith('/api/uploads/'):
            upload_lifecycle.validate_request(value)
        return value

    def _hypit_guard(self, write=False):
        # Protect spend authorization from cross-site forms and DNS rebinding.
        port = self.server.server_address[1]
        hosts = {f'127.0.0.1:{port}', f'localhost:{port}', f'[::1]:{port}'}
        if self.client_address[0] not in {'127.0.0.1','::1'} or self.headers.get('Host') not in hosts:
            raise PermissionError('Hypit management is local-only')
        origin = self.headers.get('Origin')
        if origin and origin not in {'http://' + host for host in hosts}:
            raise PermissionError('Cross-origin Hypit request refused')
        if write and (self.headers.get('Content-Type','').split(';')[0] != 'application/json'
            or not secrets.compare_digest(self.headers.get('X-Hypit-Session',''), _hypit_session_token)):
            raise PermissionError('Hypit session authorization missing')

    def _workspace_guard(self):
        token=getattr(config,'WORKSPACE_TOKEN','')
        if token and not secrets.compare_digest(self.headers.get('X-Studio-Access',''),token):
            raise PermissionError('该空间只能通过已授权的远程入口访问')

    def do_GET(self):
        try:
            if urllib.parse.urlparse(self.path).path!='/api/bootstrap-health':self._workspace_guard()
            self._get()
        except BrokenPipeError:pass
        except PermissionError as e:self.err(e,403)
        except ValueError as e:self.err(e,400)
        except Exception as e: log.exception('GET failed'); self.err(e,500)

    def _get(self):
        u=urllib.parse.urlparse(self.path); path=u.path; qs=urllib.parse.parse_qs(u.query)
        if path=='/workspace-context.js':
            ctx={'remote':False,'workspace':config.WORKSPACE_ID,'admin':config.WORKSPACE_ID=='owner'}
            return self._send(200,'window.STUDIO_CONTEXT='+json.dumps(ctx)+';', 'application/javascript; charset=utf-8')
        if path=='/api/storage/status':return self.ok(storage_manager.status())
        if path=='/api/storage/files':return self.ok(storage_manager.files((qs.get('category') or ['outputs'])[0],(qs.get('page') or ['0'])[0]))
        if path.startswith('/api/hypit/'):
            self._hypit_guard()
            if path=='/api/hypit/session':return self.ok({'token':_hypit_session_token})
            if path=='/api/hypit/health':return self.ok(hypit_service.health())
            if path=='/api/hypit/projects':return self.ok(hypit_service.list())
            if path=='/api/hypit/project':return self.ok(hypit_service.get((qs.get('id') or [''])[0]))
            if path=='/api/hypit/source':
                tools=hypit_service.tools((qs.get('id') or [''])[0])
                return self.ok(tools.call('read_file',{'path':(qs.get('file') or ['main.svml'])[0]}))
            return self.err('Not found',404)
        # Fast and side-effect-free readiness probe: no DB, provider, network or background jobs.
        if path=='/api/bootstrap-health':
            return self.ok({'version':config.APP_VERSION,'build':config.APP_BUILD,'root_dir':str(config.ROOT),'port':config.PORT,'pid':os.getpid(),'workspace':config.WORKSPACE_ID,'data_dir':str(config.DATA_DIR)})
        if path=='/api/gpt-v41/models':return self.ok(gpt_pro_v41.models(force=(qs.get('force') or ['0'])[0]=='1'))
        if path=='/api/assistant/conversations':return self.ok(ai_assistant.conversations())
        if path=='/api/image/recovery':
            return self.ok(db.all_("SELECT id,status,created_at FROM tasks WHERE kind='image' AND status='result_pending' ORDER BY created_at DESC LIMIT 10"))
        if path=='/api/assistant/messages':return self.ok(ai_assistant.messages((qs.get('conversation_id') or [''])[0],(qs.get('before_id') or [None])[0]))
        if path=='/api/assistant/attachment':
            self._hypit_guard()
            return self.ok(ai_assistant.attachment((qs.get('message_id') or [''])[0],(qs.get('index') or [''])[0]))
        if path=='/api/assistant/call-state':return self.ok(ai_assistant.call_state((qs.get('conversation_id') or [''])[0]))
        if path=='/api/assistant/diagnostics':return self.ok(ai_assistant.diagnostics((qs.get('model') or ['gpt-6.1-sol'])[0]))
        if path=='/api/gpt-v4/memory':return self.ok(gpt_memory_v4.list_memories())
        if path=='/api/bright-tiktok/settings':return self.ok(bright_tiktok.cfg())
        if path=='/api/bright-tiktok/jobs':return self.ok(bright_tiktok.jobs())
        if path=='/api/bright-tiktok/balance':return self.ok(bright_tiktok.balance())
        if path=='/api/hot-radar/video/downloader/status':return self.ok(viral_review.downloader_status())
        if path=='/api/hot-radar/vision-models':return self.ok(bright_e2e_models.catalog())
        if path=='/api/task-health/overview':return self.ok(task_observability.overview())
        if path=='/api/task-health/diagnostics':return self.ok(task_observability.diagnostics())
        if path=='/api/product-memory/thumbnail':
            image=product_memory.thumbnail_bytes((qs.get('product_id') or [''])[0])
            return self._send(200,image[0],image[1]) if image else self.err('没有已归档的商品图片',404)
        if path=='/api/product-memory/catalog':return self.ok(product_memory.catalog())
        if path=='/api/product-memory/state':return self.ok(product_memory.state((qs.get('product_id') or [None])[0]))
        if path=='/api/product-memory/analyze/status':return self.ok(product_memory.analysis_status((qs.get('task_id') or [None])[0]))
        if path=='/api/model-pricing':return self.ok(model_pricing.get((qs.get('model') or [''])[0],force=(qs.get('refresh') or ['0'])[0]=='1'))
        if path=='/api/product-memory/models':return self.ok(product_memory.model_status((qs.get('model') or ['gpt-6.1-sol'])[0]))
        if path=='/api/video-model-tests/plan':return self.ok(video_model_tests.plan())
        if path=='/api/video-evaluation/overview':
            self._hypit_guard()
            return self.ok(video_evaluation.overview())
        if path=='/api/video/resolution-options':return self.ok({'models':video_adapters.resolution_menus()})
        if path=='/api/video/smart-model-catalog':return self.ok({'models':intelligent_router.catalog(),'routing_modes':intelligent_router.ROUTE_MODES,'task_types':intelligent_router.TASK_TYPES})
        if path=='/api/hot-radar/state':return self.ok(hot_radar.state((qs.get('product_id') or [None])[0]))
        if path=='/api/hot-radar/window':return self.ok(hot_radar.scan_window_state((qs.get('product_id') or [None])[0]))
        if path=='/api/creative-production/state':return self.ok(creative_production.all_jobs((qs.get('product_id') or [None])[0]))
        if path=='/api/creative-production/models':return self.ok(creative_production.model_choices())
        if path=='/api/hot-radar/scripts/preview':return self.err('请使用 POST',405)
        # VISUAL_PROMPT_LIBRARY_GET_V1
        if path=='/api/prompt-library':return self.ok(prompt_library.list_prompts())
        if path=='/api/prompt-library/export':
            fmt=(qs.get('format') or ['json'])[0]
            try:
                b,ctype,name=prompt_library.export_bytes(fmt)
                return self._send(200,b,ctype,headers={'Content-Disposition': 'attachment; filename="prompt_library.'+fmt+'"'})
            except ValueError as e:return self.err(str(e),400)
        if path in ('/','/index.html'): return self._file(config.STATIC_DIR/'index.html','text/html; charset=utf-8')
        if path.startswith('/static/'):
            rel=path[len('/static/'):]; p=(config.STATIC_DIR/rel).resolve()
            if config.STATIC_DIR.resolve() not in p.parents:return self.err('非法路径',403)
            return self._file(p)
        if path.startswith('/public/assets/'):
            name=urllib.parse.unquote(path[len('/public/assets/'):]); p=(config.ASSET_DIR/name).resolve()
            if config.ASSET_DIR.resolve() not in p.parents:return self.err('非法路径',403)
            return self._file(p)
        if path.startswith('/outputs/'):
            name=urllib.parse.unquote(path[len('/outputs/'):]); p=(config.OUTPUT_DIR/name).resolve()
            if config.OUTPUT_DIR.resolve() not in p.parents:return self.err('非法路径',403)
            return self._stream_output(p)
        if path.startswith('/cache/'):
            rel=urllib.parse.unquote(path[len('/cache/'):]); p=(config.CACHE_DIR/rel).resolve()
            if config.CACHE_DIR.resolve() not in p.parents:return self.err('非法路径',403)
            return self._file(p)
        if path=='/api/status':
            counts={}
            for table in ('assets','tasks','generation_versions','workflows','usage_events'):
                r=db.one(f'SELECT COUNT(*) n FROM {table}');counts[table]=r['n'] if r else 0
            cfg=settings.get_all(); model=settings.effective_video_model(cfg); route=settings.route_info(cfg); create_path,query_path=settings.effective_video_paths(cfg); limits=settings.effective_limits(cfg)
            return self.ok({'version':config.APP_VERSION,'build':config.APP_BUILD,'edition':config.APP_EDITION,'image_model':config.IMAGE_MODEL,'video_model':model,
                            'video_base_model':route['base_model'],'video_route_control':route['control'],'video_route_control_label':route['control_label'],
                            'video_route_preference':route['preference'],'video_route_preference_label':route['preference_label'],
                            'video_route_provider_sort':route.get('provider_sort'),'video_endpoint_strategy':cfg.get('video_endpoint_strategy'),
                            'video_create_path':create_path,'video_query_path':query_path,
                            'resolution':cfg.get('default_video_resolution',config.DEFAULT_RESOLUTION),'api_key_configured':bool(config.OPENLUX_API_KEY),'tunnel':tunnel.status(),'public_assets':public_assets.status(),'oss':oss.status(),
                            'counts':counts,'dedupe_seconds':limits['dedupe_seconds'],'video_modes':modes.MODE_LABELS,
                            'max_concurrent_video_tasks':limits['max_concurrent_video_tasks'],'active_video_tasks':active_video_count(False),
                            'ffmpeg_available':bool(media.ffmpeg_path()),'media_shape':config.VIDEO_MEDIA_SHAPE,'settings':cfg,'hypit_engine':'native-v1',
                            'root_dir':str(config.ROOT),'port':config.PORT})
        if path=='/api/audio/subjects':return self.ok([{**x,'asset':asset_public(asset_row(x['asset_id']))} for x in audio_subjects.list_all() if not upload_lifecycle.removed('asset',x['asset_id'])])
        if path=='/api/assets':
            rows=db.all_('SELECT * FROM assets ORDER BY created_at DESC LIMIT 300'); return self.ok([asset_public(x) for x in upload_lifecycle.filter_rows(rows)])
        if path=='/api/history/overview':
            return self.ok(history_center_v423.overview((qs.get('limit') or ['140'])[0]))
        if path=='/api/history':
            tasks=[task_to_public(x) for x in db.all_('SELECT * FROM tasks ORDER BY created_at DESC LIMIT 150')]
            versions=db.all_('SELECT * FROM generation_versions ORDER BY created_at DESC LIMIT 150')
            for v in versions:v['metadata']=db.loads(v.get('metadata_json'),{})
            workflows=db.all_('SELECT * FROM workflows ORDER BY created_at DESC LIMIT 150')
            return self.ok({'tasks':tasks,'versions':versions,'workflows':workflows})
        if path=='/api/workflows':
            gid=(qs.get('group_id') or [''])[0]
            if not gid:return self.ok([])
            rows=db.all_('SELECT * FROM workflows WHERE group_id=? ORDER BY created_at DESC',(gid,))
            for r in rows:
                wf=db.loads(r.get('workflow_json'),{})
                if wf and not wf.get('execution_blueprint'):
                    pids=db.loads(r.get('product_asset_ids_json'),[]) or []
                    products=[asset_row(x) for x in pids]; products=[x for x in products if x]
                    wf=workflow.refresh_execution_blueprint(wf,products,r.get('instruction') or '')
                r['workflow']=wf
            return self.ok(rows)
        if path=='/api/tasks/resume':
            return self.ok(resumable_video_tasks(False))
        if path=='/api/tasks/recovery':
            return self.ok({'rows':recovery_tasks()})
        if path=='/api/tasks/global':
            return self.ok(global_task_center())
        if path.startswith('/api/tasks/'):
            tid=path.rsplit('/',1)[-1]; t=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
            if not t:return self.err('任务不存在',404)
            t=refresh_video_task(t); return self.ok(task_to_public(t))
        if path=='/api/tunnel/status': return self.ok(tunnel.status())
        if path=='/api/public-assets/status': return self.ok(public_assets.status())
        if path=='/api/oss/status': return self.ok(oss.status())
        if path=='/api/oss/secrets': return self.ok(secrets_store.status())
        if path=='/api/credentials/status': return self.ok(secrets_store.status())
        if path=='/api/system/health': return self.ok(system_health.report())
        if path=='/api/system/repair': return self.ok(system_health.repair_status())
        if path=='/api/system/doctor': return self.ok(production_system.doctor())
        if path=='/api/workspace': return self.ok(production_system.load_workspace())
        if path=='/api/workspace/drafts': return self.ok(workspace_store.get_all())
        if path in ('/api/library','/api/asset-library'):
            q=(qs.get('q') or [''])[0]; kind=(qs.get('kind') or [''])[0]
            return self.ok([asset_public(x) for x in upload_lifecycle.filter_rows(production_system.list_assets(q,kind))])
        if path=='/api/settings': return self.ok(settings.get_all())
        if path=='/api/models':
            route=settings.route_info()
            return self.ok({'base_model':route['base_model'],'effective':route['request_model'],
                            'route_control':route['control'],'route_preference':route['preference'],
                            'provider':route.get('provider'),
                            'control_methods':[{'id':k,**v} for k,v in settings.ROUTE_CONTROL_METHODS.items()],
                            'preferences':[{'id':k,**v} for k,v in settings.ROUTE_PREFERENCES.items()]})
        if path=='/api/cost/live':
            dur=(qs.get('duration') or [None])[0]
            res=(qs.get('resolution') or [None])[0]
            ratio=(qs.get('ratio') or ['9:16'])[0]
            input_video_seconds=(qs.get('input_video_seconds') or ['0'])[0]
            force=(qs.get('force') or ['0'])[0] in ('1','true','yes')
            return self.ok(cost_system.live(dur,res,ratio,input_video_seconds,force,force))
        if path=='/api/openlux/balance':
            # Read-only wallet lookup; no model pricing or generation requests.
            balance=cost_system.fetch_balance(force=(qs.get('refresh') or ['0'])[0]=='1')
            return self.ok({key:balance.get(key) for key in
                            ('ok','available','stale','needs_account_binding')})
        if path=='/api/openlux/account-status':
            return self.ok(secrets_store.status())
        if path=='/api/cost/summary':
            total=db.one('SELECT COUNT(*) n, SUM(estimated_cost_usd) est, SUM(actual_cost_usd) actual, COUNT(actual_cost_usd) actual_known FROM usage_events')
            by=db.all_('SELECT kind,model,COUNT(*) calls,SUM(estimated_cost_usd) est FROM usage_events GROUP BY kind,model ORDER BY calls DESC')
            return self.ok({'total':total,'by_kind':by,'pricing_configured':{'image':config.IMAGE_ESTIMATED_COST_USD>0,'video':config.VIDEO_ESTIMATED_COST_PER_SECOND_USD>0}})
        if path=='/api/activity':
            rows=db.all_("SELECT id,kind,status,provider_task_id,created_at,updated_at,error FROM tasks ORDER BY updated_at DESC LIMIT 30")
            return self.ok({'model':settings.effective_video_model(),'rows':rows})
        return self.err('Not found',404)

    def _stream_output(self,p:Path):
        """Memory-bounded file serving with Range for Chrome/Edge MP4 playback."""
        if not p.is_file():return self.err('本地输出文件不存在，请尝试重新下载',404)
        length=p.stat().st_size
        ctype=mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        range_header=self.headers.get('Range','').strip()
        first=0;last=max(length-1,0);partial=False
        if range_header:
            match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
            if not match or not length or (not match.group(1) and not match.group(2)):
                return self._range_invalid(length)
            raw_start,raw_end=match.groups()
            if raw_start:
                first=int(raw_start)
                last=min(int(raw_end),length-1) if raw_end else length-1
            else:
                take=int(raw_end)
                first=max(0,length-take)
                last=length-1
            if first>=length or first>last or (not raw_start and not int(raw_end)):
                return self._range_invalid(length)
            partial=True
        size=(last-first+1) if length else 0
        self.send_response(206 if partial else 200)
        self.send_header('Content-Type',ctype)
        self.send_header('Content-Length',str(size))
        self.send_header('Accept-Ranges','bytes')
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        if partial:self.send_header('Content-Range',f'bytes {first}-{last}/{length}')
        self.end_headers()
        with p.open('rb') as f:
            f.seek(first)
            remaining=size
            while remaining>0:
                block=f.read(min(256*1024,remaining))
                if not block:break
                self.wfile.write(block)
                remaining-=len(block)

    def _range_invalid(self,length):
        self.send_response(416)
        self.send_header('Content-Range',f'bytes */{length}')
        self.send_header('Content-Length','0')
        self.send_header('Accept-Ranges','bytes')
        self.end_headers()

    def _file(self,p:Path,ctype=None):
        if not p.exists() or not p.is_file():return self.err('文件不存在',404)
        if p.stat().st_size>1024*1024:return self._stream_output(p)
        data=p.read_bytes(); ctype=ctype or mimetypes.guess_type(p.name)[0] or 'application/octet-stream'; return self._send(200,data,ctype)

    def do_POST(self):
        self._operation_id=None;self._operation_finished=False;upload_slot=False
        try:
            self._workspace_guard()
            self._hypit_guard()
            route=urllib.parse.urlparse(self.path).path
            if route in team_safety.CHARGED:
                self._hypit_guard()
                self._cached_json=self.json_in()
                storage_manager.require_capacity(512*1024*1024)
                operation=team_safety.claim(route,self._cached_json,self.headers.get('Idempotency-Key',''))
                if operation['replay']:
                    return self._send(operation['status'],operation['body'],headers={'X-Idempotent-Replay':'true'})
                self._operation_id=operation['id']
            elif route in ('/api/upload','/api/product-memory/source/upload','/api/product-memory/image/to-assets','/api/hot-radar/video/download-post','/api/hot-radar/video/fetch','/api/tasks/retry-download','/api/tasks/retry-image'):
                upload_slot=storage_manager.upload_gate.acquire(False)
                if not upload_slot:raise team_safety.Refused('当前空间正在上传或保存文件，请完成后再上传，避免内存和磁盘突增。')
                storage_manager.require_capacity(int(self.headers.get('Content-Length','0') or 0))
            self._post()
            if self._operation_id and not self._operation_finished:
                # A streaming response has no reusable JSON body; preserve its receipt.
                team_safety.finish(self._operation_id,200,None);self._operation_finished=True
        except (BrokenPipeError,ConnectionResetError):pass
        except team_safety.Refused as e:self.err(e,409)
        except PermissionError as e:self.err(e,403)
        except openlux.ApiError as e:
            log.exception('API error');self.err(str(e),502,provider_status=e.status,provider_body=(e.body or '')[:2000],retryable=False)
        except ValueError as e:self.err(e,400)
        except Exception as e:log.exception('POST failed');self.err(e,500)
        finally:
            if self._operation_id and not self._operation_finished:team_safety.finish(self._operation_id,None,None)
            if upload_slot:storage_manager.upload_gate.release()

    def _post(self):
        path=urllib.parse.urlparse(self.path).path
        if path.startswith('/api/video-evaluation/'):
            self._hypit_guard()
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise PermissionError('JSON content type required')
            req=self.json_in()
            if not isinstance(req,dict):raise ValueError('JSON object required')
            if path=='/api/video-evaluation/review':return self.ok(video_evaluation.review(req))
            if path=='/api/video-evaluation/check':return self.ok(video_evaluation.check(req))
            return self.err('Not found',404)
        if path.startswith('/api/storage/') or path=='/api/safety/acknowledge':
            self._hypit_guard();req=self.json_in()
            if not isinstance(req,dict):raise ValueError('JSON object required')
            if path=='/api/storage/trash':return self.ok(storage_manager.trash(req.get('path'),req.get('confirmed')))
            if path=='/api/storage/restore':return self.ok(storage_manager.restore(req.get('id')))
            if path=='/api/storage/purge':return self.ok(storage_manager.purge(req.get('id'),req.get('confirmed')))
            if path=='/api/storage/clean':return self.ok(storage_manager.maintenance())
            if path=='/api/safety/acknowledge':return self.ok(team_safety.acknowledge(req.get('id'),req.get('confirmed'),req.get('note')))
            return self.err('Not found',404)
        if path in ('/api/uploads/remove','/api/uploads/restore'):
            self._hypit_guard()
            req=self.json_in()
            if not isinstance(req,dict):return self.err('JSON object required',400)
            if path.endswith('/restore'):return self.ok(upload_lifecycle.restore(req.get('kind'),req.get('id')))
            return self.ok(upload_lifecycle.remove(req.get('kind'),req.get('id'),str(req.get('product_id') or '')))
        if path.startswith(('/api/assistant/','/api/gpt-v41/','/api/gpt-v4/')):
            self._hypit_guard()
        if path.startswith('/api/hypit/'):
            self._hypit_guard(write=True)
            request=self.json_in()
            if not isinstance(request,dict):return self.err('JSON object required',400)
            pid=request.get('id')
            if path=='/api/hypit/create':return self.ok(hypit_service.create(request))
            if path=='/api/hypit/from-approved-script':return self.ok(creative_production.hypit_handoff(request,hypit_service))
            if path=='/api/hypit/quote':return self.ok(hypit_service.quote())
            if path=='/api/hypit/video-quote':return self.ok(hypit_service.video_quote(pid,request))
            if path=='/api/hypit/build-paid':return self.ok(hypit_service.build_paid(pid,request))
            if path=='/api/hypit/author':return self.ok(hypit_service.author(pid,request))
            if path=='/api/hypit/validate':return self.ok(hypit_service.validate(pid))
            if path=='/api/hypit/build':return self.ok(hypit_service.build(pid))
            if path=='/api/hypit/cancel':return self.ok(hypit_service.cancel(pid))
            if path=='/api/hypit/studio':return self.ok(hypit_service.open_studio(pid))
            return self.err('Not found',404)
        if path=='/api/gpt-v41/chat/stream':return self._gpt_v41_stream()
        if path=='/api/assistant/new':return self.ok(ai_assistant.new_conversation())
        if path=='/api/assistant/acknowledge':return self.ok(ai_assistant.acknowledge_unknown(self.json_in()))
        if path=='/api/assistant/chat':return self.ok(ai_assistant.send(self.json_in()))
        if path=='/api/gpt-v4/conversation/delete':return self.ok(gpt_memory_v4.delete_conversation(self.json_in()))
        if path=='/api/gpt-v4/memory/save':return self.ok(gpt_memory_v4.save(self.json_in()))
        if path=='/api/gpt-v4/memory/delete':return self.ok(gpt_memory_v4.delete(self.json_in()))
        if path=='/api/gpt-v4/memory/config':return self.ok(gpt_memory_v4.config(self.json_in()))
        if path=='/api/bright-tiktok/settings/save':return self.ok(bright_tiktok.save_settings(self.json_in()))
        if path=='/api/bright-tiktok/preview':
            x=self.json_in();return self.ok(bright_tiktok.preview(x.get('product_id'),x.get('quantity')))
        if path=='/api/bright-tiktok/submit':return self.ok(bright_tiktok.submit(self.json_in()))
        if path=='/api/bright-tiktok/poll':return self.ok(bright_tiktok.poll(self.json_in().get('id')))
        if path=='/api/bright-tiktok/snapshot/restore':return self.ok(bright_tiktok.restore_snapshot(self.json_in()))
        if path=='/api/bright-tiktok/snapshot/attach':return self.ok(bright_tiktok.attach_snapshot(self.json_in()))
        if path=='/api/hot-radar/video/download-post':return self.ok(viral_review.start_original_download(self.json_in(),save_asset_bytes))
        if path=='/api/hot-radar/video/download-status':return self.ok(viral_review.original_download_status(self.json_in().get('item_id')))
        if path=='/api/product-memory/new':return self.ok(hot_radar.create_draft())
        if path=='/api/product-memory/source/upload':return self.ok(product_memory.add_upload(self.json_in()))
        if path=='/api/product-memory/source/url':return self.ok(product_memory.add_url(self.json_in()))
        if path=='/api/product-memory/source/text':return self.ok(product_memory.add_text(self.json_in()))
        if path=='/api/product-memory/image/to-assets':
            record=product_memory.image_as_asset(self.json_in())
            return self.ok(save_asset_bytes(record['data'],record['name'],record['mime'],'image'))
        if path=='/api/product-memory/fact/status':return self.ok(product_memory.change_fact(self.json_in()))
        if path=='/api/product-memory/analyze/preview':return self.ok(product_memory.preview(self.json_in()))
        if path=='/api/product-memory/analyze/generate':return self.ok(product_memory.analyze(self.json_in(),async_mode=True))
        if path=='/api/product-memory/analyze/reparse':return self.ok(product_memory.reparse_saved(self.json_in().get('task_id')))
        if path=='/api/task-health/product/reconcile':return self.ok(task_observability.reconcile_product(self.json_in()))
        if path=='/api/hot-radar/video/bind':return self.ok(viral_review.bind(self.json_in()))
        if path=='/api/hot-radar/video/fetch':return self.ok(viral_review.fetch_video(self.json_in(),save_asset_bytes))
        if path=='/api/hot-radar/product/save':return self.ok(hot_radar.save_product(self.json_in()))
        if path=='/api/hot-radar/options/save':return self.ok(hot_radar.save_options(self.json_in()))
        if path=='/api/hot-radar/item/import':return self.ok(hot_radar.add_manual(self.json_in()))
        if path=='/api/hot-radar/tiktok/batch':return self.ok(hot_radar.import_tiktok_batch(self.json_in()))
        if path=='/api/hot-radar/scan':return self.err('V4.0 仅允许使用 Bright Data 手动 TikTok 采集；请在热点收集点击手动采集',400)
        if False and path=='/api/hot-radar/scan':
            req=self.json_in();return self.ok(hot_radar.scan(str(req.get('product_id','')),origin='manual'))
        if path=='/api/hot-radar/scripts/preview':return self.ok(hot_radar.script_preview(self.json_in()))
        if path=='/api/hot-radar/scripts/generate':return self.ok(hot_radar.generate_script(self.json_in()))
        if path=='/api/creative-production/new':
            req=self.json_in();return self.ok(creative_production.from_script(req.get('script_id'),req.get('model')))
        if path=='/api/creative-production/model/select':return self.ok(creative_production.select_model(self.json_in()))
        if path=='/api/creative-production/script/approve':return self.ok(creative_production.approve_script(self.json_in()))
        if path=='/api/creative-production/concept/approve':return self.ok(creative_production.approve_concept(self.json_in()))
        if path=='/api/creative-production/model/activate':return self.ok(creative_production.activate_model(self.json_in().get('id')))
        if path=='/api/creative-production/h3/handoff':return self.ok(creative_production.test_handoff(self.json_in().get('id')))
        if path=='/api/creative-production/h3/bind':return self.ok(creative_production.bind_h3_result(self.json_in()))
        if path=='/api/creative-production/hypit/bind':return self.ok(creative_production.bind_hypit_result(self.json_in(),hypit_service))
        if path=='/api/creative-production/review/reopen':return self.ok(creative_production.reopen_review(self.json_in()))
        if path=='/api/creative-production/first-cut/review':return self.ok(creative_production.review_first_cut(self.json_in()))
        # VISUAL_PROMPT_LIBRARY_POST_V1
        if path=='/api/prompt-library/save':return self.ok(prompt_library.save(self.json_in()))
        if path=='/api/prompt-library/delete':
            req=self.json_in();return self.ok(prompt_library.remove(str(req.get('code',''))))
        if path=='/api/prompt-library/favorite':
            req=self.json_in();return self.ok(prompt_library.favorite(str(req.get('code','')),req.get('value',False)))
        if path=='/api/prompt-library/import':
            req=self.json_in()
            return self.ok(prompt_library.import_records(req.get('raw',''),req.get('format','json'),req.get('on_conflict','skip'),bool(req.get('preview',True))))
        if path=='/api/upload':return self._upload()
        if path=='/api/audio/subject/create':return self._audio_subject_create()
        if path=='/api/tunnel/start':return self.ok(tunnel.start())
        if path=='/api/tunnel/stop':return self.ok(tunnel.stop())
        if path in ('/api/tunnel/selftest','/api/public-assets/selftest'):
            req=self.json_in()
            a=asset_row(req.get('asset_id')) if req.get('asset_id') else None
            if not a:
                a=db.one("SELECT * FROM assets WHERE kind='video' ORDER BY created_at DESC LIMIT 1")
            out=public_assets.self_test(a)
            out['tested_asset_id']=a.get('id') if a else None
            out['tested_asset_name']=a.get('original_name') if a else None
            return self.ok(out)
        if path=='/api/credentials/save':
            req=self.json_in()
            key=str(req.get('openlux_api_key') or '').strip()
            if key:
                secrets_store.update({'openlux_api_key':key})
                config.OPENLUX_API_KEY=key
                cost_system.clear_cache()
            return self.ok(secrets_store.status())
        if path=='/api/openlux/account-credentials':
            req=self.json_in()
            secrets_store.update({
                'openlux_account_access_token':req.get('openlux_account_access_token'),
                'openlux_account_user_id':req.get('openlux_account_user_id'),
            })
            cost_system.clear_cache()
            return self.ok({'secrets':secrets_store.status(),'cost':cost_system.live(force_balance=True,force_price=True)})
        if path=='/api/openlux/account-clear':
            out=secrets_store.clear_openlux_account()
            cost_system.clear_cache()
            return self.ok(out)
        if path=='/api/oss/config':
            req=self.json_in()
            settings_part={k:req[k] for k in ('oss_endpoint','oss_bucket','oss_object_prefix','asset_link_ttl_minutes','oss_auto_cleanup','oss_delete_after_hours','public_asset_strategy') if k in req}
            if settings_part: settings.update(settings_part)
            secrets_store.update({'oss_access_key_id':req.get('oss_access_key_id'), 'oss_access_key_secret':req.get('oss_access_key_secret')})
            return self.ok({'settings':settings.get_all(),'secrets':secrets_store.status(),'oss':oss.status()})
        if path=='/api/oss/clear-credentials':
            return self.ok(secrets_store.clear_oss())
        if path=='/api/oss/test':
            return self.ok(oss.test_connection(True))
        if path=='/api/oss/cleanup':
            return self.ok(oss.cleanup_expired(True))
        if path=='/api/system/repair':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.err('仅允许本机启动安装流程',403)
            req=self.json_in();mode=str(req.get('mode') or 'full')
            if mode not in ('core','full'):return self.err('不支持的修复模式',400)
            return self.ok(system_health.start_repair(mode))
        if path=='/api/backup/create':
            if self.client_address[0] not in ('127.0.0.1','::1'):
                return self.err('仅允许本机备份',403)
            req=self.json_in();return self.ok(backup_manager.create_backup(bool(req.get('include_outputs',False))))
        if path=='/api/workspace/draft':
            req=self.json_in()
            return self.ok(workspace_store.save(str(req.get('workspace') or 'global'),req.get('data')))
        if path=='/api/workspace/save': return self.ok(production_system.save_workspace(self.json_in()))
        if path=='/api/workspace/clear': return self.ok(production_system.clear_workspace())
        if path=='/api/system/backup': return self.ok(production_system.backup_bundle())
        if path=='/api/quality/check':
            req=self.json_in(); aid=req.get('asset_id'); a=asset_row(aid)
            if not a or a.get('kind')!='video': return self.err('请选择一个视频素材',404)
            return self.ok(production_system.quality_check(Path(a['path'])))
        if path=='/api/settings':
            out=settings.update(self.json_in()); cost_system.clear_cache(); return self.ok(out)
        if path=='/api/settings/reset': return self.ok(settings.reset_defaults())
        if path=='/api/shutdown':
            if self.client_address[0] not in ('127.0.0.1','::1'):return self.err('只允许本机停止服务',403)
            (config.ROOT/'.watchdog_stop').write_text('manual shutdown',encoding='ascii')
            self.ok(True); threading.Thread(target=self.server.shutdown,daemon=True).start(); return
        if path=='/api/open-output':
            # A tunnel-hosted page is not authorized to open local OS windows.
            host=self.headers.get('Host','').lower().split(':')[0].strip('[]')
            if host not in ('localhost','127.0.0.1','::1'):
                return self.err('仅允许本机浏览器打开输出目录',403)
            return self.ok(local_output.request_open())
        if path=='/api/tasks/refresh':
            req=self.json_in(); tid=req.get('task_id'); row=db.one('SELECT * FROM tasks WHERE id=?',(tid,))
            if not row:return self.err('任务不存在',404)
            return self.ok(task_to_public(refresh_video_task(row,True)))
        if path=='/api/tasks/retry-audio':return self.ok(task_to_public(retry_audio_output(self.json_in().get('task_id'))))
        if path=='/api/tasks/retry-download':
            req=self.json_in(); tid=req.get('task_id')
            return self.ok(task_to_public(retry_task_download(tid)))
        if path=='/api/image/generate':return self._image_generate()
        if path=='/api/tasks/retry-image':
            task=finalize_image_task(self.json_in().get('task_id'))
            public=task_to_public(task)
            return self.ok({'task':public,'version':public['result'],'paid_create_calls':0})
        if path=='/api/workflow/analyze':return self._workflow_analyze()
        if path=='/api/workflow/edit':return self._workflow_edit()
        if path=='/api/workflow/confirm':return self._workflow_confirm()
        if path=='/api/video/model-diagnostic':
            req=self.json_in()
            try:return self.ok(video_adapters.model_diagnostic(req.get('model') or ''))
            except (ValueError,openlux.ApiError) as e:return self.err(str(e),409)
        if path=='/api/video/direct-preview':return self._direct_video_preview()
        if path=='/api/video/direct-generate':return self._direct_video_generate()
        if path=='/api/video/preview':return self._video_preview()
        if path=='/api/video/generate':return self._video_generate()
        return self.err('Not found',404)

    def _gpt_v41_stream(self):
        # One paid POST, NEVER retried. SSE provides text fragments as soon as the
        # upstream supplies them. Any disconnect leaves an uncertain call history.
        payload=self.json_in()
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream; charset=utf-8')
        self.send_header('Cache-Control','no-cache, no-store, must-revalidate')
        self.send_header('X-Accel-Buffering','no')
        self.send_header('Connection','close')
        self.end_headers()
        self.close_connection=True
        def emit(kind,data):
            text='event: '+kind+'\ndata: '+json.dumps(data,ensure_ascii=False)+'\n\n'
            self.wfile.write(text.encode('utf-8'))
            self.wfile.flush()
        gpt_pro_v41.chat_events(payload,emit)

    def _upload(self):
        n=int(self.headers.get('Content-Length','0') or 0)
        if n<=0:return self.err('没有文件')
        if n>config.MAX_UPLOAD_MB*1024*1024:return self.err(f'文件超过 {config.MAX_UPLOAD_MB}MB 上限',413)
        body=self.rfile.read(n); parts=parse_multipart(body,self.headers.get('Content-Type','')); files=[p for p in parts if p['filename']]
        if not files:return self.err('没有识别到上传文件')
        out=[]
        for p in files: out.append(asset_public(save_asset_bytes(p['data'],p['filename'],p['content_type'])))
        self.ok(out)

    def _audio_subject_create(self):
        """Crop a local audio reference, store as reusable asset. No paid requests."""
        req=self.json_in();a=asset_row(req.get('asset_id'))
        if not a or a.get('kind')!='audio':return self.err('请选择已上传的音频素材',400)
        src=Path(a['path'])
        if not src.is_file():return self.err('原始音频已丢失，请重新上传',404)
        try:
            start=float(req.get('start',0));end=float(req.get('end',0))
        except (TypeError,ValueError):return self.err('裁剪起止时间不是有效数字',400)
        duration=float(a.get('duration') or 0)
        if not duration:
            a=_refresh_asset_media_metadata(a,force=True)
            duration=float(a.get('duration') or 0)
        if not duration:return self.err('无法读取音频时长，请安装 FFmpeg 后再试',409)
        if start < 0 or end <= start or end > duration+0.25:return self.err('裁剪起止时间超过音频范围',400)
        if not (0.5 <= end-start <= 30.0):return self.err('音频主体裁剪时长须为 0.5～30 秒',400)
        ff=media.ffmpeg_path()
        if not ff:return self.err('未找到 FFmpeg，无法无损安全地生成参考音频剪辑',409)
        temp=config.CACHE_DIR/f'voiceclip_{uuid.uuid4().hex}.wav'
        cmd=[str(ff),'-nostdin','-hide_banner','-loglevel','error','-y','-ss',f'{start:.3f}','-i',str(src),'-t',f'{end-start:.3f}','-vn','-ac','1','-ar','24000','-c:a','pcm_s16le',str(temp)]
        try:
            run=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=80,**media._hidden_subprocess_kwargs())
            if run.returncode!=0 or not temp.is_file() or temp.stat().st_size<1000:
                return self.err('音频裁剪失败：'+run.stderr.decode('utf-8','replace')[-380:],409)
            name=str(req.get('name','')).strip()
            if not name:return self.err('请输入音频主体名称',400)
            out=save_asset_bytes(temp.read_bytes(),safe_name(name)+'.wav','audio/wav','audio')
            subject=audio_subjects.add(out['id'],name,req.get('description',''),a['id'],start,end)
            return self.ok({**subject,'asset':asset_public(out)})
        finally:
            try:temp.unlink(missing_ok=True)
            except OSError:pass

    def _image_generate(self):
        req=self.json_in(); prompt=(req.get('prompt') or '').strip()
        if not prompt:return self.err('请输入图片提示词')
        cfg=settings.get_all(); parent=req.get('parent_version_id'); level=req.get('level') or 'normal'; ratio=req.get('ratio') or cfg.get('default_image_ratio','9:16'); quality=cfg.get('image_quality','auto')
        source_asset_id=req.get('source_asset_id')
        parent_row=None; source=None; inherited=False; generate_mode='text2image'
        if parent:
            parent_row=db.one('SELECT * FROM generation_versions WHERE version_id=?',(parent,))
            if not parent_row:return self.err('父版本不存在',404)
            if parent_row.get('asset_id'):source=asset_row(parent_row['asset_id'])
            meta=db.loads(parent_row.get('metadata_json'),{})
            if not req.get('change_ratio') and meta.get('ratio'):
                ratio=meta['ratio']; inherited=True
            generate_mode='deep'
        elif source_asset_id:
            source=asset_row(source_asset_id)
            if not source or source.get('kind')!='image':
                return self.err('图生图起始图片不存在或不是图片',404)
            generate_mode='image2image'
        if ratio not in {'auto','1:1','4:5','3:4','9:16','16:9'}:return self.err('不支持的图片比例')
        size=openlux.resolve_image_size(ratio)
        fp_obj={'kind':'image','prompt':prompt,'parent':parent,'source_asset_id':source_asset_id,'level':level,'ratio':ratio,'size':size,'quality':quality,'model':config.IMAGE_MODEL,'source_sha':source.get('sha256') if source else None,'mode':generate_mode}
        fp=fingerprint(fp_obj); running=db.task_recent_by_fp(fp,None)
        if running and running['status'] in ('pending','running','result_pending','submission_unknown'):
            return self.ok({'task':task_to_public(running),'reused':True,'reason':'同一图片任务仍在处理中，已复用原任务，未再次调用接口。'})
        dedupe=settings.effective_limits().get('dedupe_seconds',config.DEDUPE_SECONDS)
        recent=db.task_recent_by_fp(fp,dedupe)
        if recent:return self.ok({'task':task_to_public(recent),'reused':True,'reason':f'{dedupe} 秒内相同请求已拦截，未再次计费。'})
        fp_obj['_inherited_ratio']=inherited
        tid=db.task_create('image',fp,fp_obj); db.task_update(tid,status='running')
        task_observability.record('image',tid,'请求图片生成服务','running','已发送付费请求，未收到远端响应')
        resp=None
        try:
            actual_prompt=deepen_prompt(prompt,level) if source else prompt
            try:
                actual_prompt=reference_logic.image_prompt(actual_prompt,source)
            except ValueError as e:
                db.task_update(tid,status='failed',error=str(e)); return self.err(str(e),409)
            resp=openlux.image_edit(actual_prompt,Path(source['path']),source['mime'],ratio,quality,idempotency_key=fp) if source else openlux.image_generate(actual_prompt,ratio,quality,idempotency_key=fp)
            db.task_update(tid,status='result_pending',response_json=resp)
            est=config.IMAGE_ESTIMATED_COST_USD or None
            db.usage_add(tid,'image',config.IMAGE_MODEL,1,est,None,{'ratio':ratio,'size':size,'quality':quality,'deep':bool(parent),'image2image':bool(source_asset_id and not parent),'source_asset_id':source.get('id') if source else None})
            cost_system.clear_balance_cache()
            try:
                saved=finalize_image_task(tid)
                result=db.loads(saved['result_json'],{})
                return self.ok({'task':task_to_public(saved),'version':result,'cost_estimate_usd':est})
            except Exception as save_error:
                db.task_update(tid,status='result_pending',error='原图片响应已保留，本地保存失败：'+str(save_error)[:600])
                task_observability.record('image',tid,'原图片响应已保留，仅需重新保存','result_pending')
                return self.ok({'task':task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))),
                    'reason':'原图片响应已保留，请点击“仅重新保存原图片”；不要重新生成。',
                    'paid_create_calls':1,'local_save_pending':True})
        except openlux.ApiError as e:
            status='submission_unknown' if e.status is None or (e.status and e.status>=500) else 'failed'; db.task_update(tid,status=status,error=str(e),response_json={'provider_status':e.status,'provider_body':e.body})
            task_observability.record('image',tid,'图片提交结果未知' if status=='submission_unknown' else '图片接口明确失败',status,str(e)); raise
        except Exception as e:
            log.exception('image task %s failed, detailed stack trace',tid)
            fields={'status':'result_pending' if resp is not None else 'failed',
                    'error':f'图片响应后的本地处理失败，原响应已保留，禁止重新生成：{e}' if resp is not None else f'图片生成失败：{type(e).__name__}: {e}'}
            if resp is not None:fields['response_json']=resp
            db.task_update(tid,**fields)
            raise

    def _workflow_analyze(self):
        req=self.json_in(); rid=req.get('reference_asset_id'); pids=req.get('product_asset_ids') or []; instruction=(req.get('instruction') or '').strip(); ref=asset_row(rid)
        if not ref or ref['kind']!='video':return self.err('请选择参考视频')
        # Always re-probe before Hypit analysis. This repairs stale rows from earlier
        # versions and ensures a failed media read never turns into a fake 0-second workflow.
        ref=_refresh_asset_media_metadata(ref, force=True)
        if not ref.get('duration') or not ref.get('width') or not ref.get('height'):
            return self.err('参考视频基础媒体信息读取失败，已停止建立工作流。请不要进入付费生成。',409,
                            probe_backend=ref.get('_probe_backend'),probe_error=ref.get('_probe_error'),
                            asset_id=rid,path=ref.get('path'))
        all_refs=[asset_row(x) for x in pids];all_refs=[x for x in all_refs if x and x.get('kind') in ('image','audio')]
        products=[x for x in all_refs if x.get('kind')=='image']
        records=reference_logic.alias_records([ref,*all_refs])
        if any(x.get('kind')=='audio' and not x.get('duration') for x in all_refs):
            return self.err('有参考音频未能读取时长，请先安装 FFmpeg 并重新上传',409)
        try:
            mapped_instruction=reference_logic.provider_prompt(instruction,records)
        except ValueError as e:
            return self.err(str(e),409)
        wf=workflow.build_workflow(ref,products,mapped_instruction,settings.get_all())
        # The media alias used for generation MUST reference the same paid payload item index.
        wf['instruction']=mapped_instruction
        wf['user_instruction']=instruction
        wf['reference_audio_asset_ids']=[x['id'] for x in all_refs if x['kind']=='audio']
        wf['reference_bindings']=reference_logic.binding_summary([ref,*all_refs])
        src=wf.get('source') or {}
        if not src.get('duration') or not src.get('width') or not src.get('height'):
            return self.err('本地解析没有生成有效媒体证据，已阻止保存无效工作流。请重新分析。',409,source=src)
        for a in wf.get('anchors',[]):
            try:
                rel=Path(a['path']).resolve().relative_to(config.CACHE_DIR.resolve()); a['url']='/cache/'+urllib.parse.quote(str(rel).replace('\\','/'),safe='/')
            except Exception:pass
        gid=db.new_id('wfgrp'); vid=db.new_id('wfver')
        db.execute('''INSERT INTO workflows(version_id,group_id,parent_version_id,reference_asset_id,product_asset_ids_json,instruction,workflow_json,confirmed,created_at)
                      VALUES(?,?,?,?,?,?,?,0,?)''',(vid,gid,None,rid,db.dumps(pids),instruction,db.dumps(wf),db.now()))
        return self.ok({'version_id':vid,'group_id':gid,'workflow':wf,'cost':'0 图片/视频生成 API 调用','analysis_gate':(wf.get('analysis') or {}).get('paid_test_gate')})

    def _workflow_edit(self):
        req=self.json_in(); parent=req.get('parent_version_id'); prow=db.one('SELECT * FROM workflows WHERE version_id=?',(parent,))
        if not prow:return self.err('工作流版本不存在',404)
        wf=req.get('workflow')
        if not isinstance(wf,dict):return self.err('workflow 必须是对象')
        instruction=(req.get('instruction') if req.get('instruction') is not None else prow['instruction']) or ''
        pids=db.loads(prow.get('product_asset_ids_json'),[]) or []
        products=[asset_row(x) for x in pids]; products=[x for x in products if x]
        wf=workflow.refresh_execution_blueprint(wf,[x for x in products if x.get('kind')=='image'],instruction)
        vid=db.new_id('wfver')
        db.execute('UPDATE workflows SET confirmed=0 WHERE group_id=?',(prow['group_id'],))
        db.execute('''INSERT INTO workflows(version_id,group_id,parent_version_id,reference_asset_id,product_asset_ids_json,instruction,workflow_json,confirmed,created_at)
                      VALUES(?,?,?,?,?,?,?,0,?)''',(vid,prow['group_id'],parent,prow['reference_asset_id'],prow['product_asset_ids_json'],instruction,db.dumps(wf),db.now()))
        return self.ok({'version_id':vid,'group_id':prow['group_id'],'workflow':wf})

    def _workflow_confirm(self):
        req=self.json_in(); vid=req.get('version_id'); row=db.one('SELECT * FROM workflows WHERE version_id=?',(vid,))
        if not row:return self.err('工作流版本不存在',404)
        wf=db.loads(row.get('workflow_json'),{})
        gate=((wf.get('analysis') or {}).get('paid_test_gate') or {})
        if settings.get('replica_require_analysis_gate',True) and not gate.get('pass'):
            return self.err((gate.get('reason') or '本地分析证据不足，当前禁止进入付费生成。请重新分析并确认工作流。'),409,analysis_gate=gate)
        db.execute('UPDATE workflows SET confirmed=0 WHERE group_id=?',(row['group_id'],)); db.execute('UPDATE workflows SET confirmed=1 WHERE version_id=?',(vid,))
        return self.ok({'version_id':vid,'confirmed':True})

    def _router_history(self):
        """Use actual recorded tasks only; no invented latency or outcomes."""
        history=[]
        try:
            rows=db.all_("SELECT id,status,request_json,result_json,created_at,updated_at FROM tasks WHERE kind='video' ORDER BY created_at DESC LIMIT 150")
            for row in rows:
                req=db.loads(row.get('request_json'),{}) or {}
                res=db.loads(row.get('result_json'),{}) or {}
                try: model=video_adapters.canonical(req.get('_request_model') or req.get('model'))
                except ValueError:continue
                usage=db.one('SELECT actual_cost_usd FROM usage_events WHERE task_id=? AND actual_cost_usd IS NOT NULL ORDER BY created_at DESC LIMIT 1',(row['id'],))
                history.append({'model':model,'status':row['status'],
                    'elapsed_seconds':max(0,(row.get('updated_at') or row.get('created_at') or 0)-(row.get('created_at') or 0)),
                    'has_downloadable_video':bool(res.get('output_path') or res.get('asset_id') or res.get('download_path')),
                    'actual_cost_usd':usage['actual_cost_usd'] if usage else None})
        except Exception:pass
        return history

    def _route_for_selected_video(self, req, cfg):
        """Bind a user-selected model into the preview token before any paid operation.

        This app currently has a Seedance-specific payload adapter. Non-Seedance
        models are selectable/persistable, but must not hit that adapter until a
        provider-specific endpoint + pricing + parameter contract is verified.
        """
        from core.creative_production import MODEL_CHOICES
        selected=str(req.get('video_model') or cfg.get('video_base_model') or '').strip()
        selected=settings._strip_route_suffix(selected)
        canonical=video_adapters.canonical(selected)
        if canonical!=video_adapters.SEED:
            return {'request_model':canonical,'provider':None,'control':'native_adapter',
                    'preference':'provider_native','preference_label':'按模型独立适配','route_model':canonical}
        effective=dict(cfg);effective['video_base_model']=selected
        return settings.route_info(effective)

    def _direct_video_preview(self):
        req=self.json_in(); prompt=(req.get('prompt') or '').strip()
        if not prompt:return self.err('请输入视频提示词')
        aids=req.get('asset_ids') or []
        if len(aids)>50:return self.err('参考素材最多 50 个。')
        refs=[asset_row(x) for x in aids]; refs=[x for x in refs if x]
        cfg=settings.get_all()
        task_route=None; router_decision=None
        requested_mode=req.get('video_mode') or cfg['default_video_mode']
        records=reference_logic.alias_records(refs)
        try:
            reference_logic.validate_prompt_aliases(prompt,records)
            resolved_mode,mode_reason=reference_logic.infer_mode(requested_mode,refs,prompt)
            # Audio references must never be discarded by incompatible explicit modes.
            if any(x.get('kind')=='audio' for x in refs) and resolved_mode in ('text','image_reference'):
                return self.err('当前生成模式不支持音频参考：请选择“智能参考（自动选择）”或“全能参考”，避免 @audio 素材被忽略；本次不会调用付费接口。',409)
            frame_info=reference_logic.resolve_frame_roles(resolved_mode,refs,prompt)
            provider_prompt=reference_logic.provider_prompt(prompt,records)
        except ValueError as e:
            return self.err(str(e),409)

        try:
            audio_policy=audio_output.select_reference_policy(prompt,refs,req.get('audio_reference_mode'))
            audio_mode=audio_policy['mode']
            audio_source_ids=audio_policy['source_ids']
            audio_source_id=audio_source_ids[0] if audio_source_ids else None
            audio_ref_mode=audio_policy.get('reference_option')
            audio_ref_ids=audio_policy.get('reference_asset_ids') or []
            audio_output.ensure_reference_audio_exists(audio_ref_ids,asset_row)
            if audio_mode=='original':
                # A mute reference video should remain mute by default, not cause
                # an inexplicable paid-preview failure or add fabricated sound.
                source=asset_row(audio_source_id)
                if source and not media.probe(Path(source['path'])).get('has_audio'):
                    audio_mode='silent';audio_source_ids=[];audio_source_id=None
                    audio_policy['note']='参考视频无原声音轨，自动输出无声视频。'
            audio_output.validate(audio_mode,audio_source_id,asset_row,source_ids=audio_source_ids)
        except ValueError as e:return self.err(str(e),409)
        # V3.9.8.20: user selects the model directly, no task classification,
        # motion-fidelity form, or hidden quality/cheap/fast router choices.
        # The existing provider adapter still validates real input compatibility.
        try:
            chosen=req.get('video_model')
            if not chosen or chosen in ('auto','default'):
                chosen=cfg.get('video_base_model') or video_adapters.SEED
            task_route=self._route_for_selected_video({'video_model':chosen},cfg)
        except (ValueError,video_adapters.RouteError) as exc:
            return self.err('所选视频模型不可用：'+str(exc),409)
        router_decision=None
        if audio_ref_mode and video_adapters.canonical(task_route['request_model'])!=video_adapters.SEED:
            return self.err('当前音色参考功能仅对 Seedance 2.5 启用；其他模型的音色引用与口型链路未经验证。请选择 Seedance 2.5，避免付费失败。',409)

        # V3.9.3 unified local analysis router. Normal video generation shares the same
        # local understanding stack as viral-clone mode, but only activates analyzers
        # that match the actual reference media/mode. Analysis failure never blocks the
        # paid-preview firewall; it only falls back to the original prompt.
        try:
            local_analysis=smart_analysis.analyze_references(refs,purpose='direct',prompt=prompt,mode=resolved_mode)
            provider_prompt=smart_analysis.augment_prompt(provider_prompt,local_analysis)
        except Exception as e:
            local_analysis={'ok':False,'used':False,'summary':f'本地智能分析未完成，已回退原始生成链路：{type(e).__name__}: {e}'}

        try:
            voice_plan=voice_bindings.plan(prompt,refs,audio_ref_mode)
        except ValueError as e:return self.err(str(e),409)
        if audio_ref_mode:
            provider_prompt += '\n' + audio_output.reference_instructions(audio_ref_mode,refs)
            provider_prompt += '\n' + voice_plan['instruction']
        prepared=modes.prepare(resolved_mode,refs,req.get('ratio') or cfg['default_video_ratio'],int(req.get('duration') or cfg['default_video_duration']),provider_prompt,frame_roles=frame_info)
        if audio_ref_mode == 'content':
            audio_policy['source_rows']=[r for r in refs if r.get('kind')=='audio']
            audio_output.validate_content_duration(audio_policy,prepared['billing_duration'])
        create_path,query_path=settings.effective_video_paths(cfg); route=task_route
        if video_adapters.canonical(route['request_model'])==video_adapters.VIDU and prepared['mode']=='image_reference':
            if len(prepared['specs'])!=1:
                return self.err('Vidu Q3 Turbo 的图生视频需一张首帧图片；多图请显式选择首尾帧模式。',409)
            prepared['mode']='first_frame'
            prepared['specs'][0]['role']='first_frame'
            prepared['ratio']='adaptive'
            prepared['label']='Vidu 单图生视频'
        output_resolution=req.get('resolution') or cfg['default_video_resolution']
        resolution=output_resolution
        h3_plan=None
        if video_adapters.canonical(route['request_model'])==video_adapters.H3:
            try:h3_plan=video_adapters.h3_resolution_plan(output_resolution)
            except ValueError as e:return self.err(str(e),409)
            resolution=h3_plan['provider_resolution']
        if resolution not in modes.ALLOWED_RESOLUTIONS and not (h3_plan and resolution=='768P'):return self.err('不支持的清晰度')
        record_by_id={str(r['asset_id']):r for r in records}
        media_items=[]
        for s in prepared['specs']:
            r=record_by_id.get(str(s['asset']['id'])) or {}
            media_items.append({'asset_id':s['asset']['id'],'sha256':s['asset']['sha256'],'role':s['role'],'alias':r.get('alias'),'provider_alias':r.get('provider_alias')})
        if video_adapters.canonical(route['request_model'])!=video_adapters.SEED:
            try:
                video_adapters.route(route['request_model'],prepared['mode'],prepared['ratio'],resolution,prepared['duration'],
                                     [{'kind':sp['asset'].get('kind')} for sp in prepared['specs']])
                # Do not assume model-picker availability proves this OpenLux key has access.
                check=video_adapters.model_diagnostic(route['request_model'])
                if not check['available']:return self.err(check['note'],409)
            except (ValueError,openlux.ApiError) as e:return self.err('免费模型校验未通过：'+str(e),409)
        omni_task_type={'reference':'reference','edit':'edit','extend':'extend'}.get(prepared['mode'])
        payload={'mode':'direct','video_mode':prepared['mode'],'requested_video_mode':requested_mode,'mode_reason':mode_reason,
                 'prompt':provider_prompt,'user_prompt':prompt,'asset_ids':aids,'media':media_items,'ratio':prepared['ratio'],'resolution':resolution,
                 'duration':prepared['duration'],'requested_duration':prepared['requested_duration'],'billing_duration':prepared['billing_duration'],
                 'generate_audio':audio_mode=='model','audio_mode':audio_mode,'audio_source_asset_id':audio_source_id,'audio_source_asset_ids':audio_source_ids,'audio_reason':audio_policy['reason'],'audio_reference_mode':audio_ref_mode,'audio_reference_asset_ids':audio_ref_ids,'audio_voice_bindings':voice_plan['bindings'],
                 'output_format':prepared['output_format'],
                 'model':route['request_model'],'provider':route.get('provider'),'route_control':route['control'],'route_preference':route['preference'],
                 'router_decision':router_decision,
                 'output_resolution':output_resolution,
                 'local_export_resolution':output_resolution if h3_plan and h3_plan['local_downscale'] else None,
                 'reference_bindings':reference_logic.binding_summary(refs,frame_info),'frame_binding_source':frame_info.get('source'),
                 'omni_reference_task_type':omni_task_type,
                 '_create_path':create_path,'_query_path':query_path}
        if video_adapters.canonical(payload['model'])!=video_adapters.SEED:
            caps=video_adapters.capabilities(payload['model'])
            payload['_query_path']=caps['query']
            payload['_provider_adapter']=payload['model']
        fp=fingerprint(payload); token=db.new_id('preview'); exp=db.now()+config.PREVIEW_TTL_SECONDS
        db.execute('INSERT INTO generation_previews(token,fingerprint,payload_json,expires_at,consumed_at,created_at) VALUES(?,?,?,?,NULL,?)',(token,fp,db.dumps(payload),exp,db.now()))
        bd=max(0,float(prepared['billing_duration'] or 0)); est=(config.VIDEO_ESTIMATED_COST_PER_SECOND_USD*bd) if config.VIDEO_ESTIMATED_COST_PER_SECOND_USD>0 and not payload.get('_provider_adapter') else None
        warnings=list(prepared['warnings'])
        if h3_plan:warnings.append(h3_plan['explanation'])
        if local_analysis.get('summary'):
            warnings.append(local_analysis.get('summary'))
        media_preview=media_preflight.preview_specs(prepared['specs'])
        warnings.extend(media_preview.get('warnings') or [])
        if prepared['mode'] in ('reference','edit','extend') and not payload.get('_provider_adapter'):
            warnings.append('Seedance 2.5 技术参数由系统自动生成：任务类型、素材 role、ratio、duration、output_format 无需手动填写。')
        if prepared['mode']=='first_last':
            if frame_info.get('source')=='explicit_prompt':warnings.append(f"首尾帧按提示词真实绑定：{frame_info.get('first_alias')}=首帧，{frame_info.get('last_alias')}=尾帧。")
            else:warnings.append(f"未指定首尾帧，按上传顺序真实绑定：{frame_info.get('first_alias')}=首帧，{frame_info.get('last_alias')}=尾帧。")
        elif prepared['mode']=='first_frame':
            if frame_info.get('source')=='explicit_prompt':warnings.append(f"首帧按提示词真实绑定：{frame_info.get('first_alias')}=首帧。")
            else:warnings.append(f"未指定首帧，按上传顺序真实绑定：{frame_info.get('first_alias')}=首帧。")
        requested_label='智能参考（自动选择）' if requested_mode=='auto' else modes.MODE_LABELS.get(requested_mode,requested_mode)
        return self.ok({'preview_token':token,'expires_in':config.PREVIEW_TTL_SECONDS,'model':payload['model'],'mode':prepared['label'],'requested_mode':requested_label,'mode_reason':mode_reason,'calls':{'image':0,'video':1},
                        'duration':'继承原视频' if prepared['duration']==-1 else prepared['duration'],'billing_duration':prepared['billing_duration'],
                        'resolution':output_resolution,'provider_resolution':resolution,'ratio':prepared['ratio'],'output_format':prepared['output_format'],'estimated_cost_usd':est,
                        'pricing_note':'未配置真实单价，不能把未知费用显示为 0。' if est is None else '按 .env 中配置的每秒估算价计算。',
                        'warnings':warnings+voice_plan['warnings'],'media_count':len(media_items),'create_path':payload.get('_create_path'),
                        'route_control':payload.get('route_control'),'route_preference':payload.get('route_preference'),'provider':payload.get('provider'),
                        'reference_bindings':payload.get('reference_bindings'),
                        'router_decision':router_decision,'audio_mode':audio_mode,'audio_reference_mode':audio_ref_mode,'audio_voice_bindings':voice_plan['bindings'],
                        'audio_note':audio_policy['note'],
                        'local_analysis':{'used':local_analysis.get('used',False),'ok':local_analysis.get('ok',False),'summary':local_analysis.get('summary',''),'used_tools':local_analysis.get('used_tools',[])},
                        'media_preflight':media_preview,'api_parameters':'自动管理'})

    def _ensure_video_slot(self):
        # Capacity checks are DB-local. The background watcher owns provider polling;
        # a new submission must not synchronously query up to 10 upstream tasks.
        n=active_video_count(False)
        limit=settings.effective_limits().get('max_concurrent_video_tasks',config.MAX_CONCURRENT_VIDEO_TASKS)
        if n>=limit:
            return False,n
        return True,n

    def _direct_video_generate(self):
        req=self.json_in(); token=req.get('preview_token'); pre=db.one('SELECT * FROM generation_previews WHERE token=?',(token,))
        if not pre:return self.err('生成确认令牌不存在，请重新预览调用。',409)
        if pre['expires_at']<db.now():return self.err('生成确认令牌已过期，请重新预览。',409)
        payload=db.loads(pre['payload_json'],{}); fp=pre['fingerprint']
        if payload.get('mode')!='direct':return self.err('令牌类型不匹配',409)
        running=db.task_recent_by_fp(fp,None)
        if running and running['status'] in ('pending','running','result_pending','submission_unknown'):
            return self.ok({'task':task_to_public(running),'reused':True,'reason':'相同视频任务已经存在，未再次调用 Seedance。'})
        dedupe=settings.effective_limits().get('dedupe_seconds',config.DEDUPE_SECONDS)
        recent=db.task_recent_by_fp(fp,dedupe)
        if recent:return self.ok({'task':task_to_public(recent),'reused':True,'reason':f'{dedupe} 秒内相同提交已拦截。'})
        slot,n=self._ensure_video_slot()
        if not slot:
            limit=settings.effective_limits().get('max_concurrent_video_tasks',config.MAX_CONCURRENT_VIDEO_TASKS)
            return self.err(f'当前已有 {n} 个视频任务运行，达到本地并发上限 {limit}。请等待已有任务完成后再提交；本次未调用付费接口。',409)
        specs=specs_from_payload(payload); public_base=None
        if specs:
            ready=public_assets.prepare_specs(specs,min_ttl_minutes=720)
            if not ready.get('ok'):return self.err('公网参考素材准备失败；本次尚未调用付费视频接口。',409,public_assets=ready)
            specs=ready.get('specs') or specs; public_base=None
            payload['_auto_media_fixes']=ready.get('auto_fixes') or []
        # Consume only after all local/tunnel preconditions pass, immediately before the paid POST.
        # Final paid gate. Only the winner of this atomic DB claim may call OpenLux.
        claim=db.claim_video_submission(
            token,
            dedupe,
            settings.effective_limits().get('max_concurrent_video_tasks',config.MAX_CONCURRENT_VIDEO_TASKS),
            expected_mode='direct'
        )
        if claim['state']=='reused':
            return self.ok({
                'task':task_to_public(claim['task']),
                'reused':True,
                'reason':'并发重复提交已由数据库原子锁拦截；复用已有任务。'
            })
        if claim['state']=='capacity':
            return self.err(
                f"当前已有 {claim.get('active_count',0)} 个视频任务运行，达到本地并发上限；本次未调用付费接口。",
                409
            )
        if claim['state']=='expired':
            return self.err('生成确认令牌已过期，请重新预览。',409)
        if claim['state']=='consumed':
            return self.err('该确认令牌已经被其他请求占用；本次未调用付费接口。',409)
        if claim['state']=='mode_mismatch':
            return self.err('令牌类型不匹配；本次未调用付费接口。',409)
        if claim['state']=='missing':
            return self.err('生成确认令牌不存在，请重新预览调用。',409)
        if claim['state']!='claimed':
            return self.err('未获得本地付费提交锁；为避免重复扣费，本次未调用视频接口。',409)
        tid=claim['task_id']
        db.task_update(tid,status='running')
        try:
            if payload.get('_provider_adapter'):
                resp,provider_payload=video_adapters.create(payload['model'],payload['prompt'],specs,payload['ratio'],payload['resolution'],payload['duration'],payload['generate_audio'],public_base,idempotency_key=fp)
            else:
                resp,provider_payload=openlux.video_create(payload['prompt'],specs,payload['ratio'],payload['resolution'],payload['duration'],payload['generate_audio'],public_base,idempotency_key=fp,output_format=payload.get('output_format','mp4'),model=payload.get('model'),provider=payload.get('provider'),create_path=payload.get('_create_path'),query_path=payload.get('_query_path'),omni_reference_task_type=payload.get('omni_reference_task_type'))
            pid=openlux.extract_task_id(resp)
            if not pid:
                db.task_update(tid,status='submission_unknown',response_json=resp,error='接口响应中没有识别到 task_id/id。禁止自动重提。')
                return self.err('接口返回但没有识别到任务 ID。系统不会自动重提，请先检查 OpenLux 后台。',502,task=task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))))
            payload['_query_path']=provider_payload.get('_query_path'); payload['_create_path']=provider_payload.get('_create_path'); payload['_request_model']=provider_payload.get('_request_model'); db.task_update(tid,status='running',provider_task_id=pid,response_json=resp,request_json=payload)
            bd=max(0,float(payload.get('billing_duration') or 0)); est=(config.VIDEO_ESTIMATED_COST_PER_SECOND_USD*bd) if config.VIDEO_ESTIMATED_COST_PER_SECOND_USD>0 and video_adapters.canonical(payload['model'])==video_adapters.SEED else None
            actual_model=provider_payload.get('_request_model') or payload.get('model'); db.usage_add(tid,'video',actual_model,bd,est,None,{'resolution':payload['resolution'],'ratio':payload['ratio'],'provider_task_id':pid,'mode':payload.get('video_mode'),'create_path':provider_payload.get('_create_path')})
            cost_system.clear_balance_cache()
            return self.ok({'task':task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))),'estimated_cost_usd':est,'paid_create_calls':1,'actual_request_model':actual_model})
        except openlux.ApiError as e:
            ambiguous=e.status is None or (e.status and e.status>=500)
            provider_detail=(e.body or '').strip()
            task_error=str(e)+(('｜'+provider_detail[:1200]) if provider_detail else '')
            db.task_update(tid,status='submission_unknown' if ambiguous else 'failed',error=task_error,response_json={'provider_status':e.status,'provider_body':e.body})
            if ambiguous:return self.err('视频提交结果不确定。系统已停止自动重试，避免二次扣费。请先检查 OpenLux 后台是否已经产生任务。',502,task=task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))),ambiguous_submission=True)
            raise

    def _video_preview(self):
        req=self.json_in(); wvid=req.get('workflow_version_id'); row=db.one('SELECT * FROM workflows WHERE version_id=?',(wvid,))
        if not row:return self.err('工作流版本不存在',404)
        if not row['confirmed']:return self.err('当前内部工作流尚未确认，请重新点击“分析并准备生成”。',409)

        wf=db.loads(row['workflow_json'],{})
        ref=asset_row(row['reference_asset_id'])
        if not ref or ref.get('kind')!='video':
            return self.err('参考视频不存在或不是有效视频，请重新上传。',409)
        ref=_refresh_asset_media_metadata(ref,force=False)
        pids=db.loads(row['product_asset_ids_json'],[]) or []
        all_refs=[asset_row(x) for x in pids]; all_refs=[x for x in all_refs if x]
        products=[x for x in all_refs if x.get('kind')=='image']
        audio_refs=[x for x in all_refs if x.get('kind')=='audio']

        if wf and not wf.get('execution_blueprint'):
            wf=workflow.refresh_execution_blueprint(wf,[x for x in products if x.get('kind')=='image'],row.get('instruction') or '')
        blueprint=wf.get('execution_blueprint') or {}
        strategy=blueprint.get('generation_strategy') or {}

        cfg=settings.get_all()
        create_path,query_path=settings.effective_video_paths(cfg)
        route=settings.route_info(cfg)
        rawdur=float((ref or {}).get('duration') or wf.get('duration') or 0)
        if rawdur <= 0:
            return self.err('没有读取到参考视频有效时长，已阻止进入付费生成。',409)
        if rawdur > 30.5:
            return self.err(f'参考视频约 {rawdur:.2f} 秒，超过当前视频编辑可接受范围，请先裁剪素材。',409)
        try:
            audio_policy=audio_output.select_reference_policy(row.get('instruction') or wf.get('user_instruction') or '',[ref,*all_refs],req.get('audio_reference_mode'))
            audio_mode=audio_policy['mode']
            audio_source_ids=audio_policy['source_ids']
            audio_source_id=audio_source_ids[0] if audio_source_ids else None
            audio_ref_mode=audio_policy.get('reference_option')
            audio_ref_ids=audio_policy.get('reference_asset_ids') or []
            audio_output.ensure_reference_audio_exists(audio_ref_ids,asset_row)
            if audio_mode=='original' and not media.probe(Path(ref['path'])).get('has_audio'):
                audio_mode='silent';audio_source_ids=[];audio_source_id=None
                audio_policy['note']='参考视频无原声音轨，自动输出无声视频。'
            audio_output.validate(audio_mode,audio_source_id,asset_row,source_ids=audio_source_ids)
        except ValueError as e:return self.err(str(e),409)
        # V3.9.8.20: replica uses the explicitly selected or global default model.
        # Preserve original Seedance edit and H3 reference-recreation adapters.
        try:
            chosen=req.get('video_model')
            if not chosen or chosen in ('auto','default'):
                chosen=cfg.get('video_base_model') or video_adapters.SEED
            route=self._route_for_selected_video({'video_model':chosen},cfg)
        except (ValueError,video_adapters.RouteError) as exc:
            return self.err('所选复刻模型不可用：'+str(exc),409)
        replica_decision=None


        resolution=req.get('resolution') or cfg.get('default_video_resolution','720p')
        # Replica is a source-preserving transformation. Use the same validated edit
        # semantics as the normal "视频编辑" mode instead of passing a rounded duration.
        edit_prompt=workflow.workflow_prompt(wf)
        is_h3=(video_adapters.canonical(route['request_model'])==video_adapters.H3)
        h3_plan=None
        if is_h3:
            try:h3_plan=video_adapters.h3_resolution_plan(resolution)
            except ValueError as e:return self.err(str(e),409)
        is_vidu=(video_adapters.canonical(route['request_model'])==video_adapters.VIDU)
        if is_vidu:return self.err('Vidu Q3 Turbo 的视频复刻当前没有可验证的原生视频参考编辑接口。请选择 H3 的参考重制或 Seedance 视频编辑；本次未付费。',409)
        if audio_ref_mode and (is_h3 or is_vidu):return self.err('当前音色参考功能仅对 Seedance 2.5 启用；其他模型的音色引用与口型链路未经验证。请选择 Seedance 2.5，避免付费失败。',409)
        if is_h3 and rawdur>15:return self.err('H3 单段参考视频不超过15秒，请裁剪后再测试。',409)
        try:
            voice_plan=voice_bindings.plan(row.get('instruction') or wf.get('user_instruction') or '',[ref,*all_refs],audio_ref_mode)
        except ValueError as e:return self.err(str(e),409)
        if audio_ref_mode:
            edit_prompt += '\n' + audio_output.reference_instructions(audio_ref_mode,[ref,*all_refs])
            edit_prompt += '\n' + voice_plan['instruction']
            if audio_ref_mode == 'content':
                audio_policy['source_rows']=audio_refs
                try:audio_output.validate_content_duration(audio_policy,rawdur)
                except ValueError as e:return self.err(str(e),409)
        prepared=modes.prepare(
            'reference' if is_h3 else 'edit',
            [ref,*all_refs],
            '9:16' if is_h3 else 'adaptive',
            max(4,min(15 if is_h3 else 30,int(round(rawdur)))),
            edit_prompt,
        )

        warnings=list(prepared.get('warnings') or [])+voice_plan['warnings']
        if h3_plan:warnings.append(h3_plan['explanation'])
        smart_tracking=((wf.get('analysis') or {}).get('smart_tracking') or {})
        pose_track=smart_tracking.get('pose') or {}; object_track=smart_tracking.get('object_tracking') or {}
        if pose_track.get('used'):
            warnings.append(f"本地动作分析：MediaPipe 已参与，人物姿态覆盖 {float(pose_track.get('person_presence_ratio') or 0):.0%}，手部覆盖 {float(pose_track.get('hand_presence_ratio') or 0):.0%}。")
        if object_track.get('used'):
            warnings.append(f"本地目标追踪：YOLO + ByteTrack 已参与，共形成 {len(object_track.get('tracks') or [])} 条通用目标轨迹。")
        elif object_track.get('available') and not object_track.get('model_ready',True):
            warnings.append('YOLO + ByteTrack 已安装但尚无本地模型权重；本次复刻继续使用其他本地分析证据，不会静默联网下载模型。')
        for risk in (blueprint.get('risk_flags') or []):
            if risk and '没有检测到需要阻断生成' not in str(risk):
                warnings.append('分析提醒：'+str(risk))

        media_items=[
            {'asset_id':s['asset']['id'],'sha256':s['asset']['sha256'],'role':s['role']}
            for s in prepared['specs']
        ]
        # Use the exact source duration only for local cost/history metadata.
        # The API request itself must keep duration=-1.
        billing_duration=round(rawdur,3)

        payload={
            'mode':'replica',
            'video_mode':'reference' if is_h3 else 'edit',
            'workflow_version_id':wvid,
            'reference_asset_id':ref['id'],'product_asset_ids':[a['id'] for a in all_refs],
            'original_instruction':str(row['instruction'] or ''),
            'audio_bound_prompt':edit_prompt,
            'media':media_items,
            'ratio':'9:16' if is_h3 else 'adaptive',
            'resolution':h3_plan['provider_resolution'] if is_h3 else resolution,
            'output_resolution':resolution,
            'local_export_resolution':resolution if h3_plan and h3_plan['local_downscale'] else None,
            'duration':max(4,min(15,int(round(rawdur)))) if is_h3 else -1,
            'requested_duration':round(rawdur,3),
            'billing_duration':billing_duration,
            'generate_audio':audio_mode=='model','audio_mode':audio_mode,'audio_source_asset_id':audio_source_id,'audio_source_asset_ids':audio_source_ids,'audio_reason':audio_policy['reason'],'audio_reference_mode':audio_ref_mode,'audio_reference_asset_ids':audio_ref_ids,'audio_voice_bindings':voice_plan['bindings'],
            'output_format':prepared.get('output_format') or 'mov',
            'model':route['request_model'],
            'provider':route.get('provider'),
            'route_control':route['control'],
            'route_preference':route['preference'],
            'router_decision':replica_decision,
            'execution_strategy':strategy.get('label') or '视频编辑复刻',
            'execution_strategy_code':'video_edit',
            'omni_reference_task_type':None if is_h3 else 'edit',
            'blueprint_schema':blueprint.get('schema'),
            'blueprint_goal':blueprint.get('goal'),
            '_create_path':create_path,
            '_query_path':query_path,
        }

        # Final local validation happens before a preview token is issued.
        try:
            if is_h3:
                spec_check=specs_from_payload(payload)
                video_adapters.route(payload['model'],'reference',payload['ratio'],payload['resolution'],payload['duration'],
                    [{'kind':sp['asset'].get('kind')} for sp in spec_check])
                model_check=video_adapters.model_diagnostic(payload['model'])
                if not model_check['available']:return self.err(model_check['note'],409)
                payload['_provider_adapter']=payload['model']
                payload['_query_path']=video_adapters.H3_QUERY
            else:validate_replica_paid_payload(payload,specs_from_payload(payload))
        except (ValueError,openlux.ApiError) as e:
            return self.err(str(e),409)

        fp=fingerprint(payload); token=db.new_id('preview'); exp=db.now()+config.PREVIEW_TTL_SECONDS
        db.execute(
            'INSERT INTO generation_previews(token,fingerprint,payload_json,expires_at,consumed_at,created_at) VALUES(?,?,?,?,NULL,?)',
            (token,fp,db.dumps(payload),exp,db.now())
        )

        replica_media_preview=media_preflight.preview_specs(specs_from_payload(payload))
        warnings.extend(replica_media_preview.get('warnings') or [])
        warnings.append('H3 使用参考重制模式，不是 Seedance 的1:1视频编辑；动作与场景仍需人工检查。' if is_h3 else '爆款复刻 API 技术参数由系统自动管理：edit / adaptive / -1 / MOV / 素材 role 无需手动填写。')

        est=(config.VIDEO_ESTIMATED_COST_PER_SECOND_USD*billing_duration) if config.VIDEO_ESTIMATED_COST_PER_SECOND_USD>0 and not is_h3 else None
        route_label=settings.ROUTE_PREFERENCES.get(route.get('preference') or '',{}).get('label') if route.get('preference') else '由 Token 设置决定'
        if est is None:
            pricing_note=f'OpenLux 实际渠道可能变化；当前路由为“{route_label}”，无法可靠给出精确费用，因此不显示假精确金额。'
        else:
            pricing_note=f'仅按你在 .env 配置的本地参考单价估算；OpenLux 实际渠道与最终账单仍可能变化。当前路由：{route_label}。'

        return self.ok({
            'preview_token':token,
            'expires_in':config.PREVIEW_TTL_SECONDS,
            'model':payload['model'],
            'mode':'H3 参考重制（非精确视频编辑）' if is_h3 else '爆款复刻（视频编辑）',
            'calls':{'image':0,'video':1},
            'duration':f'参考重制（目标 {payload["duration"]}s）' if is_h3 else f'继承原视频（约 {rawdur:.2f}s）',
            'billing_duration':billing_duration,
            'resolution':resolution,
            'provider_resolution':payload['resolution'],
            'ratio':'adaptive（继承原视频）',
            'output_format':payload['output_format'],
            'estimated_cost_usd':est,
            'pricing_note':pricing_note,
            'warnings':warnings,
            'media_count':len(media_items),
            'create_path':payload.get('_create_path'),
            'router_decision':replica_decision,
            'execution_strategy':payload.get('execution_strategy'),
            'audio_mode':audio_mode,'audio_reference_mode':audio_ref_mode,'audio_note':audio_policy['note'],'audio_voice_bindings':voice_plan['bindings'],
            'route_control':payload.get('route_control'),
            'route_preference':payload.get('route_preference'),
            'provider':payload.get('provider'),
            'media_preflight':replica_media_preview,
            'api_parameters':'自动管理',
        })

    def _video_generate(self):
        req=self.json_in(); token=req.get('preview_token'); pre=db.one('SELECT * FROM generation_previews WHERE token=?',(token,))
        if not pre:return self.err('生成确认令牌不存在，请重新预览调用。',409)
        if pre['expires_at']<db.now():return self.err('生成确认令牌已过期，请重新预览。',409)
        payload=db.loads(pre['payload_json'],{}); fp=pre['fingerprint']; running=db.task_recent_by_fp(fp,None)
        if running and running['status'] in ('pending','running','result_pending','submission_unknown'):return self.ok({'task':task_to_public(running),'reused':True,'reason':'相同视频任务已经存在，未再次调用 Seedance。'})
        dedupe=settings.effective_limits().get('dedupe_seconds',config.DEDUPE_SECONDS)
        recent=db.task_recent_by_fp(fp,dedupe)
        if recent:return self.ok({'task':task_to_public(recent),'reused':True,'reason':f'{dedupe} 秒内相同视频提交已拦截。'})
        slot,n=self._ensure_video_slot()
        if not slot:
            limit=settings.effective_limits().get('max_concurrent_video_tasks',config.MAX_CONCURRENT_VIDEO_TASKS)
            return self.err(f'当前已有 {n} 个视频任务运行，达到本地并发上限 {limit}；本次未调用付费接口。',409)
        wrow=db.one('SELECT * FROM workflows WHERE version_id=?',(payload['workflow_version_id'],))
        if not wrow or not wrow['confirmed']:return self.err('工作流确认状态已经失效，请重新确认。',409)
        wf=db.loads(wrow['workflow_json'],{}); specs=specs_from_payload(payload)
        try:
            if payload.get('_provider_adapter'):
                video_adapters.route(payload['model'],'reference',payload['ratio'],payload['resolution'],payload['duration'],
                    [{'kind':sp['asset'].get('kind')} for sp in specs])
            else:validate_replica_paid_payload(payload,specs)
        except ValueError as e:
            return self.err(str(e),409)
        ready=public_assets.prepare_specs(specs,min_ttl_minutes=720); public_base=None
        if not ready.get('ok'):return self.err('公网参考素材准备失败，未调用视频生成接口。',409,public_assets=ready)
        specs=ready.get('specs') or specs
        payload['_auto_media_fixes']=ready.get('auto_fixes') or []
        # Final paid gate for viral-clone generation.
        claim=db.claim_video_submission(
            token,
            dedupe,
            settings.effective_limits().get('max_concurrent_video_tasks',config.MAX_CONCURRENT_VIDEO_TASKS),
            expected_mode=None
        )
        if claim['state']=='reused':
            return self.ok({
                'task':task_to_public(claim['task']),
                'reused':True,
                'reason':'并发重复提交已由数据库原子锁拦截；复用已有任务。'
            })
        if claim['state']=='capacity':
            return self.err(
                f"当前已有 {claim.get('active_count',0)} 个视频任务运行，达到本地并发上限；本次未调用付费接口。",
                409
            )
        if claim['state']=='expired':
            return self.err('生成确认令牌已过期，请重新预览。',409)
        if claim['state']=='consumed':
            return self.err('该确认令牌已经被其他请求占用；本次未调用付费接口。',409)
        if claim['state']=='missing':
            return self.err('生成确认令牌不存在，请重新预览调用。',409)
        if claim['state']!='claimed':
            return self.err('未获得本地付费提交锁；为避免重复扣费，本次未调用视频接口。',409)
        tid=claim['task_id']
        db.task_update(tid,status='running')
        prompt=payload.get('audio_bound_prompt') or workflow.workflow_prompt(wf)
        try:
            if payload.get('_provider_adapter'):
                resp,provider_payload=video_adapters.create(payload['model'],prompt,specs,payload['ratio'],payload['resolution'],payload['duration'],payload['generate_audio'],public_base,idempotency_key=fp)
            else:
                resp,provider_payload=openlux.video_create(prompt,specs,payload['ratio'],payload['resolution'],payload['duration'],payload['generate_audio'],public_base,idempotency_key=fp,output_format=payload.get('output_format','mp4'),model=payload.get('model'),provider=payload.get('provider'),create_path=payload.get('_create_path'),query_path=payload.get('_query_path'),omni_reference_task_type='edit')
            pid=openlux.extract_task_id(resp)
            if not pid:
                db.task_update(tid,status='submission_unknown',response_json=resp,error='接口响应中没有识别到 task_id/id。禁止自动重提。')
                return self.err('视频接口返回后没有识别到任务 ID。为了避免重复扣费，系统不会自动重提；请先检查 OpenLux 后台账单/任务记录。',502,task=task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))))
            payload['_query_path']=provider_payload.get('_query_path'); payload['_create_path']=provider_payload.get('_create_path'); payload['_request_model']=provider_payload.get('_request_model'); db.task_update(tid,status='running',provider_task_id=pid,response_json=resp,request_json=payload); bd=float(payload.get('billing_duration') or payload.get('duration') or 0); est=(config.VIDEO_ESTIMATED_COST_PER_SECOND_USD*bd) if config.VIDEO_ESTIMATED_COST_PER_SECOND_USD>0 and video_adapters.canonical(payload['model'])==video_adapters.SEED else None
            actual_model=provider_payload.get('_request_model') or payload.get('model'); db.usage_add(tid,'video',actual_model,bd,est,None,{'resolution':payload['resolution'],'ratio':payload['ratio'],'provider_task_id':pid,'mode':'replica_edit','create_path':provider_payload.get('_create_path')})
            cost_system.clear_balance_cache()
            return self.ok({'task':task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))),'estimated_cost_usd':est,'paid_create_calls':1,'actual_request_model':actual_model})
        except openlux.ApiError as e:
            ambiguous=e.status is None or (e.status and e.status>=500)
            provider_detail=(e.body or '').strip()
            task_error=str(e)+(('｜'+provider_detail[:1200]) if provider_detail else '')
            db.task_update(tid,status='submission_unknown' if ambiguous else 'failed',error=task_error,response_json={'provider_status':e.status,'provider_body':e.body})
            if ambiguous:return self.err('视频提交结果不确定。系统已停止自动重试，避免二次扣费。请先检查 OpenLux 后台是否已经产生任务。',502,task=task_to_public(db.one('SELECT * FROM tasks WHERE id=?',(tid,))),ambiguous_submission=True)
            raise


def run(open_browser=False):
    _boot_phase("准备绑定本地服务端口")
    server=ThreadingHTTPServer((config.HOST,config.PORT),Handler)
    config.PID_PATH.write_text(str(os.getpid()),encoding='utf-8')
    team_safety.recover()
    storage_manager.reconcile_moves()
    _boot_phase("端口已绑定，正在初始化热点及商品数据表")
    url=f'http://{config.HOST}:{config.PORT}'
    log.info('Seedance Studio %s started at %s model=%s',config.APP_VERSION,url,settings.effective_video_model())
    hot_radar.ensure_schema()
    hot_radar.record_startup()
    product_memory.ensure_schema()
    ai_assistant.schema()
    bright_tiktok.schema()
    viral_review.schema()
    creative_production.ensure_schema()
    _boot_phase("数据库初始化完成，正在恢复历史任务状态")
    task_observability.recover_interrupted_on_start()
    hypit_service.recover()
    # Hotspot collection is manually triggered only; there is no radar scheduler thread.
    _task_poller_stop.clear()
    poller=threading.Thread(target=_background_task_poller,args=(_task_poller_stop,),name='video-task-poller',daemon=True)
    poller.start()
    threading.Thread(target=storage_manager.worker,args=(_task_poller_stop,),name="storage-maintenance",daemon=True).start()
    if open_browser: threading.Timer(0.8,lambda:webbrowser.open(url)).start()
    _boot_phase("服务已就绪，等待浏览器连接")
    try:server.serve_forever()
    finally:
        _task_poller_stop.set()
        hypit_service.close_studio()
        try:poller.join(timeout=2)
        except Exception:pass
        try:config.PID_PATH.unlink(missing_ok=True)
        except Exception:pass
        try:tunnel.stop()
        except Exception:pass

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--open-browser',action='store_true');args=ap.parse_args();run(args.open_browser)
