"""OpenLux provider-specific paid video routes. No automatic paid fallback."""
from __future__ import annotations
import urllib.parse
from . import openlux, settings, config

SEED='doubao-seedance-2-5-260628'
VIDU='viduq3-turbo'
H3='minimax-video-h3'
WAN='wan3.0-video' # official OpenLux catalog identifier
# H3 route follows MiniMax V2 native API; OpenLux relay support is account-dependent.
# Never assume a listed model proves the provider path: paid POST still requires explicit preview+authorization.
H3_CREATE='/minimax/v2/video_generation'
H3_QUERY='/minimax/v2/query/video_generation/{task_id}'
VIDU_QUERY='/ent/v2/tasks/{task_id}/creations'

class RouteError(ValueError):pass

def canonical(model):
    m=str(model or '').split(':')[0]
    if m.startswith('doubao-seedance-2-5-'):return SEED
    if m in (VIDU,'viduq3-turbo'):return VIDU
    if m in (H3,'MiniMax-H3','minimax-h3'):return H3
    if m in ('wan-3.0',WAN,'wan3.0'):return WAN
    raise RouteError(f'视频模型 {m} 没有已实现的 OpenLux 生成适配器；不会误用 Seedance 请求。')

def capabilities(model):
    m=canonical(model)
    if m==VIDU:return {'model':VIDU,'provider':'Vidu','modes':['text','first_frame','first_last'],'max_seconds':16,'create':'/ent/v2/text2video | /ent/v2/img2video | /ent/v2/start-end2video','query':VIDU_QUERY,'status':'provider_schema_documented_account_untested'}
    if m==WAN:return {'model':WAN,'provider':'Wan / Alibaba','modes':['text','first_frame','first_last','reference'],'max_seconds':30,'create':None,'query':None,'status':'openlux_model_listed_provider_route_unverified','account_ready':False,'note':'OpenLux 价格表确认模型 ID，但未查到此模型在 OpenLux 的专用创建/查询接口。禁止调用其他服务商地址冒充 OpenLux。'}
    if m==H3:return {'model':'MiniMax-H3','provider':'MiniMax','modes':['text','first_frame','first_last','reference'],'max_seconds':15,'create':H3_CREATE,'query':H3_QUERY,'status':'openlux_relay_needs_validation'}
    return {'model':SEED,'provider':'Seedance','modes':['text','first_frame','first_last','reference','edit','extend'],'max_seconds':30,'status':'legacy'}

def validate(model,mode,ratio,resolution,duration,media):
    m=canonical(model)
    if m==SEED:return
    if m==WAN:
        if mode not in ('text','first_frame','first_last','reference'):
            raise RouteError('Wan 3.0 不允许将编辑或续写错误映射为普通图生视频。')
        if not 2<=int(duration)<=30:raise RouteError('Wan 3.0 当前参考能力支持2–30秒。')
        if resolution.lower() not in ('480p','720p','1080p'):
            raise RouteError('Wan 3.0 当前候选只接受480P/720P/1080P；更高清晰度需单独核验。')
        if ratio not in ('16:9','9:16','1:1','4:3','3:4','adaptive'):
            raise RouteError('Wan 3.0 不支持此比例，请选择9:16等常见比例。')
        expected={'text':0,'first_frame':1,'first_last':2}.get(mode)
        if expected is not None and len(media)!=expected:raise RouteError('Wan 3.0 首尾帧素材数量不匹配。')
        return
    if m==VIDU:
        if mode not in ('text','first_frame','first_last'):
            raise RouteError('Vidu Q3 Turbo 当前已核对的是文生、单图及首尾帧接口；不能把视频复刻/视频编辑错误地送往文生视频接口。')
        if not 1<=int(duration)<=16:raise RouteError('Vidu Q3 Turbo 支持的生成时长为 1–16 秒。')
        if resolution not in ('540p','720p','1080p'):raise RouteError('Vidu Q3 Turbo 当前测试清晰度仅支持 540p / 720p / 1080p。')
        images=[a for a in media if a.get('kind')=='image']
        expected={'text':0,'first_frame':1,'first_last':2}[mode]
        if len(media)!=expected or len(images)!=expected:raise RouteError('Vidu 当前模式的参考素材数量不匹配；文生0张、单图1张、首尾帧2张，不能附带不受支持的视频或音频。')
    if m==H3:
        if mode not in ('text','first_frame','first_last','reference','image_reference'):
            raise RouteError('MiniMax H3 目前只开放文本、首尾帧、参考生成适配；视频编辑/续写不能冒充参考生成。')
        if not 4<=int(duration)<=15:raise RouteError('MiniMax H3 生成时长为 4–15 秒；请选择符合范围的秒数。')
        if resolution not in ('2K','2k','768P','768p'):
            raise RouteError('MiniMax H3 原生请求参数仅支持768P和2K，1080P成片需通过2K生成后本地缩小。')
        if mode=='text' and media:raise RouteError('H3 文生视频不能携带参考素材；请切换为全能参考。')
        if mode=='first_frame' and len(media)!=1:raise RouteError('H3 首帧模式需要恰好1张图片。')
        if mode=='first_last' and len(media)!=2:raise RouteError('H3 首尾帧模式需要恰好2张图片。')
        if ratio=='adaptive' and mode=='text':raise RouteError('H3 纯文本模式需要指定 9:16 或 16:9 等明确比例。')

