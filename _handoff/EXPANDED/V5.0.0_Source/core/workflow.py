from __future__ import annotations
from .db import cache_get, cache_set
from . import hypit_engine

CACHE_SCHEMA = 'hypit_v5_smart_tracking_v393'


def _cache_valid(wf: dict, expected_sha: str) -> bool:
    if not isinstance(wf, dict):
        return False
    src = wf.get('source') or {}
    if src.get('sha256') != expected_sha:
        return False
    # Never reuse a workflow created while media probing was broken.
    try:
        if float(src.get('duration') or 0) <= 0:
            return False
    except Exception:
        return False
    if not src.get('width') or not src.get('height'):
        return False
    footage = ((wf.get('tracks') or {}).get('footage') or wf.get('segments') or [])
    if not footage:
        return False
    analysis = wf.get('analysis') or {}
    seg = analysis.get('segmentation') or {}
    tr = analysis.get('transcript') or {}
    try:
        dur = float(src.get('duration') or 0)
    except Exception:
        dur = 0
    beat_count = int(seg.get('beat_count') or len(footage) or 0)
    cuts = int(((analysis.get('scene_detection') or {}).get('cut_count')) or 0)
    word_cov = float(analysis.get('word_anchor_coverage') or 0)
    if dur > 7 and cuts == 0 and (tr.get('used') or word_cov > 0) and beat_count <= 1:
        return False
    gate_reason = str(((analysis.get('paid_test_gate') or {}).get('reason')) or '')
    if dur > 0 and src.get('width') and src.get('height') and 'FFmpeg' in gate_reason and beat_count <= 1:
        return False
    return True




