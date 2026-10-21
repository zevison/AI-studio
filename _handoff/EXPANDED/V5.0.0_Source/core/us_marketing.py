"""Deterministic United States market calendar / time constraints. No weather assertions."""
from __future__ import annotations
import datetime as dt
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Windows Python distributions usually have no IANA timezone database.
# Bundle the exact US Eastern TZif data so launch never depends on a fresh pip
# install of tzdata.  Do NOT replace New York with UTC-5: DST would be wrong.
_BUNDLED_NEW_YORK = Path(__file__).resolve().parents[1] / 'assets' / 'timezones' / 'America_New_York.tzif'


def load_us_eastern_zone():
    try:
        return ZoneInfo('America/New_York')
    except ZoneInfoNotFoundError as exc:
        if not _BUNDLED_NEW_YORK.is_file():
            raise RuntimeError('美国纽约时区数据缺失。请重新应用 V3.9.8.9 时区修复补丁，勿手动更改系统时间。') from exc
        # tzfile is bundled with the app and supports historical/current DST.
        with _BUNDLED_NEW_YORK.open('rb') as stream:
            return ZoneInfo.from_file(stream, key='America/New_York')


US_EAST = load_us_eastern_zone()
SEASON={12:'winter',1:'winter',2:'winter',3:'spring',4:'spring',5:'spring',6:'summer',7:'summer',8:'summer',9:'fall',10:'fall',11:'fall'}
CN={'winter':'冬季','spring':'春季','summer':'夏季','fall':'秋季'}

def context(now=None):
    if now is None:now=dt.datetime.now(dt.timezone.utc)
    if isinstance(now,(int,float)):now=dt.datetime.fromtimestamp(now,dt.timezone.utc)
    if now.tzinfo is None:raise ValueError('日期必须包含时区')
    local=now.astimezone(US_EAST);season=SEASON[local.month]
    return {'market_country':'US','as_of_us':local.date().isoformat(),
            'season':season,'season_cn':CN[season], 'month':local.month,
            'time_zone':'America/New_York', 'region_policy':'面向美国市场；美国不同州气候不同，未经来源确认不可推断当地气温、降雪、风暴等天气。',
            'event_policy':'只有有可信来源的时效性事件才能用于事实宣传，节日日期必须核对。',
            'consumer_language':'美国自然口语英语；同时给出完整中文对照',
            'commercial_truth_policy':'功能及性能必须依据已确认商品事实，不得由热点推断产品能力'}

def validate_script(doc, market):
    problems=[]
    if doc.get('market_country')!='US':problems.append('缺少或错误的美国市场标记')
    if doc.get('as_of_us')!=market['as_of_us']:problems.append('美国日期不一致')
    if doc.get('season')!=market['season']:problems.append('季节不符')
    if not str(doc.get('seasonal_fit_cn') or '').strip():problems.append('缺少季节适用性说明')
    if not str(doc.get('hook_en') or '').strip() or not str(doc.get('hook_zh') or '').strip():problems.append('缺少双语开头')
    segments=doc.get('script_segments')
    if not isinstance(segments,list) or not segments:problems.append('无分镜')
    else:
        for i,seg in enumerate(segments):
            if not isinstance(seg,dict) or not all(str(seg.get(k) or '').strip() for k in ('narration_en','narration_zh','visual_cn')):
                problems.append(f'分镜{i+1}缺少双语文案或画面');break
    # Specific wrong-season statements are a safety failure, not simply a style issue.
    en=' '.join([str(doc.get('hook_en') or '')]+[str(v.get('narration_en') or '') for v in (segments or []) if isinstance(v,dict)]).lower()
    for season in ('winter','spring','summer','fall','autumn'):
        normalized='fall' if season=='autumn' else season
        if normalized==market['season']:continue
        if re.search(r'\b'+season+r'\s+(?:is here|has arrived|is in full swing|season is here)\b',en):
            problems.append('当前季节与英文脚本声称的季节冲突');break
    holidays={'halloween':'10-31','christmas':'12-25','independence day':'07-04','thanksgiving':None}
    for title,date in holidays.items():
        if re.search(r'\b(?:today is|it is today|it\'s today|happy)\s+'+re.escape(title)+r'\b',en):
            # Thanksgiving varies annually and is therefore always a human-review fact unless sourced.
            if not date or market['as_of_us'][5:]!=date:
                problems.append('节日当日宣传与美国当地日期不一致或待核实');break
    if re.search(r'\b(?:it is snowing|it\'s snowing|blizzard today|heatwave today|heat wave today)\b',en):
        problems.append('实时天气表述必须附可靠地区及来源证明，由人工确认')
    return {'ok':not problems,'errors':problems}