# Distinguish user-requested exported clip from the original provider's native tier.
# H3 native API documents only 768P and 2K, not 1080P.
# Never bill the user as if 1080P were a cheaper native provider tier.
_H3_EXPORT_TO_NATIVE={'480p':'768P','720p':'768P','768P':'768P','768p':'768P',
                      '1080p':'2K','2K':'2K','2k':'2K'}
def h3_resolution_plan(export_resolution):
    output=str(export_resolution or '2K')
    native=_H3_EXPORT_TO_NATIVE.get(output)
    if native is None:
        raise RouteError('MiniMax H3 原生只提供768P和2K；请选480P、720P、768P、1080P或2K导出，系统会显示实际模型档位。')
    resize=output.lower() in ('480p','720p','1080p')
    return {'output_resolution':output,'provider_resolution':native,
            'local_downscale':resize,'quote_resolution':native,
            'explanation':('使用H3原生'+native+'档位生成，完成后由本地FFmpeg缩小为'+output+'；按'+native+'生成档位预检费用。'
                           if resize else '使用H3原生'+native+'档位；不需要二次缩放。')}

# The single source of truth for UI resolution menus, including final exports.
# These are supported by the currently implemented adapters, not an assertion
# that the user's particular OpenLux account has passed a paid generation test.
RESOLUTION_MENUS={
 SEED:[{'value':'480p','label':'480P｜现有生成规格','native':'480p'},
       {'value':'720p','label':'720P｜现有生成规格','native':'720p'},
       {'value':'1080p','label':'1080P｜现有生成规格','native':'1080p'},
       {'value':'2K','label':'2K｜现有生成规格','native':'2K'}],
 VIDU:[{'value':'540p','label':'540P｜模型原生档位','native':'540p'},
       {'value':'720p','label':'720P｜模型原生档位','native':'720p'},
       {'value':'1080p','label':'1080P｜模型原生档位','native':'1080p'}],
 H3:[{'value':'768P','label':'768P｜H3原生档位','native':'768P'},
     {'value':'2K','label':'2K｜H3原生档位','native':'2K'},
     {'value':'720p','label':'720P成片｜按768P生成并本地缩小','native':'768P'},
     {'value':'1080p','label':'1080P成片｜按2K生成并本地缩小','native':'2K'},
     {'value':'480p','label':'480P成片｜按768P生成并本地缩小','native':'768P'}],
}

