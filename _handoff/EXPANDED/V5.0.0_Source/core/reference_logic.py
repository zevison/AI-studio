from __future__ import annotations
import re

KIND_LABEL={'image':'图片','video':'视频','audio':'音频'}
PROVIDER_KIND={'image':'image','video':'video','audio':'audio'}
_FIRST_WORDS=('首帧','第一帧','开场帧','开头帧')
_LAST_WORDS=('尾帧','最后一帧','结尾帧','结束帧')
_EDIT_WORDS=('编辑','替换','换成','改成','修改','删除','去掉','增加','加上','移除')
_EXTEND_WORDS=('向前延长','向后延长','延长','延续','续写')
_REFERENCE_WORDS=('模仿','参考','动作参考','镜头参考','节奏参考','音色参考','风格参考')
_ALIAS_RE=re.compile(r'@(?P<kind>image|video|audio|图片|视频|音频)\s*(?P<index>\d+)(?!\d)',re.I)
_CANONICAL_ALIAS_RE=re.compile(r'@(?:image|video|audio) \d+(?!\d)')
_CHINESE_TO_KIND={'图片':'image','视频':'video','音频':'audio'}


def normalize_prompt_mentions(prompt:str)->str:
    """Accept legacy Chinese and compact tokens, display @image 1 / @video 1.

    Provider serialization is handled separately and remains @image1/@video1,
    to avoid changing the existing OpenLux/Seedance request contract.
    """
    return _ALIAS_RE.sub(lambda m: f"@{_CHINESE_TO_KIND.get(m.group('kind'),m.group('kind').lower())} {int(m.group('index'))}",prompt or '')


def alias_records(assets:list[dict])->list[dict]:
    """Build user-visible aliases that EXACTLY mirror provider per-type numbering.

    Seedance prompt references such as @image1/@video1 are positional. Therefore aliases
    must be contiguous and derived from the actual content order at submission time.
    """
    counts={'image':0,'video':0,'audio':0}; out=[]
    for i,a in enumerate([x for x in assets if x]):
        kind=a.get('kind')
        if kind not in counts: continue
        counts[kind]+=1
        n=counts[kind]
        out.append({
            'asset':a,'asset_id':a.get('id'),'kind':kind,'upload_index':i,'kind_index':n,
            'alias':f"@{PROVIDER_KIND[kind]} {n}",
            'provider_alias':f"@{PROVIDER_KIND[kind]}{n}",
        })
    return out


def alias_map(records:list[dict])->dict[str,dict]:
    return {r['alias']:r for r in records}


def prompt_aliases(prompt:str)->list[str]:
    seen=[]
    for m in _CANONICAL_ALIAS_RE.findall(normalize_prompt_mentions(prompt)):
        if m not in seen: seen.append(m)
    return seen


def validate_prompt_aliases(prompt:str, records:list[dict]):
    amap=alias_map(records)
    missing=[x for x in prompt_aliases(prompt) if x not in amap]
    if missing:
        raise ValueError('提示词引用了不存在的参考素材：'+ '、'.join(missing) +'。请重新选择素材后再预览；本次不会调用付费接口。')


def provider_prompt(prompt:str, records:list[dict])->str:
    """Accept both legacy and new UI aliases, submit compact provider tokens.

    UI: @image 1 / @video 1 / @audio 1
    API: @image1 / @video1 / @audio1 (existing provider contract)
    """
    normalized=normalize_prompt_mentions(prompt)
    validate_prompt_aliases(normalized,records)
    amap=alias_map(records)
    return _CANONICAL_ALIAS_RE.sub(lambda m: amap[m.group(0)]['provider_alias'],normalized)


def _explicit_role_alias(prompt:str, words:tuple[str,...], records:list[dict])->str|None:
    p=normalize_prompt_mentions(prompt); amap=alias_map(records); role_expr='|'.join(map(re.escape,words))
    p1=re.compile(r'(@image \d+)[^。；;\n]{0,22}?(?:作为|设为|用作|当作|做|为)?\s*(?:'+role_expr+r')')
    m=p1.search(p)
    if m and m.group(1) in amap:return m.group(1)
    p2=re.compile(r'(?:'+role_expr+r')[^。；;\n]{0,22}?(?:使用|用|参考|设为|选择|取|是|为|[:：])?\s*(@image \d+)')
    m=p2.search(p)
    if m and m.group(1) in amap:return m.group(1)
    for clause in re.split(r'[。；;\n]',p):
        if any(w in clause for w in words):
            aliases=re.findall(r'@image \d+',clause)
            if len(aliases)==1 and aliases[0] in amap:return aliases[0]
    return None


