"""Evidence-gated video routing for US TikTok product production.

This module has NO network side effects and NEVER pays for generation.
Every provider is required to pass the existing video_adapters.route gate.
A public catalogue listing cannot upgrade a candidate to operational readiness.
"""
from __future__ import annotations
import datetime as dt
import json
import math
from . import video_adapters

# Observed starting prices are USER-REPORTED DIRECTORY REFERENCES, not account quotes.
# Unknown surcharges/segment tokens are never silently estimated as exact cost.
CATALOG = {
  'doubao-seedance-2-5-260628':{'name':'Seedance 2.5','kind':'per_million_tokens','from_price':6.955,'state':'adapter_available','tier':'complex'},
  'doubao-seedance-2-0':{'name':'Seedance 2.0','kind':'per_million_tokens','from_price':4.55,'state':'catalog_only'},
  'doubao-seedance-2-0-fast':{'name':'Seedance 2.0 Fast','kind':'per_million_tokens','from_price':3.64,'state':'catalog_only'},
  'doubao-seedance-2-0-mini':{'name':'Seedance 2.0 Mini','kind':'per_million_tokens','from_price':2.275,'state':'catalog_only'},
  'wan3.0-video':{'name':'Wan 3.0','kind':'per_second','from_price':0.035,'state':'adapter_missing'},
  'viduq3-turbo':{'name':'Vidu Q3 Turbo','kind':'per_second','from_price':0.0221,'state':'adapter_available_account_untested'},
  'viduq3-pro':{'name':'Vidu Q3 Pro','kind':'per_second','from_price':0.0386,'state':'catalog_only'},
  'minimax-video-h3':{'name':'MiniMax H3','kind':'per_second','from_price':0.06,'state':'adapter_available_account_untested'},
  'kling-motion-control':{'name':'Kling Motion Control','kind':'per_second','from_price':0.0625,'state':'adapter_missing'},
  'happyhorse-1.1-r2v':{'name':'HappyHorse 1.1 R2V','kind':'per_second','from_price':0.086,'state':'adapter_missing'},
  'pixverse-mimic':{'name':'PixVerse Mimic','kind':'per_request','from_price':0.00302,'state':'adapter_missing'},
}
SEED=video_adapters.SEED
VIDU=video_adapters.VIDU
H3=video_adapters.H3
ACTIVE=(SEED,VIDU,H3)
ROUTE_MODES=('auto','fast','cheap')
TASK_TYPES=('auto','creative','bulk','hq','motion','multi_image','edit','replica','extend')
# Only these three adapters are presented as ordinary-user routes. Others remain in catalog.
COMMON_MODELS=(SEED,VIDU,H3)
MOTION_FIDELITIES=('approx','strict','background')
_STRICT_CUES=('唯一舞蹈动作','唯一动作','完全一致','逐帧','100%还原','一模一样','精准复制','精确还原',
              '严格保持','必须一致','原背景不变','背景完全不变','不得改变背景','原视频的全部动作',
              '唯一参考','保留原照片','背景保持不变','不得修改背景','动作完全一致','exact motion','exactly replicate','pixel perfect','identical moves')
_MOTION_CUES=('舞蹈','跳舞','动作模仿','动作迁移','动作参考','motion transfer','pose transfer','dance mimic','dance','choreography')


def motion_requirement(context):
    """Describe conflicting constraints, never invent a guaranteed imitation ability."""
    prompt=str(context.get('prompt') or '').lower()
    has_video=int(context.get('videos') or 0)>0
    has_motion=has_video and any(t in prompt for t in _MOTION_CUES)
    strict_terms=[term for term in _STRICT_CUES if term in prompt] if has_motion else []
    return {'reference_action':has_motion,'strict_markers':strict_terms,
            'requested_fidelity':str(context.get('motion_fidelity') or 'approx').strip(),
            'approx_confirmed':context.get('approx_confirmed') is True}