def resolution_options(model):
    m=canonical(model)
    if m not in RESOLUTION_MENUS:
        raise RouteError('当前模型的清晰度尚未核实，不可按已支持规格展示')
    return {'model':m,'options':[dict(x) for x in RESOLUTION_MENUS[m]],
            'native_levels':sorted({x['native'] for x in RESOLUTION_MENUS[m]}),
            'note':('H3只有768P和2K原生生成规格；720P/1080P等属于本地转码，仍按对应原生档位计费。'
                    if m==H3 else '仅表示本地接口适配支持的选项，具体账户支持及实际费用仍需预检。')}

def resolution_menus():
    return {m:resolution_options(m) for m in RESOLUTION_MENUS}

def _media_urls(specs,public_base):
    items=[]
    for item in specs:
        asset=item.get('asset') or {}
        u=openlux._asset_url(asset,public_base)
        if not u:raise openlux.ApiError('参考素材没有可被 OpenLux 访问的 HTTPS 链接，请先配置 OSS 或公网素材通道。')
        if not u.startswith('https://'):raise openlux.ApiError('参考素材必须使用 HTTPS 链接。')
        items.append((asset.get('kind'),item.get('role'),u))
    return items

def build(model,prompt,specs,ratio,resolution,duration,audio,public_base):
    m=canonical(model); media=_media_urls(specs,public_base)
    check_mode=_infer_mode(media)
    # A single role=reference_image is the H3 image-reference workflow, not a first frame.
    validate(m,check_mode,ratio,resolution,duration,[{'kind':kind} for kind,_,_ in media])
    if m==WAN:
        # This is a DRY-RUN mapping only. Other gateways disagree about paths/fields.
        # Do not send this until OpenLux publishes a verified provider contract.
        return None,None,{'model':WAN,'prompt':prompt,'duration':int(duration),
              'resolution':resolution.upper(),'aspect_ratio':ratio,'references':[{'kind':k,'role':r,'url':url} for k,r,url in media]}
    if m==VIDU:
        images=[u for kind,role,u in sorted(media,key=lambda item: {'first_frame':0,'last_frame':1}.get(item[1],2)) if kind=='image']
        path=('/ent/v2/text2video' if not images else '/ent/v2/img2video' if len(images)==1 else '/ent/v2/start-end2video')
        data={'model':VIDU,'prompt':prompt,'duration':int(duration),'resolution':resolution,'audio':bool(audio)}
        if images:data['images']=images
        if ratio!='adaptive' and not images:data['aspect_ratio']=ratio
        return path,VIDU_QUERY,data
    if m==H3:
        content=[{'type':'text','text':prompt}]
        for kind,role,url in media:
            if kind not in ('image','video','audio'):raise RouteError('H3 未识别的参考素材类型')
            role=role or ('reference_'+kind)
            typ={'image':'image_url','video':'video_url','audio':'audio_url'}[kind]
            entry={'type':typ,typ:{'url':url},'role':role}
            content.append(entry)
        if any(x.get('role','').startswith('reference_') for x in content) and any(x.get('role') in ('first_frame','last_frame') for x in content):
            raise RouteError('H3 不能在一个请求中同时混用首尾帧和普通参考素材。')
        data={'model':'MiniMax-H3','content':content,'duration':int(duration),'resolution':h3_resolution_plan(resolution)['provider_resolution']}
        if not media:data['ratio']=ratio if ratio!='adaptive' else '9:16'
        return H3_CREATE,H3_QUERY,data
    raise RouteError('Seedance 使用原有接口。')

def _infer_mode(media):
    roles={role for _,role,_ in media}; kinds=[kind for kind,_,_ in media]
    if not media:return 'text'
    if 'last_frame' in roles:return 'first_last'
    if 'first_frame' in roles:return 'first_frame'
    return 'reference'

def route(model,selected_mode,ratio,resolution,duration,media):
    """Validate current UI mode before issuing a no-charge preview token."""
    m=canonical(model)
    if m!=SEED:validate(m,selected_mode,ratio,resolution,duration,media)
    if m==WAN:
        raise RouteError('Wan 3.0 的型号和参数已建立校验，但 OpenLux 尚未公开可验证的 Wan 专用创建/查询路径，本次不能生成付费预览令牌。请先使用免费模型目录检查。')
    return capabilities(m)

