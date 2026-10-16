from __future__ import annotations

ALLOWED_RATIOS = {'16:9','9:16','1:1','4:3','3:4','21:9','adaptive'}
ALLOWED_RESOLUTIONS = {'480p','540p','720p','768P','1080p','2K'}

MODE_LABELS = {
    'auto': '智能参考（自动选择）',
    'text': '文生视频',
    'image_reference': '图片参考生视频',
    'first_frame': '首帧图生视频',
    'first_last': '首尾帧生视频',
    'reference': '全能参考',
    'edit': '视频编辑',
    'extend': '视频延长',
}


def _first(assets, kind):
    return next((a for a in assets if a and a.get('kind') == kind), None)


def _all(assets, kind):
    return [a for a in assets if a and a.get('kind') == kind]


def normalize_mode(mode: str) -> str:
    mode = (mode or 'text').strip()
    if mode == 'auto':
        mode = 'text'
    if mode not in MODE_LABELS:
        raise ValueError('不支持的视频模式')
    return mode


def prepare(mode: str, assets: list[dict], ratio: str, duration: int, prompt: str, frame_roles: dict|None=None) -> dict:
    """Return validated assets/roles and Seedance-2.5 parameter locks.

    This only prepares a request. It never calls a paid API.
    """
    mode = normalize_mode(mode)
    assets = [a for a in assets if a]
    prompt = (prompt or '').strip()
    ratio = ratio if ratio in ALLOWED_RATIOS else '9:16'
    duration = int(duration)
    warnings = []
    frame_roles = frame_roles or {}
    specs: list[dict] = []
    api_ratio = ratio
    api_duration = duration
    output_format = 'mp4'
    billing_duration = duration

    if mode == 'text':
        assets = []
    elif mode == 'image_reference':
        imgs = _all(assets, 'image')
        if not imgs:
            raise ValueError('图片参考生视频至少需要 1 张图片。')
        specs = [{'asset': a, 'role': 'reference_image'} for a in imgs]
    elif mode == 'first_frame':
        imgs = _all(assets, 'image')
        if not imgs:
            raise ValueError('首帧图生视频至少需要 1 张图片。')
        first_id=frame_roles.get('first_asset_id') or imgs[0].get('id')
        for a in assets:
            role={'image':'reference_image','video':'reference_video','audio':'reference_audio'}.get(a.get('kind'))
            if a.get('kind')=='image' and a.get('id')==first_id:role='first_frame'
            if role:specs.append({'asset':a,'role':role})
        api_ratio = 'adaptive'
        warnings.append('首帧模式使用 first_frame role 锁定首帧；其它素材继续作为普通参考，素材编号保持与上传顺序一致。')
    elif mode == 'first_last':
        imgs = _all(assets, 'image')
        if len(imgs) < 2:
            raise ValueError('首尾帧生视频至少需要 2 张图片。')
        first_id=frame_roles.get('first_asset_id') or imgs[0].get('id')
        last_id=frame_roles.get('last_asset_id') or imgs[1].get('id')
        if first_id==last_id:raise ValueError('首帧和尾帧不能使用同一张图片。')
        for a in assets:
            role={'image':'reference_image','video':'reference_video','audio':'reference_audio'}.get(a.get('kind'))
            if a.get('kind')=='image' and a.get('id')==first_id:role='first_frame'
            elif a.get('kind')=='image' and a.get('id')==last_id:role='last_frame'
            if role:specs.append({'asset':a,'role':role})
        api_ratio = 'adaptive'
        warnings.append('首尾帧通过 first_frame / last_frame role 严格绑定；其它素材继续作为普通参考，素材编号保持与上传顺序一致。')
    elif mode == 'reference':
        if not assets:
            raise ValueError('全能参考模式至少需要 1 个图片、视频或音频参考素材。')
        for a in assets:
            role = {'image':'reference_image','video':'reference_video','audio':'reference_audio'}.get(a.get('kind'))
            if role:
                specs.append({'asset': a, 'role': role})
    elif mode == 'edit':
        videos = _all(assets, 'video')
        if not videos:
            raise ValueError('视频编辑模式至少需要 1 段待编辑视频。')
        # Keep all supported references; prompt decides which video is edited when multiple videos exist.
        for a in assets:
            role = {'image':'reference_image','video':'reference_video','audio':'reference_audio'}.get(a.get('kind'))
            if role:
                specs.append({'asset': a, 'role': role})
        api_ratio = 'adaptive'
        api_duration = -1
        output_format = 'mov'
        src_dur = next((float(v.get('duration')) for v in videos if v.get('duration')), None)
        billing_duration = int(round(src_dur)) if src_dur else max(4, duration)
        warnings.append('视频编辑自动锁定 ratio=adaptive、duration=-1，以继承待编辑视频的画幅与时长。')
        if not any(k in prompt for k in ('编辑','增加','加上','删除','去掉','修改','替换','改成')):
            warnings.append('建议在提示词中明确写“编辑/增加/删除/修改/替换”等动作词，减少模型把任务误判为普通生成。')
    elif mode == 'extend':
        videos = _all(assets, 'video')
        if not videos:
            raise ValueError('视频延长模式至少需要 1 段原视频。')
        for a in assets:
            role = {'image':'reference_image','video':'reference_video','audio':'reference_audio'}.get(a.get('kind'))
            if role:
                specs.append({'asset': a, 'role': role})
        api_ratio = 'adaptive'
        output_format = 'mov'
        warnings.append('视频延长自动使用 adaptive（自适应比例）；为提高声画衔接稳定性，请优先使用明确的“向前/向后延长、续写”指令。')
        if not any(k in prompt for k in ('向前延长','向后延长','延长','延续','续写')):
            warnings.append('当前提示词未检测到“延长/延续/续写”关键词，建议补充以明确触发视频延长能力。')

    if mode != 'edit' and not (4 <= duration <= 30):
        raise ValueError('Seedance 2.5 生成时长请选择 4–30 秒。')

    # Reference count guardrails based on Seedance 2.5 documented input limits.
    images = sum(1 for s in specs if s['asset'].get('kind') == 'image')
    videos = sum(1 for s in specs if s['asset'].get('kind') == 'video')
    audios = sum(1 for s in specs if s['asset'].get('kind') == 'audio')
    if images > 30 or videos > 10 or audios > 10 or len(specs) > 50:
        raise ValueError('参考素材超过 Seedance 2.5 上限：图片≤30、视频≤10、音频≤10、总素材≤50。')
    video_seconds=sum(float(s['asset'].get('duration') or 0) for s in specs if s['asset'].get('kind')=='video')
    audio_seconds=sum(float(s['asset'].get('duration') or 0) for s in specs if s['asset'].get('kind')=='audio')
    if video_seconds > 30.5:
        raise ValueError(f'参考视频总时长约 {video_seconds:.1f}s，超过 Seedance 2.5 当前参考视频总时长 30s 上限。请先裁剪素材。')
    if audio_seconds > 30.5:
        raise ValueError(f'参考音频总时长约 {audio_seconds:.1f}s，超过 Seedance 2.5 当前参考音频总时长 30s 上限。请先裁剪素材。')
    if mode=='edit' and video_seconds>20:
        warnings.append('当前待编辑视频超过 20s；Seedance 2.5 官方建议编辑视频尽量控制在 20s 内以提高稳定性。')

    return {
        'mode': mode,
        'label': MODE_LABELS[mode],
        'specs': specs,
        'ratio': api_ratio,
        'duration': api_duration,
        'requested_duration': duration,
        'billing_duration': billing_duration,
        'output_format': output_format,
        'warnings': warnings,
    }