def resolve_frame_roles(mode:str, assets:list[dict], prompt:str)->dict:
    prompt=normalize_prompt_mentions(prompt)
    records=alias_records(assets); amap=alias_map(records); images=[r for r in records if r['kind']=='image']
    first=_explicit_role_alias(prompt,_FIRST_WORDS,records)
    last=_explicit_role_alias(prompt,_LAST_WORDS,records)
    if not (first and last):
        m=re.search(r'首尾帧[^。；;\n]{0,30}?(@image \d+)[^。；;\n]{0,16}?(?:和|及|与|、|,|，)[^。；;\n]{0,16}?(@image \d+)',prompt or '')
        if m and m.group(1) in amap and m.group(2) in amap:
            first=first or m.group(1); last=last or m.group(2)
    source='explicit_prompt' if first or last else 'upload_order'
    if mode=='first_frame':
        if not first and images:first=images[0]['alias']
        last=None
    elif mode=='first_last':
        if not first and images:first=images[0]['alias']
        if not last and len(images)>1:last=images[1]['alias']
        if first and last and first==last:
            last=next((x['alias'] for x in images if x['alias']!=first),None)
    else:
        source='not_applicable'; first=None; last=None
    return {
        'source':source,
        'first_alias':first,'last_alias':last,
        'first_asset_id':amap.get(first,{}).get('asset_id') if first else None,
        'last_asset_id':amap.get(last,{}).get('asset_id') if last else None,
        'records':records,
    }


def infer_mode(requested_mode:str, assets:list[dict], prompt:str):
    requested=(requested_mode or 'auto').strip()
    if requested!='auto':return requested,'manual'
    prompt=normalize_prompt_mentions(prompt)
    records=alias_records(assets)
    validate_prompt_aliases(prompt,records)
    if not records:return 'text','auto_no_reference'
    kinds=[r['kind'] for r in records]; images=kinds.count('image'); videos=kinds.count('video'); audios=kinds.count('audio'); p=prompt or ''
    explicit_first=_explicit_role_alias(p,_FIRST_WORDS,records) if images else None
    explicit_last=_explicit_role_alias(p,_LAST_WORDS,records) if images else None
    has_first=any(w in p for w in _FIRST_WORDS) or bool(explicit_first)
    has_last=any(w in p for w in _LAST_WORDS) or bool(explicit_last) or ('首尾帧' in p)
    if videos and any(w in p for w in _EXTEND_WORDS):return 'extend','auto_extend'
    if videos and any(w in p for w in _EDIT_WORDS):return 'edit','auto_video_edit'
    if images>=2 and has_last:return 'first_last','auto_first_last'
    if images>=1 and has_first:return 'first_frame','auto_first_frame'
    if videos or audios or len(set(kinds))>1:return 'reference','auto_omni_reference'
    if images:return 'image_reference','auto_image_reference'
    return 'text','auto_fallback'


def binding_summary(assets:list[dict], frame_info:dict|None=None)->list[dict]:
    frame_info=frame_info or {}; out=[]
    for r in alias_records(assets):
        role='普通参考'
        if r['asset_id']==frame_info.get('first_asset_id'):role='首帧'
        if r['asset_id']==frame_info.get('last_asset_id'):role='尾帧'
        out.append({'alias':r['alias'],'provider_alias':r['provider_alias'],'asset_id':r['asset_id'],'kind':r['kind'],'name':r['asset'].get('original_name'),'role':role,'upload_index':r['upload_index']})
    return out


def image_prompt(prompt:str, source:dict|None)->str:
    """Single-image edit supports @image 1; accept legacy references as input."""
    normalized=normalize_prompt_mentions(prompt)
    aliases=prompt_aliases(normalized)
    if not aliases:return prompt
    if not source:raise ValueError('图片提示词使用了 @image 引用，但当前没有图生图起始图片。')
    invalid=[x for x in aliases if x!='@image 1']
    if invalid:raise ValueError('当前图片编辑接口一次只提交 1 张起始图片；请只使用 @image 1。')
    return normalized.replace('@image 1','输入图片')