def create(model,prompt,specs,ratio,resolution,duration,audio,public_base,idempotency_key=None):
    path,query,data=build(model,prompt,specs,ratio,resolution,duration,audio,public_base)
    if canonical(model)==WAN:
        raise RouteError('Wan 3.0 目前在 OpenLux 价格目录可见，但专用创建/查询接口未经官方文档验证，且存在临时维护报道；本次绝不提交付费生成。请先免费核对账户模型权限和官方路由。')
    # Do not retry POST on timeout: provider may already have accepted and charged.
    resp=openlux.request_json('POST',path,data,timeout=max(120,int(settings.get_all().get('http_timeout',120))),retries=0,idempotency_key=idempotency_key)
    if not isinstance(resp,dict):raise openlux.ApiError('视频平台返回数据格式不正确，禁止重复提交。')
    if isinstance(resp.get('base_resp'),dict) and int(resp['base_resp'].get('status_code') or 0)!=0:
        raise openlux.ApiError('视频供应商业务错误：'+str(resp['base_resp'])[:250],400,str(resp),False)
    if resp.get('code') not in (None,0,'0',200,'200'):
        raise openlux.ApiError('视频供应商返回错误：'+str(resp.get('message') or resp.get('code')),400,str(resp),False)
    return resp,{'_create_path':path,'_query_path':query,'_request_model':data['model'],'_provider_adapter':canonical(model),'_request_provider':None}

def query(task_id,meta):
    path=meta.get('_query_path')
    if not path:raise RouteError('历史任务缺少实际查询路径')
    path=path.replace('{task_id}',urllib.parse.quote(str(task_id),safe=''))
    return openlux.request_json('GET',path,timeout=30,retries=1)

def status(resp):
    if isinstance(resp,dict) and isinstance(resp.get('task'),dict):
        t=resp['task'];return openlux.extract_status(t)
    return openlux.extract_status(resp)

def result_url(resp):
    # Vidu's image covers are never substituted for video files.
    if isinstance(resp,dict):
        nodes=[resp,resp.get('data') or {}]
        for node in nodes:
            if isinstance(node,dict):
                for x in node.get('creations') or []:
                    if isinstance(x,dict) and x.get('url'):return x['url']
                content=node.get('content') or {}
                if isinstance(content,dict):
                    u=content.get('url') or content.get('video_url')
                    if isinstance(u,str) and u.startswith('https://'):return u
    # MiniMax v2 nests the completion under task.content.url.
    if isinstance(resp,dict) and isinstance(resp.get('task'),dict):
        t=resp['task']; content=t.get('content') or {}
        if isinstance(content,dict) and isinstance(content.get('url'),str):return content['url']
    return openlux.extract_result_url(resp)


def model_diagnostic(model, *, list_fn=None):
    """Read-only model availability; does not promise the native POST route works."""
    m=canonical(model)
    if m==SEED:
        return {'selected':m,'available':True,'provider':'Seedance','schema':'legacy','paid_calls':0}
    rows=list_fn() if list_fn else openlux.list_models()
    ids={str(x).lower() for x in rows}
    aliases=({VIDU} if m==VIDU else {WAN,'wan-3.0'} if m==WAN else {H3,'minimax-h3','minimax-h3-max'})
    matched=sorted(ids & aliases)
    return {'selected':m,'available':bool(matched),'matched_ids':matched,'provider':capabilities(m)['provider'],
      'schema':('Wan 3.0 仅模型目录与参数范围已核对；OpenLux 付费创建/查询路径尚未确认' if m==WAN else 'Vidu 官方接口格式' if m==VIDU else 'MiniMax H3 V2 规范，OpenLux 原生转发地址尚需实测'),
      'paid_calls':0,'note':('当前密钥的 /v1/models 未找到所选模型，已禁止自动付费提交。' if not matched else
       '模型仅出现在账户目录；Wan 3.0 路由尚未证实，严禁付费提交。' if m==WAN else '型号存在于模型目录；仍须确认当前 OpenLux 转发通道接受对应原生参数。')}
