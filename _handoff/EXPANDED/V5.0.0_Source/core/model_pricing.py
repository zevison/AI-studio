"""OpenLux automated LLM price discovery. Read-only, fail-closed.
Prices are public/reference rates, NOT a promise of final account billing.
Never infer separate input/output prices from the pricing website's 'from /M'.
"""
from __future__ import annotations
import re,time,threading
from . import openlux

_lock=threading.RLock()
_cache={}
TTL=180

def _positive(v):
    try:
        v=float(v)
        if 0<v<10000 and v==v:return v
    except (ValueError,TypeError,OverflowError):pass
    return None

def _unwrap(resp):
    if not isinstance(resp,dict):return None
    d=resp.get('data')
    return d if isinstance(d,(dict,list)) else None

def _from_pricing(model):
    resp=openlux.request_json('GET','/api/pricing',timeout=12,retries=0)
    rows=_unwrap(resp)
    if not isinstance(rows,list):return None
    # Never assume the first channel is representative. Only exact model IDs.
    matches=[x for x in rows if isinstance(x,dict) and str(x.get('model_name') or x.get('model') or '')==model]
    if not matches:return None
    ratios=resp.get('group_ratio') if isinstance(resp.get('group_ratio'),dict) else {}
    candidates=[]
    for item in matches:
        try:qt=int(item.get('quota_type') or 0)
        except (TypeError,ValueError):continue
        if qt!=0:continue  # per-request pricing cannot be converted into text token prices
        inp=next((_positive(item.get(k)) for k in ('input_price_per_m','prompt_price_per_m') if _positive(item.get(k))),None)
        out=next((_positive(item.get(k)) for k in ('output_price_per_m','completion_price_per_m') if _positive(item.get(k))),None)
        mr=_positive(item.get('model_ratio'));cr=_positive(item.get('completion_ratio'))
        if inp is None and mr:inp=2*mr
        if out is None and inp and cr:out=inp*cr
        if inp is None or out is None:continue
        groups=item.get('enable_group') or item.get('enable_groups')
        if isinstance(groups,str):groups=[groups]
        if not isinstance(groups,list):groups=list(ratios) if ratios else ['default']
        factors=[_positive(ratios.get(g)) for g in groups if g in ratios]
        factor=max(factors) if factors else (max([_positive(v) or 1 for v in ratios.values()]) if ratios else 1)
        candidates.append((inp*factor,out*factor))
    if not candidates:return None
    # Use the highest advertised channel/group rate for a conservative estimate.
    i=max(x[0] for x in candidates);o=max(x[1] for x in candidates)
    return {'model':model,'input_usd_per_million':round(i,6),'output_usd_per_million':round(o,6),
            'source':'OpenLux /api/pricing','source_url':'https://www.openlux.ai/pricing',
            'group_policy':'公开渠道/分组最高参考倍率；个人实际计费以账单为准'}

def _from_ratios(model):
    resp=openlux.request_json('GET','/api/ratio_config',timeout=12,retries=0)
    d=_unwrap(resp)
    if not isinstance(d,dict):return None
    models=d.get('model_ratio');comps=d.get('completion_ratio')
    if not isinstance(models,dict) or not isinstance(comps,dict):return None
    mr=_positive(models.get(model));cr=_positive(comps.get(model))
    if not mr or not cr:return None
    return {'model':model,'input_usd_per_million':round(2*mr,6),
            'output_usd_per_million':round(2*mr*cr,6),
            'source':'OpenLux /api/ratio_config','source_url':'https://www.openlux.ai/pricing',
            'group_policy':'基础分组参考倍率；账号其他分组实际扣费可能不同'}

def get(model,force=False):
    model=str(model or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.:/@+\-]{2,90}',model):
        raise ValueError('无效模型编号')
    now=time.time()
    with _lock:
        obj=_cache.get(model)
        if obj and not force and now-obj['checked_at']<TTL:return dict(obj)
    errors=[]
    for fn in (_from_pricing,_from_ratios):
        try:
            result=fn(model)
            if result:
                result.update({'available':True,'checked_at':time.time(),'currency':'USD','billing':'token',
                               'warning':'自动读取 OpenLux 公开计费数据；实际账号分组、附加计费和最终账单可能不同。'})
                with _lock:_cache[model]=dict(result)
                return result
        except Exception as exc:errors.append(str(exc)[:90])
    return {'available':False,'model':model,'input_usd_per_million':None,'output_usd_per_million':None,
            'source_url':'https://www.openlux.ai/pricing','checked_at':time.time(),
            'warning':'OpenLux 未返回该模型完整且可核验的输入/输出词元报价；已停止付费预检。'+(';'.join(errors[:2]) if errors else '')}