def _execution_blueprint(wf: dict, product_assets: list[dict], instruction: str) -> dict:
    analysis=wf.get('analysis') or {}
    scene=analysis.get('scene_detection') or {}
    seg_info=analysis.get('segmentation') or {}
    footage=((wf.get('tracks') or {}).get('footage') or wf.get('segments') or [])
    duration=float((wf.get('source') or {}).get('duration') or wf.get('duration') or 0)
    cuts=int(scene.get('cut_count') or 0)
    beats=int(seg_info.get('beat_count') or len(footage) or 0)
    evidence=float(analysis.get('evidence_score') or 0)
    word_cov=float(analysis.get('word_anchor_coverage') or 0)
    smart_tracking=analysis.get('smart_tracking') or {}
    pose_tracking=smart_tracking.get('pose') or {}
    object_tracking=smart_tracking.get('object_tracking') or {}
    mode=seg_info.get('mode') or 'unknown'
    product_count=len([x for x in product_assets if x])

    if cuts == 0 and mode in ('speech_beats','time_beats','single_shot'):
        continuity='连续单镜头'
    elif cuts <= 3:
        continuity='低切换连续结构'
    elif cuts <= 8:
        continuity='中等切换结构'
    else:
        continuity='高密度切换结构'

    # V3.1 only chooses among one-call strategies that the current provider path can actually execute.
    # It does not pretend to segment/render/stitch unless that pipeline is really implemented.
    if duration <= 30 and cuts <= 3:
        strategy='整条参考复刻'
        strategy_code='whole_reference'
        reason='原视频时长在当前 Seedance 范围内，且连续性较高；优先整条生成以保持人物、产品和动作衔接。'
    elif duration <= 30 and cuts <= 8:
        strategy='整条参考复刻 + 强时间轴约束'
        strategy_code='whole_reference_timeline'
        reason='原视频存在多个真实切换或动作节拍，但仍可在单次生成范围内；保留一次调用，同时把时间轴约束写入生成提示。'
    else:
        strategy='整条参考复刻 + 密集结构约束'
        strategy_code='whole_reference_dense'
        reason='当前正式链路仍采用一次 Seedance 创建请求；对于高密度结构，V3.1 会强化镜头顺序、动作节拍和连续性约束，不虚构未实现的自动分段拼接。'

    preserve=[
        '动作发生顺序', '核心节奏与时长关系', '主体进入/离开画面的逻辑',
        '关键产品交互动作', '原视频镜头运动与构图变化趋势', '证明结果出现的先后关系'
    ]
    replace=[
        '严格执行用户提示词中明确要求替换的人物、产品、场景、语言或视觉元素',
        '产品图存在时优先锁定产品外观、比例、颜色和关键结构',
        '原品牌 Logo、水印、高识别度文案默认原创化处理'
    ]
    timeline=[]
    for i,s in enumerate(footage):
        role=(s.get('role_candidate') or {}).get('label') or '结构段'
        conf=(s.get('role_candidate') or {}).get('confidence')
        timeline.append({
            'index':i+1,'id':s.get('id'),'start':s.get('start'),'end':s.get('end'),'duration':s.get('duration'),
            'role':role,'role_confidence':conf,'speech_text':(s.get('speech_text') or '').strip(),
            'ocr_text':(s.get('ocr_text') or '').strip(),'anchor':s.get('anchor'),
            'evidence':list(s.get('source_evidence') or []),
            'continuity_rule':'保持本段结束状态能够自然连接下一段，不允许产品/人物无原因瞬移、消失或形态突变。'
        })
    risks=[]
    if evidence < .70:risks.append('本地证据完整度偏低，建议人工检查关键帧与动作顺序后再付费生成。')
    if cuts > 8:risks.append('真实切镜较密集；单次生成更容易出现节奏偏差，需要优先检查最终结果的结构一致性。')
    if word_cov < .5 and ((analysis.get('transcript') or {}).get('used')):risks.append('逐词锚点覆盖不完整；口播时序只作为辅助约束。')
    if product_count == 0:risks.append('未上传产品参考图；若提示词要求替换产品，产品外观稳定性会低于有产品图时。')
    if pose_tracking.get('available') and not pose_tracking.get('used'):
        risks.append('MediaPipe 已可用但本次姿态证据未形成；人物/手部连续性主要依赖原视频与提示词。')
    if object_tracking.get('available') and not object_tracking.get('used') and not object_tracking.get('model_ready',True):
        risks.append('YOLO + ByteTrack 已安装，但本地 YOLO 模型权重尚未准备；本次未执行通用目标追踪。')
    if not risks:risks.append('当前没有检测到需要阻断生成的结构风险。')
    return {
        'schema':'seedance_execution_blueprint_v1',
        'goal':(instruction or '').strip() or '保留原爆款结构，仅做必要原创化替换',
        'source_profile':{'duration':duration,'hard_cut_count':cuts,'beat_count':beats,'segmentation_mode':mode,'continuity_profile':continuity,'evidence_score':evidence,'word_anchor_coverage':word_cov,
                          'pose_tracking_used':bool(pose_tracking.get('used')),'object_tracking_used':bool(object_tracking.get('used'))},
        'generation_strategy':{'code':strategy_code,'label':strategy,'paid_video_create_calls':1,'reason':reason,'automatic_segment_stitching':False},
        'preserve_contract':preserve,'replacement_contract':replace,'timeline':timeline,'risk_flags':risks,
        'quality_targets':['动作顺序一致','人物/产品连续性','用户指定替换准确','时长与画幅继承正确','最终结果为连续视频而不是故事板/九宫格']
    }


def refresh_execution_blueprint(wf: dict, product_assets: list[dict], instruction: str) -> dict:
    wf['instruction']=(instruction or '').strip()
    wf['product_count']=len([x for x in product_assets if x])
    wf['product_asset_ids']=[x.get('id') for x in product_assets if x]
    wf['execution_blueprint']=_execution_blueprint(wf,product_assets,instruction)
    return wf


def build_workflow(reference_asset: dict, product_assets: list[dict], instruction: str, settings:dict|None=None) -> dict:
    """Build a Hypit-inspired evidence-driven editable workflow locally.

    Invalid historical caches (duration=0 / no dimensions / no footage) are ignored.
    This prevents a previously failed probe from poisoning every later re-analysis of
    the same video after FFmpeg has been fixed.
    """
    settings=settings or {}
    key=(
        f'{CACHE_SCHEMA}:'+reference_asset['sha256']+
        f":{settings.get('replica_scene_threshold',.30)}:{settings.get('replica_max_scenes',24)}:"+
        f"{settings.get('replica_transcript_mode','if_installed')}:{settings.get('replica_ocr_mode','if_installed')}"
    )
    cached=cache_get(key)
    if _cache_valid(cached, reference_asset.get('sha256')):
        wf=cached
        wf['instruction']=(instruction or '').strip()
    else:
        wf=hypit_engine.analyze(reference_asset,instruction,settings)
        # Cache only structurally valid media analyses. Failed probes should be retried
        # automatically next time instead of becoming permanent poison caches.
        if _cache_valid(wf, reference_asset.get('sha256')):
            cache_set(key,wf)
    return refresh_execution_blueprint(wf,product_assets,instruction)