def intent(context):
    """Deterministic primary intent; no unsupported AI inference or invented claims."""
    t=str(context.get('task_type') or 'auto').strip()
    if t not in TASK_TYPES:raise ValueError('未知视频任务类型')
    if t!='auto':return t
    mode=context.get('mode') or 'text'
    prompt=str(context.get('prompt') or '').lower()
    if mode=='extend':return 'extend'
    if mode=='edit':return 'edit'
    if context.get('replica'):return 'replica'
    if mode=='reference' and context.get('videos',0)>0:
        # Generic dance/motion references are NOT automatically strict motion-transfer jobs.
        # Exact reproduction is an explicit user decision and separate provider capability.
        return 'replica'
    if context.get('images',0)>=3:return 'multi_image'
    if context.get('duration',0)>=15 or context.get('videos',0)>0:return 'creative'
    if context.get('resolution','').lower() in ('2k','4k'):return 'hq'
    return 'bulk'


def catalog():
    return [{'id':k,**dict(v)} for k,v in CATALOG.items()]


def _capability_fit(model,ctx):
    # Hard-gate before scoring (never silently drop requested media).
    rawmode=ctx.get('mode') or 'text'
    mode='first_frame' if rawmode=='image_reference' and ctx.get('images')==1 and ctx.get('videos')==0 and ctx.get('audios')==0 and model==VIDU else rawmode
    image_count=int(ctx.get('images') or 0);video_count=int(ctx.get('videos') or 0);audio_count=int(ctx.get('audios') or 0)
    duration=float(ctx.get('duration') or 0);resolution=str(ctx.get('resolution') or '720p')
    ratio=str(ctx.get('ratio') or '9:16')
    if ctx.get('task_type')=='motion':return (False,'需要动作迁移专用适配器；Kling Motion Control/PixVerse Mimic 未完成可靠的 OpenLux 实测接入',mode)
    if ctx.get('task_type')=='hq' and resolution.lower() not in ('1080p','2k','4k'):return (False,'高清任务需要明确选择1080p或2K，不自动改变费用规格',mode)
    if model not in ACTIVE:return (False,'当前模型没有已核验的 OpenLux 生成适配器',mode)
    if model==SEED:
        if not 1<=duration<=30:return (False,'Seedance 单任务时长超出可处理范围',mode)
        if ctx.get('task_type')=='multi_image' and ctx.get('images',0)<2:return (False,'多图参考模式需要多张原始图片',mode)
        return (True,'沿用原有多素材和编辑链路',mode)
    if audio_count:return (False,'当前适配器不支持独立音频参考素材；模型生成新音频与保留上传的原音轨是不同功能',mode)
    if model==VIDU:
        # The official Vidu Q3 Turbo text/image/first-last endpoints expose
        # audio=true. This does NOT establish that video-reference transfer or
        # exact reuse of the source soundtrack works through OpenLux.
        if mode not in ('text','first_frame','first_last'):
            return (False,'Vidu 当前适配器不支持参考视频/动作迁移/编辑',mode)
        expected={'text':0,'first_frame':1,'first_last':2}[mode]
        if image_count!=expected or video_count:return (False,'图片数量或素材类型不匹配',mode)
        if resolution not in ('540p','720p','1080p'):return (False,'分辨率不兼容',mode)
        if not 1<=duration<=16:return (False,'生成时长超出Vidu现有适配范围',mode)
        if ratio not in ('9:16','16:9','1:1','adaptive'):return (False,'画幅不兼容',mode)
        return (True,'原生视频接口已编写，真实账户需验证',mode)
    if model==H3:
        if ctx.get('native_audio'):
            return (False,'MiniMax H3 原厂支持原生音频，但 OpenLux 当前转发适配的音频能力尚未经过账户验证，不能自动保证',mode)
        if mode not in ('text','first_frame','first_last','reference','image_reference'):
            return (False,'H3 当前适配器不能直接编辑或延长视频',mode)
        if not 4<=duration<=15:return (False,'时长须为4至15秒',mode)
        try:
            plan=video_adapters.h3_resolution_plan(resolution)
        except video_adapters.RouteError as exc:
            return (False,str(exc),mode)
        if mode=='text' and (image_count or video_count):return (False,'文生模式附加了不兼容素材',mode)
        if mode=='first_frame' and (image_count!=1 or video_count):return (False,'首帧素材数量不匹配',mode)
        if mode=='first_last' and (image_count!=2 or video_count):return (False,'首尾帧素材数量不匹配',mode)
        if mode=='image_reference' and image_count==0:return (False,'需要图片参考',mode)
        return (True,'H3接口适配已编写；'+plan['explanation']+' OpenLux账户路由仍需实测。',mode)
    return (False,'未核实',mode)


