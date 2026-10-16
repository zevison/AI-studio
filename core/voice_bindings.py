"""Paid-preview firewall for explicit @audio-to-speaker assignments.

No ASR, vision inference, voice identification or paid provider calls occur here.
The parser intentionally accepts a narrow, auditable assignment vocabulary.
Ambiguous multi-speaker prompts fail *before* a video-preview token is created.
"""
from __future__ import annotations
import re
from . import reference_logic

_ROLE_WORD = (r'(?:最左边|最右边|左边|右边|左侧|右侧|中间|中央|画面中央|画面左边|画面右边)?'
              r'(?:的)?\s*'
              r'(?:小女孩|小男孩|女孩子|男孩子|女主持人|男主持人|女顾客|男顾客|'
              r'女孩|男孩|女生|男生|女人|男人|女性|男性|女士|先生|妈妈|爸爸|'
              r'主持人|顾客|店员|旁白|画外音|解说员|主播|演员|角色\s*[A-Za-z0-9甲乙丙丁一二三]+|'
              r'人物\s*[A-Za-z0-9甲乙丙丁一二三]+|说话人\s*[A-Za-z0-9甲乙丙丁一二三]+|'
              r'名为\s*[A-Za-z\u4e00-\u9fff]{1,10}\s*的(?:人物|角色|男孩|女孩|男生|女生)|'
              r'(?:Alice|Bob|Alex|Emma|Lisa|Tom|Jack|Mary)|'
              r'(?:(?:the\s+)?(?:girl|boy|woman|man|female host|male host|host|narrator|speaker\s*\d+|character\s*[A-Za-z0-9]+)(?:\s+on\s+the\s+(?:left|right))?|(?:left|right)\s+(?:girl|boy|woman|man)))')
ROLE = re.compile(_ROLE_WORD, re.I)
AUDIO = re.compile(r'@audio (\d+)(?!\d)', re.I)
IMAGE = re.compile(r'@image (\d+)(?!\d)', re.I)
PUNCT = re.compile(r'[，,。；;！？!?\n]+')
BIND_BEFORE = re.compile(r'(?:使用|用|参考|采用|沿用|保持|匹配|模仿|绑定|配|配上|采用|设为|指定|声音取自|音色来自|声音来自|说话参考|说话用|音色参考|声音参考|音色用)[^。；;，,\n]{0,14}$')
BIND_AFTER = re.compile(r'^\s*(?:的音色|的声音|音色|声音)?\s*(?:用作|用于|用在|分配给|绑定到|绑定给|配给|给|作为|属于|由|对应|提供给|赋予|是)[^。；;，,\n]{0,14}')
MULTI = re.compile(r'(?:(?:两|二|三|四|2|3|4|多)(?:个|位|名)?(?:人物|角色|说话者|人)[^。；;\n]{0,30}(?:说话|交谈|对话|聊天|台词|交替|轮流|发言)|'
                   r'(?:对话|交谈|聊天|一问一答|轮流说|分别说|两人都说|三人都说)|'
                   r'(?:男生|男人|男孩|爸爸|男主持人)[^。；;\n]{0,40}(?:和|与|跟)[^。；;\n]{0,20}(?:女生|女孩|女人|妈妈|女主持人)[^。；;\n]{0,25}(?:说话|回答|交谈|对话|聊天)|'
                   r'(?:女孩|女生|女人|妈妈)[^。；;\n]{0,30}(?:提问|问|说)[^。；;\n]{0,55}(?:男孩|男生|男人|爸爸)[^。；;\n]{0,20}(?:回答|答|说)|'
                   r'\b(?:two|three|multiple)\s+(?:people|characters|speakers)[^.\n]{0,55}\b(?:talk|speak|chat|conversation|dialogue)|'
                   r'\b(?:dialogue|conversation|chat)\s+between\b|\bboth\s+(?:speak|talk|answer)\b|'
                   r'\b(?:woman|girl|man|boy)[^.\n]{0,35}\b(?:asks?|says?|speaks?)\b[^.\n]{0,55}\b(?:woman|man|girl|boy)[^.\n]{0,25}\b(?:replies|answers|says?)\b|'
                   r'\b(?:woman|girl|man|boy)\s+and\s+(?:a\s+|the\s+)?(?:woman|man|girl|boy)[^.\n]{0,25}\b(?:talk|chat|speak)\b)',re.I)


def _role_and_image(segment: str, *, backward: bool):
    """Find nearest human descriptor, not just @image (an image may contain 2+ people)."""
    matches=list(ROLE.finditer(segment))
    if not matches:
        return None
    role=matches[-1] if backward else matches[0]
    # Include image alias only if explicitly located in same text fragment.
    before=segment[:role.start()]
    images=list(IMAGE.finditer(before))
    image=f'@image {images[-1].group(1)}' if images else ''
    label=(image+' 中 ' if image else '')+re.sub(r'\s+',' ',role.group(0)).strip()
    return label