def _anchor_text(anchor):
    if not isinstance(anchor,dict):return ''
    if anchor.get('type')=='word':
        sw=anchor.get('start_word') or {}; ew=anchor.get('end_word') or {}
        return f"词锚点 {sw.get('id','')}({sw.get('word','')}) → {ew.get('id','')}({ew.get('word','')})"
    return f"{float(anchor.get('start') or 0):.2f}-{float(anchor.get('end') or 0):.2f}s"


def workflow_prompt(workflow: dict) -> str:
    tracks=(workflow.get('tracks') or {})
    footage=tracks.get('footage') or workflow.get('segments') or []
    bp=workflow.get('execution_blueprint') or {}
    strat=bp.get('generation_strategy') or {}
    lines=[]
    for i,s in enumerate(footage):
        role=(s.get('role_candidate') or {}).get('label') or s.get('role') or '结构段'
        speech=(s.get('speech_text') or '').strip(); text=(s.get('ocr_text') or '').strip()
        st=float(s.get('start') or 0); ed=float(s.get('end') or 0)
        details=[]
        if speech:details.append('口播：'+speech[:150])
        if text:details.append('画面文字：'+text[:100])
        lines.append(f"{i+1}. {st:.2f}-{ed:.2f}s｜{role}"+('｜'+'；'.join(details) if details else ''))
    inst=workflow.get('instruction') or bp.get('goal') or '保持原爆款结构，仅做必要原创化替换'
    preserve='；'.join((bp.get('preserve_contract') or [])[:6])
    replace='；'.join((bp.get('replacement_contract') or [])[:4])
    strategy=strat.get('label') or '整条参考复刻'
    smart=((workflow.get('analysis') or {}).get('smart_tracking') or {})
    pose=smart.get('pose') or {}; obj=smart.get('object_tracking') or {}
    tracking_lines=[]
    if pose.get('used'):
        tracking_lines.append(f"人物/手部姿态：人物覆盖{float(pose.get('person_presence_ratio') or 0):.0%}，手部覆盖{float(pose.get('hand_presence_ratio') or 0):.0%}，保持手部与产品交互动作连续。")
    if obj.get('used'):
        counts=obj.get('class_track_counts') or {}
        txt='、'.join(f'{k}×{v}' for k,v in list(counts.items())[:8])
        if txt:tracking_lines.append('通用目标追踪：'+txt+'；同一目标在相邻时间段不得无原因瞬移、消失或形态突变。')
    tracking_text=('\n【本地连续性追踪证据】\n'+'\n'.join(tracking_lines)) if tracking_lines else ''
    return (
        '最终目标：严格按照用户提示词完成一条可直接使用的完整视频。不要输出分镜图、九宫格、故事板、PPT 或多张静态图。\n'
        '用户要求：'+inst+'\n'
        '执行策略：'+strategy+'。当前正式链路采用 1 次视频创建请求，优先保证连续性。\n\n'
        '【最高优先级规则】\n'
        '1. 用户提示词明确要求修改的内容必须修改；未明确要求修改的结构尽量继承参考视频。\n'
        '2. 参考视频负责动作顺序、节奏、镜头关系和结果证明逻辑；产品参考图负责锁定产品外观。\n'
        '3. 保持连续性：人物身份、衣着、产品外观、产品位置、手部持有关系、场景状态不得无原因跳变。\n'
        '4. 如果参考视频与用户替换要求冲突，只替换冲突元素，不要连带重写其他动作和镜头结构。\n'
        '5. 输出必须是单条连续真实视频。\n\n'
        '【必须尽量保留】'+preserve+'\n'
        '【允许/要求替换】'+replace+'\n\n'
        '【时间轴执行蓝图】\n'+'\n'.join(lines[:30])+tracking_text
    )