def historical_metrics(model,rows,context=None):
    """Comparable, reviewed attempts only; completion alone is not usability."""
    from .video_evaluation import case_key
    key=case_key(context or {})
    outcomes=[r for r in rows if key and r.get('case_key')==key and r.get('model')==model]
    complete_reviews=all(r.get('status') in ('completed','failed') and (r.get('status')=='failed' or type(r.get('quality_accepted')) is bool) for r in outcomes)
    if len(outcomes)<5 or not complete_reviews:return {'count':len(outcomes),'enough_evidence':False,'success_rate':None,'average_seconds':None,'cost_per_usable_video_usd':None}
    successes=[r for r in outcomes if r.get('status')=='completed' and r.get('has_downloadable_video') is True]
    usable=[r for r in successes if r.get('quality_accepted') is True and r.get('technical_status') in ('passed','warning')]
    valid=lambda v:type(v) in (int,float) and math.isfinite(v) and v>=0
    durations=[float(r['elapsed_seconds']) for r in outcomes if valid(r.get('elapsed_seconds'))]
    costs=[float(r['actual_cost_usd']) for r in outcomes if valid(r.get('actual_cost_usd'))]
    full_costs=len(costs)==len(outcomes)
    return {'count':len(outcomes),'enough_evidence':True,'success_rate':len(usable)/len(outcomes),
            'average_seconds':sum(durations)/len(durations) if len(durations)==len(outcomes) else None,
            'cost_per_usable_video_usd':sum(costs)/len(usable) if full_costs and usable else None}


def quote(model,context):
    meta=CATALOG[model];duration=float(context.get('duration') or 0)
    lower=round(meta['from_price']*duration,6) if meta['kind']=='per_second' and duration>0 else None
    return {'estimated_total_usd':None,'estimated_status':'待验证',
            'reference_lower_bound_usd':lower,'billing_unit':meta['kind'],'catalog_reference_rate':meta['from_price'],
            'additional_charges':'高清、素材、音频及分组倍率未全部核验；仅作目录参考，不是付费预检结果',
            'actual_cost_usd':None}