def _specific_assignment(part: str, start: int, end: int):
    before,after=part[:start].strip(),part[end:].strip()
    # Examples: "@image 1 中右边的男生使用 @audio 1 的音色"
    #           "男生的声音参考 @audio 1"
    # Most explicit descriptions end with a voice or use verb.
    pre=re.search(r'(?:使用|用|参考|采用|沿用|保持|绑定|配上|配|指定|取自|来自|uses?|speaks? with|voice(?: is)? from|voice matches|matches|reference(?:s)?)\s*$',before,re.I)
    if pre:
        desc=before[:pre.start()].rstrip(' 的音色声音\t')
        role=_role_and_image(desc,backward=True)
        if role:return role
    # "男生的声音是 @audio 1" / "男生音色：@audio 1"
    m=re.search(r'(?:的(?:声音|音色)|(?:声音|音色))\s*(?:为|是|:|：)\s*$',before)
    if m:
        role=_role_and_image(before[:m.start()],backward=True)
        if role:return role
    # "@audio 1 用于 @image 1 中右侧男生" / "@audio 1 作为女孩的声音"
    post=re.match(r'^(?:的音色|的声音|音色|声音)?\s*(?:用于|给|分配给|绑定到|绑定给|配给|赋予|作为|是|对应|for|assigned to|used by|used for)\s*',after,re.I)
    if post:
        role=_role_and_image(after[post.end():],backward=False)
        if role:return role
    return None


def plan(prompt: str, assets: list[dict], option: str|None) -> dict:
    """Parse explicit @audio -> visible character/voice assignments.

    Returned mappings are frozen into previews. Does NOT claim provider
    implements exact speaker isolation; the provider still needs verification.
    """
    empty={'bindings':[], 'warnings':[], 'instruction':'', 'explicit':False}
    if not option:return empty
    records=reference_logic.alias_records(assets)
    audios=[x for x in records if x['kind']=='audio']
    if not audios:return empty
    normalized=reference_logic.normalize_prompt_mentions(prompt or '')
    reference_logic.validate_prompt_aliases(normalized,records)
    all_aliases={r['alias'] for r in audios}
    referenced=set(AUDIO.findall(normalized))
    multi=bool(MULTI.search(normalized))
    assignments={}
    # Keep role descriptions on the same punctuation-delimited fragment as @audio.
    for part in PUNCT.split(normalized):
        for match in AUDIO.finditer(part):
            alias=f'@audio {match.group(1)}'
            if alias not in all_aliases:raise ValueError(f'{alias} 不在当前上传音频中；请重新选择音频后预览（未扣费）。')
            role=_specific_assignment(part,match.start(),match.end())
            if not role:continue
            if alias in assignments and assignments[alias]!=role:
                raise ValueError(f'{alias} 同时绑定了不同人物（{assignments[alias]} / {role}）；请修改提示词，未扣费。')
            assignments[alias]=role

    # Strict mode only when multiple human speakers or multiple reference voices.
    # One attached voice with no recognized multi-speaker cue keeps single-speaker
    # original behavior, but does not guess which character in a multi-person shot.
    if multi or len(audios)>1:
        unassigned=all_aliases-set(assignments)
        if unassigned:
            samples='、'.join(sorted(unassigned,key=lambda x:int(x.rsplit(' ',1)[1])))
            raise ValueError('检测到多人对话或多段声音，但 '+samples+' 没有明确绑定说话人。'
                             '请在提示词写“右边的男生使用 @audio 1 的音色”或“@audio 1 用于左边的女生”；'
                             '一张图片包含两个人时须写清楚左右/角色，系统不会猜测，未扣费。')
    # Multiple aliases mapped to same role is more likely a mistake than intended
    # voice switching. Require unambiguous mapping; don't implicitly share roles.
    inverse={}
    for audio,role in assignments.items():
        role_key=re.sub(r'\s+','',role).lower()
        if role_key in inverse and inverse[role_key]!=audio:
            raise ValueError(f'同一说话人“{role}”同时绑定 {inverse[role_key]} 和 {audio}；请为每个人物选择唯一音色（未扣费）。')
        inverse[role_key]=audio
    if not assignments and len(audios)==1 and not multi:
        # No explicit role selected: leave it as a single-voice *instruction*,
        # without pretending to know which person is actually pictured.
        audio=audios[0]
        mappings=[{'alias':audio['alias'],'provider_alias':audio['provider_alias'],
                   'asset_id':audio['asset_id'],'target':'唯一发声人物或旁白','source':'single_voice_default'}]
        warning='未指定人物，系统仅对唯一发声者参考该音色；如画面涉及多个人物，请用 @ 指定具体人物。'
        warnings=[warning]
    else:
        mappings=[{'alias':r['alias'],'provider_alias':r['provider_alias'],
                   'asset_id':r['asset_id'],'target':assignments[r['alias']],'source':'explicit_at'} for r in audios if r['alias'] in assignments]
        warnings=[]
    action='仅模仿音色，不使用原台词，台词按用户新文案或模型生成' if option=='timbre' else '保持上传音频原台词、音色和时序，输出原音轨，不要替换其声音'
    lines=[f'{x["provider_alias"]} → {x["target"]}（{action}）' for x in mappings]
    instruction=('【角色与参考音频绑定（高优先级；不得自行调换或给其他角色使用）】\n'
                 +'\n'.join(lines)+'\n未被上述绑定的角色不得复用绑定音色；不同人物的声音应当区分。'
                 '如果无法准确复现，不能假装保证完全音色克隆或口型匹配。') if lines else ''
    if option=='content' and mappings:
        warnings.append('原音频保留模式会使用原音轨；若一段音频内有多名说话人，请先裁剪成单一说话人的音频片段后分别绑定。')
    return {'bindings':mappings,'warnings':warnings,'instruction':instruction,'explicit':bool(assignments)}