def choose(context,*,mode='auto',override=None,history=None):
    if mode not in ROUTE_MODES:raise ValueError('只能选择自动、快速或便宜模式')
    fidelity=motion_requirement(context)
    if fidelity['requested_fidelity'] not in MOTION_FIDELITIES:
        raise ValueError('未知动作复刻要求；请选择近似参考、严格迁移或背景保留')
    if fidelity['requested_fidelity']=='strict' or str(context.get('task_type'))=='motion':
        raise ValueError('严格动作迁移需要专用动作控制接口，当前未核实可用。可改选“低成本近似动作参考”，但必须确认接受动作差异；本次未付费。')
    if fidelity['requested_fidelity']=='background':
        raise ValueError('保持原始背景完全不变需要人物分层/跟踪/后期合成工作流，当前不能保证；不能使用普通生成模型伪装精确背景复刻。本次未付费。')
    if fidelity['strict_markers'] and not fidelity['approx_confirmed']:
        raise ValueError('提示词明确要求动作或背景严格一致，但选择了“近似动作参考”。请先勾选“接受动作与背景存在差异”，或者改为严格动作迁移模式；本次未付费。')
    history=history or []
    ctx=dict(context);raw_task=str(ctx.get('task_type') or 'auto');ctx['task_type']=intent(ctx)
    rejected=[];eligible=[]
    for model in ACTIVE:
        ok,why,mapped=_capability_fit(model,ctx)
        if not ok:rejected.append({'model':model,'reason':why});continue
        metrics=historical_metrics(model,history,ctx)
        eligible.append({'model':model,'mode':mapped,'reason':why,'metrics':metrics,'quote':quote(model,ctx)})
    if override and override not in ('auto','default'):
        try:target=video_adapters.canonical(override)
        except video_adapters.RouteError:
            raise ValueError('手动模型没有已实现的生成适配器；不能仅根据目录展示就发起付费任务')
        choice=next((x for x in eligible if x['model']==target),None)
        if choice is None:
            failure=next((x['reason'] for x in rejected if x['model']==target),'没有已核验的对应生成接口')
            display=CATALOG.get(target,{}).get('name',target)
            usable='、'.join(CATALOG.get(x['model'],{}).get('name',x['model']) for x in eligible) or '无'
            raise ValueError(f'当前在高级设置中手动指定了【{display}】，但本次参数不兼容：{failure}。'
                             f'当前可参与自动选择的兼容模型：{usable}。'
                             '可以点击「恢复智能自动路由」后重新预览（只预览，不扣费）；系统不会偷偷忽略参考素材、音频或清晰度要求。')
        reason='用户高级手动指定（仍需通过账户权限与付费预检）'
    else:
        if not eligible:
            detail='动作迁移专用接口（Kling Motion Control / PixVerse Mimic）尚未验证可用；不以普通视频模型替代。' if ctx['task_type']=='motion' else '当前输入素材、时长和输出规格没有已适配的可用模型；不会自动降低规格或丢弃参考素材'
            raise ValueError(detail)
        def key(v):
            m=v['model'];his=v['metrics']
            success=his['success_rate'] if his['enough_evidence'] and his['success_rate'] is not None else None
            speed=his['average_seconds'] if his['enough_evidence'] else None
            usable=his['cost_per_usable_video_usd'] if his['enough_evidence'] else None
            if mode=='fast':return (0 if speed is not None else 1,speed if speed is not None else 0,0 if m==SEED else 1)
            if mode=='cheap':
                return (0 if usable is not None else 1,usable if usable is not None else 0,0 if m==VIDU else 1,0 if m==SEED else 1)
            # Quality-first default: provider with proven rate, then stable complex adapter.
            return (0 if success is not None else 1,-success if success is not None else 0,0 if m==SEED else 1)
        choice=min(eligible,key=key)
        reason=('基于实际完成时间选择' if mode=='fast' and choice['metrics']['average_seconds'] is not None else
                '基于已记录的合格成片成本选择' if mode=='cheap' and choice['metrics']['cost_per_usable_video_usd'] is not None else
                '质量优先：暂无可比实测统计，保守沿用已适配模型' if mode=='auto' else
                '缺少足够实测，使用保守默认规则；不能把模型名称或目录起价当作实测速率/最终成本')
    warnings=[]
    if fidelity['reference_action']:
        warnings.append('仅进行近似动作参考；不保证与原视频动作、人物、音乐或背景完全一致。')
        if fidelity['strict_markers']:
            warnings.append('原提示词包含严格复刻要求，已根据用户明确确认改为近似参考；必须在结果审核中检查差异。')
    if mode=='cheap' and choice['model']==SEED:
        warnings.append('虽然选择便宜模式，但参考视频/音频/规格排除了 Vidu 等低价候选；Seedance 并不代表最低费用，生成前必须预检。')
        reason='兼容性优先：本任务需要视频/多模态参考，低价候选不满足；沿用已适配 Seedance，完整费用待核验。'
    return {'route_version':'39815','task_type':ctx['task_type'],'requested_task_type':raw_task,
            'detected_reference_action':fidelity['reference_action'],
            'motion_fidelity':fidelity['requested_fidelity'],'approx_confirmed':fidelity['approx_confirmed'],
            'warnings':warnings,'routing_mode':mode,
            'selected_model':choice['model'],'selected_video_mode':choice['mode'],
            'selection_reason':reason,'candidate_count':len(eligible),
            'eligible_models':[e['model'] for e in eligible],'excluded_models':rejected,
            'quote':choice['quote'],'metrics':choice['metrics'],
            'manual_override':bool(override and override not in ('auto','default')),
            'selected_at_utc':dt.datetime.now(dt.timezone.utc).isoformat(),
            'note':'自动路由为能力和现有接口预选。未通过账户权限、真实价格和用户单次付费确认，不可提交。'}
