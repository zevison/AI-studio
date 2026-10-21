from __future__ import annotations
import time, threading, json, urllib.request, urllib.error
from pathlib import Path
from . import config, db, openlux, settings, secrets_store

_LOCK=threading.Lock()
_BALANCE_CACHE={'ts':0.0,'value':None}
_PRICE_CACHE={'ts':0.0,'value':None}
_FX_CACHE={'ts':0.0,'value':None}
CACHE_SECONDS=20
DEFAULT_FPS=24.0
FX_CACHE_PATH=config.CACHE_DIR/'cny_usd_rate.json'

# 已由 OpenLux 模型详情页核验的价格。
# 单位是 OpenLux 页面展示的计费单位 / 1M Tokens，不擅自标成 USD。
VERIFIED_MODEL_PRICES_PER_M={
    'doubao-seedance-2-5-260628': 6.955,
}

# Seedance 2.5 官方分辨率 × 宽高比像素表。
PIXELS={
    '480p':{
        '16:9':(854,480),'4:3':(752,560),'1:1':(640,640),
        '3:4':(560,752),'9:16':(480,854),'21:9':(992,432),
    },
    '720p':{
        '16:9':(1280,720),'4:3':(1112,834),'1:1':(960,960),
        '3:4':(834,1112),'9:16':(720,1280),'21:9':(1470,630),
    },
    '1080p':{
        '16:9':(1920,1080),'4:3':(1664,1248),'1:1':(1440,1440),
        '3:4':(1248,1664),'9:16':(1080,1920),'21:9':(2206,946),
    },
}

def _num(v):
    try:
        if v is None or v=='': return None
        return float(v)
    except Exception:
        return None

def _data(resp):
    if not isinstance(resp,dict): return None
    return resp.get('data') if resp.get('data') is not None else resp

def _quota_divisor():
    try:
        v=float(settings.get_all().get('cost_quota_per_usd') or 500000.0)
        return v if v>0 else 500000.0
    except Exception:
        return 500000.0

def _quota_to_balance(v):
    n=_num(v)
    return None if n is None else max(0.0,n/_quota_divisor())

# ---------------- Account wallet ----------------

def _parse_user_self(resp):
    d=_data(resp)
    if not isinstance(d,dict): return None
    quota=_num(d.get('quota'))
    if quota is None: return None
    return {
        'ok':True,
        'available':_quota_to_balance(quota),
        'used':_quota_to_balance(d.get('used_quota')),
        'raw_available':quota,
        'source':'openlux_user_self',
        'username':d.get('username') or d.get('display_name'),
        'display_name':d.get('display_name'),
        'email':d.get('email'),
        'group':d.get('group'),
        'request_count':d.get('request_count'),
        'user_id':d.get('id'),
        'detail':'OpenLux /api/user/self 账户钱包余额',
        'unlimited':False,
    }

def _try_user_self_with_model_key():
    try:
        out=_parse_user_self(openlux.request_json('GET','/api/user/self',timeout=12,retries=0))
        if out:
            out['source']='openlux_user_self_api_key'
            out['detail']='OpenLux /api/user/self（当前 API Key）'
        return out
    except Exception:
        return None

def _try_account_access_token():
    token=secrets_store.get('openlux_account_access_token','').strip()
    if not token: return None
    uid=secrets_store.get('openlux_account_user_id','').strip() or None
    try:
        out=_parse_user_self(
            openlux.request_json_with_token(
                'GET','/api/user/self',token,timeout=12,retries=0,user_id=uid
            )
        )
        if out:
            out['source']='openlux_user_self_account_token'
            out['detail']='OpenLux /api/user/self（账号访问令牌）'
        return out
    except Exception:
        return None

def fetch_balance(force=False):
    now=time.time()
    with _LOCK:
        if not force and _BALANCE_CACHE['value'] is not None and now-_BALANCE_CACHE['ts']<CACHE_SECONDS:
            return dict(_BALANCE_CACHE['value'])

    result=_try_user_self_with_model_key() or _try_account_access_token()
    if result is None:
        with _LOCK:
            prev=_BALANCE_CACHE.get('value')
        if prev and prev.get('ok'):
            result=dict(prev)
            result['stale']=True
            result['detail']='账户余额暂时刷新失败，继续显示最近一次 /api/user/self 成功值'
        else:
            result={
                'ok':False,'available':None,'used':None,'unlimited':False,
                'source':'user_self_unavailable',
                'detail':'暂未从 /api/user/self 读取到账户钱包余额',
                'needs_account_binding':True,
            }

    with _LOCK:
        _BALANCE_CACHE['ts']=now
        _BALANCE_CACHE['value']=dict(result)
    return result

# ---------------- OpenLux live model price ----------------

def _base_model_name():
    cfg=settings.get_all()
    return str(
        cfg.get('video_base_model')
        or config.VIDEO_MODEL
        or 'doubao-seedance-2-5-260628'
    ).split(':',1)[0]

def _token_rate_from_ratio(model_ratio,completion_ratio=1.0,group_ratio=1.0):
    mr=_num(model_ratio)
    cr=_num(completion_ratio)
    gr=_num(group_ratio)
    if mr is None or mr<=0:
        return None
    if cr is None or cr<=0:
        cr=1.0
    if gr is None or gr<=0:
        gr=1.0
    return mr*2.0*cr*gr

def _group_ratio_for_response(resp, item=None):
    """当前工作台默认只使用 token 所在分组；若没有明确分组，优先 default，否则 1。"""
    if not isinstance(resp,dict):
        return 1.0,'default'
    ratios=resp.get('group_ratio')
    if not isinstance(ratios,dict) or not ratios:
        return 1.0,'default'

    # item.enable_group 只有一个分组时可直接使用。
    if isinstance(item,dict):
        groups=item.get('enable_group')
        if isinstance(groups,list) and len(groups)==1 and groups[0] in ratios:
            return _num(ratios.get(groups[0])) or 1.0,str(groups[0])

    if 'default' in ratios:
        return _num(ratios.get('default')) or 1.0,'default'

    # 不猜特殊分组，避免把另一个付费组价格套到当前用户。
    return 1.0,'unknown'

def _pricing_item_to_rate(item,envelope=None,force_token_model=False):
    quota_type=int(item.get('quota_type') or 0)
    model_price=_num(item.get('model_price')) or 0.0
    model_ratio=_num(item.get('model_ratio'))
    completion_ratio=_num(item.get('completion_ratio'))
    group_ratio,group_name=_group_ratio_for_response(envelope,item)

    if completion_ratio is None or completion_ratio<=0:
        completion_ratio=1.0

    # 最可靠：明确的每百万 Token 字段。
    for k in (
        'output_price_per_m','output_price','completion_price_per_m',
        'price_per_m_tokens','price_per_m','token_price_per_m'
    ):
        v=_num(item.get(k))
        if v is not None and v>0:
            return {
                'billing':'token','price_per_m':v*group_ratio,
                'source_field':k,'group_ratio':group_ratio,'group_name':group_name,
            }

    # 对已知 Token 计费视频模型，不允许 quota_type=1 的 0.01 之类记录覆盖真实 Token 报价。
    if force_token_model:
        rate=_token_rate_from_ratio(model_ratio,completion_ratio,group_ratio)
        if rate is not None:
            return {
                'billing':'token','price_per_m':rate,
                'model_ratio':model_ratio,'completion_ratio':completion_ratio,
                'group_ratio':group_ratio,'group_name':group_name,
                'source_field':'pricing:model_ratio×2×completion_ratio×group_ratio',
            }
        return None

    if quota_type==0:
        rate=_token_rate_from_ratio(model_ratio,completion_ratio,group_ratio)
        if rate is not None:
            return {
                'billing':'token','price_per_m':rate,
                'model_ratio':model_ratio,'completion_ratio':completion_ratio,
                'group_ratio':group_ratio,'group_name':group_name,
                'source_field':'pricing:model_ratio×2×completion_ratio×group_ratio',
            }
        # 部分分支会直接把 Token 单价放 model_price。
        if model_price>0:
            return {
                'billing':'token','price_per_m':model_price*group_ratio,
                'source_field':'pricing:model_price','group_ratio':group_ratio,'group_name':group_name,
            }

    if quota_type==1 and model_price>0:
        return {
            'billing':'request','price_per_request':model_price*group_ratio,
            'source_field':'pricing:model_price',
            'group_ratio':group_ratio,'group_name':group_name,
        }

    return None

def _fetch_ratio_config(model):
    """New API / OpenLux 公共倍率接口。对于当前 Seedance 2.5，优先用它还原 Token 报价。"""
    try:
        resp=openlux.request_json('GET','/api/ratio_config',timeout=12,retries=1)
        d=resp.get('data') if isinstance(resp,dict) else None
        if not isinstance(d,dict):
            return None

        model_ratios=d.get('model_ratio') or {}
        completion_ratios=d.get('completion_ratio') or {}
        model_prices=d.get('model_price') or {}

        # ratio_config 的 map 形式与 pricing 列表口径不同：
        # model_ratio × 2 × completion_ratio = 每百万 Token 价格（基础分组）。
        mr=_num(model_ratios.get(model)) if isinstance(model_ratios,dict) else None
        cr=_num(completion_ratios.get(model)) if isinstance(completion_ratios,dict) else None
        if mr is not None and mr>0:
            if cr is None or cr<=0:
                cr=1.0
            price=_token_rate_from_ratio(mr,cr,1.0)
            if price is not None:
                return {
                    'ok':True,'model':model,'source':'openlux_ratio_config',
                    'billing':'token','price_per_m':price,
                    'model_ratio':mr,'completion_ratio':cr,
                    'source_field':'ratio_config:model_ratio×2×completion_ratio',
                }

        # 若公共倍率接口直接给 model_price，只有在没有 ratio 时才使用。
        mp=_num(model_prices.get(model)) if isinstance(model_prices,dict) else None
        if mp is not None and mp>0:
            return {
                'ok':True,'model':model,'source':'openlux_ratio_config_model_price',
                'billing':'token','price_per_m':mp,
                'source_field':'ratio_config:model_price',
            }
    except Exception:
        return None
    return None

def _fetch_pricing_candidates(model):
    """扫描所有同名记录，而不是 next() 取第一条。"""
    try:
        resp=openlux.request_json('GET','/api/pricing',timeout=15,retries=1)
        rows=resp.get('data') if isinstance(resp,dict) else None
        if not isinstance(rows,list):
            return None,[],resp

        matches=[
            x for x in rows
            if isinstance(x,dict)
            and str(x.get('model_name') or x.get('model') or '').split(':',1)[0]==model
        ]
        return resp,matches,resp
    except Exception:
        return None,[],None

def fetch_model_price(force=False):
    now=time.time()
    with _LOCK:
        if not force and _PRICE_CACHE['value'] is not None and now-_PRICE_CACHE['ts']<CACHE_SECONDS:
            return dict(_PRICE_CACHE['value'])

    model=_base_model_name()
    verified=VERIFIED_MODEL_PRICES_PER_M.get(model)
    diagnostics=[]

    # 1) 当前模型是 Token 计费，优先从公开 ratio_config 读取真实倍率。
    result=_fetch_ratio_config(model)
    if result is not None:
        # 若已经有已核验模型页价格，偏差过大时不盲信异常接口值。
        if verified is not None:
            live=float(result.get('price_per_m') or 0)
            deviation=abs(live-verified)/verified if verified else 0
            if deviation>0.12:
                diagnostics.append(
                    f'/api/ratio_config 返回 {live:g}/1M，与已核验模型页 {verified:g}/1M 偏差 {deviation:.1%}，已拒绝异常值'
                )
                result=None
            else:
                result['verified_reference_price_per_m']=verified
                result['verified_match']=True

    # 2) ratio_config 不可用时，扫描 /api/pricing 所有同名记录。
    if result is None:
        pricing_resp,matches,_=_fetch_pricing_candidates(model)
        token_candidates=[]
        per_call_candidates=[]

        for item in matches:
            quota_type=int(item.get('quota_type') or 0)
            parsed=_pricing_item_to_rate(
                item,pricing_resp,
                force_token_model=(model in VERIFIED_MODEL_PRICES_PER_M)
            )
            if not parsed:
                # 保留错误记录用于诊断。
                if quota_type==1 and _num(item.get('model_price')):
                    diagnostics.append(
                        f'忽略按次记录 model_price={_num(item.get("model_price")):g}，当前 {model} 已核验为 Token 计费'
                    )
                continue
            row={'ok':True,'model':model,'source':'openlux_pricing','item':item,**parsed}
            if row.get('billing')=='token':
                token_candidates.append(row)
            else:
                per_call_candidates.append(row)

        # 对已核验 Token 模型，只接受 Token candidate。
        candidates=token_candidates if model in VERIFIED_MODEL_PRICES_PER_M else (token_candidates or per_call_candidates)

        if candidates:
            if verified is not None:
                # 多条同名记录时，选择最接近 OpenLux 模型详情页核验值的真实 Token 记录。
                candidates.sort(key=lambda x:abs(float(x.get('price_per_m') or 1e99)-verified))
                candidate=candidates[0]
                live=float(candidate.get('price_per_m') or 0)
                deviation=abs(live-verified)/verified if verified else 0
                if deviation<=0.12:
                    result=candidate
                    result['verified_reference_price_per_m']=verified
                    result['verified_match']=True
                else:
                    diagnostics.append(
                        f'/api/pricing 最接近的 Token 报价仍为 {live:g}/1M，与模型页 {verified:g}/1M 偏差过大'
                    )
            else:
                result=candidates[0]

    # 3) 当前常用模型：OpenLux 模型详情页已核验 6.955 / 1M Tokens。
    #    当公开价格接口返回冲突记录时，使用核验价，而不是显示 0.01/次。
    if result is None and verified is not None:
        result={
            'ok':True,'model':model,
            'source':'verified_openlux_model_page',
            'billing':'token',
            'price_per_m':float(verified),
            'source_field':'verified_model_detail_page',
            'verified_match':True,
            'verified_reference_price_per_m':float(verified),
            'diagnostics':diagnostics,
        }

    # 4) 未知模型且两个价格接口都不可用：让 estimate_video 进入 ¥1/秒备用公式。
    if result is None:
        result={
            'ok':False,'model':model,'source':'unavailable',
            'error':'OpenLux 实时 Token 报价暂不可用',
            'diagnostics':diagnostics,
        }
    else:
        if diagnostics:
            result['diagnostics']=diagnostics

    with _LOCK:
        _PRICE_CACHE['ts']=now
        _PRICE_CACHE['value']=dict(result)
    return result

# ---------------- CNY -> USD live FX fallback ----------------

def _read_fx_disk():
    try:
        if not FX_CACHE_PATH.exists():
            return None
        obj=json.loads(FX_CACHE_PATH.read_text(encoding='utf-8'))
        rate=_num(obj.get('rate'))
        if rate is None or rate<=0:
            return None
        return obj
    except Exception:
        return None

def _write_fx_disk(obj):
    try:
        FX_CACHE_PATH.parent.mkdir(parents=True,exist_ok=True)
        FX_CACHE_PATH.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
    except Exception:
        pass

def _fetch_json_url(url,timeout=8):
    req=urllib.request.Request(
        url,
        headers={'User-Agent':f'SeedanceStudio/{config.APP_VERSION}'},
        method='GET',
    )
    with urllib.request.urlopen(req,timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8','replace'))

def fetch_fx_rate(force=False):
    cfg=settings.get_all()
    ttl=max(300,int(cfg.get('cost_fx_refresh_minutes') or 30)*60)
    now=time.time()

    with _LOCK:
        if not force and _FX_CACHE['value'] is not None and now-_FX_CACHE['ts']<ttl:
            return dict(_FX_CACHE['value'])

    errors=[]
    result=None

    # Primary: no-key exchange-rate endpoint.
    try:
        j=_fetch_json_url('https://open.er-api.com/v6/latest/CNY')
        rate=_num((j.get('rates') or {}).get('USD'))
        if rate and rate>0:
            result={
                'ok':True,'rate':rate,'base':'CNY','quote':'USD',
                'source':'open.er-api.com',
                'as_of':j.get('time_last_update_utc') or j.get('time_last_update_unix'),
                'stale':False,
            }
    except Exception as e:
        errors.append(str(e)[:160])

    # Backup: Frankfurter (ECB reference data).
    if result is None:
        try:
            j=_fetch_json_url('https://api.frankfurter.app/latest?from=CNY&to=USD')
            rate=_num((j.get('rates') or {}).get('USD'))
            if rate and rate>0:
                result={
                    'ok':True,'rate':rate,'base':'CNY','quote':'USD',
                    'source':'frankfurter.app',
                    'as_of':j.get('date'),
                    'stale':False,
                }
        except Exception as e:
            errors.append(str(e)[:160])

    if result is not None:
        result['saved_at']=now
        _write_fx_disk(result)
    else:
        disk=_read_fx_disk()
        if disk:
            result={
                'ok':True,
                'rate':float(disk['rate']),
                'base':'CNY','quote':'USD',
                'source':disk.get('source') or 'local_cache',
                'as_of':disk.get('as_of'),
                'saved_at':disk.get('saved_at'),
                'stale':True,
                'refresh_error':'；'.join(errors)[:300],
            }
        else:
            result={
                'ok':False,'rate':None,'base':'CNY','quote':'USD',
                'source':'unavailable','error':'；'.join(errors)[:300],
            }

    with _LOCK:
        _FX_CACHE['ts']=now
        _FX_CACHE['value']=dict(result)
    return result

# ---------------- Video cost estimate ----------------

def _normalize_ratio(ratio):
    r=str(ratio or '9:16').strip()
    return r if r in ('16:9','4:3','1:1','3:4','9:16','21:9') else '9:16'

def _pixels(resolution,ratio):
    res=str(resolution or '720p').lower()
    if res not in PIXELS:
        res='720p'
    r=_normalize_ratio(ratio)
    return PIXELS[res][r]

def _manual_fallback(output_seconds,fx):
    cfg=settings.get_all()
    cny_per_second=float(cfg.get('cost_fallback_cny_per_second') or 1.0)
    cny_cost=max(0.0,float(output_seconds))*cny_per_second
    rate=_num((fx or {}).get('rate'))
    usd_cost=cny_cost*rate if rate is not None and rate>0 else None
    return {
        'configured':usd_cost is not None,
        'source':'manual_cny_fallback',
        'billing':'manual_cny_per_second',
        'cny_per_second':cny_per_second,
        'cost_cny':round(cny_cost,4),
        'fx':fx,
        'cost_usd':None if usd_cost is None else round(usd_cost,6),
        'cost':None if usd_cost is None else round(usd_cost,6),
        'currency':'USD',
        'basis':f'输出视频 {output_seconds:g}s × ¥{cny_per_second:g}/s × CNY→USD 最新可用汇率',
    }

def estimate_video(duration,resolution='720p',ratio='9:16',input_video_seconds=0.0,force_price=False,force_fx=False):
    try: output_seconds=max(0.0,float(duration or 0))
    except Exception: output_seconds=0.0
    try: input_seconds=max(0.0,float(input_video_seconds or 0))
    except Exception: input_seconds=0.0

    ratio=_normalize_ratio(ratio)
    width,height=_pixels(resolution,ratio)
    price=fetch_model_price(force_price)

    base={
        'output_seconds':round(output_seconds,3),
        'input_video_seconds':round(input_seconds,3),
        'resolution':resolution,
        'ratio':ratio,
        'fps':DEFAULT_FPS,
        'width':width,
        'height':height,
        'model_price':price,
    }

    if price.get('ok'):
        if price['billing']=='request':
            cost=float(price['price_per_request'])
            return {
                **base,'configured':True,'source':'openlux_live_price',
                'cost':round(cost,6),'currency':'OPENLUX',
                'billing':'request',
                'basis':'OpenLux /api/pricing 按次价格',
                'fallback_used':False,
            }

        if price['billing']=='second':
            cost=(output_seconds+input_seconds)*float(price['price_per_second'])
            return {
                **base,'configured':True,'source':'openlux_live_price',
                'cost':round(cost,6),'currency':'OPENLUX',
                'billing':'second',
                'basis':'OpenLux /api/pricing 每秒价格 ×（输入视频秒数 + 输出视频秒数）',
                'fallback_used':False,
            }

        # Official Seedance estimate:
        # tokens=(input video seconds + output seconds)*width*height*fps/1024
        tokens=(input_seconds+output_seconds)*width*height*DEFAULT_FPS/1024.0
        ppm=float(price.get('price_per_m') or 0.0)
        amount=tokens/1_000_000.0*ppm
        return {
            **base,
            'configured':True,
            'source':'openlux_live_price',
            'cost':round(amount,6),
            'currency':'OPENLUX',
            'billing':'token',
            'estimated_tokens':round(tokens),
            'price_per_m':round(ppm,6),
            'pixel_area':width*height,
            'basis':'(输入视频秒数 + 输出视频秒数) × 宽 × 高 × 24fps ÷ 1024 × OpenLux /api/pricing 实时每百万 Token 价格',
            'formula':f'({input_seconds:g}+{output_seconds:g})×{width}×{height}×24÷1024 = {round(tokens):,} Tokens；{round(tokens):,}÷1,000,000×{ppm:g} ≈ {amount:.4f}',
            'fallback_used':False,
        }

    # OpenLux price unavailable -> user's requested ¥1/sec fallback converted to USD.
    fx=fetch_fx_rate(force_fx)
    fallback=_manual_fallback(output_seconds,fx)
    return {
        **base,
        **fallback,
        'model_price':price,
        'fallback_used':True,
        'formula':(
            f'{output_seconds:g}s×¥{fallback["cny_per_second"]:g}/s = ¥{fallback["cost_cny"]:.2f}'
            + (
                f'；¥{fallback["cost_cny"]:.2f}×{fx["rate"]:.6f} ≈ ${fallback["cost_usd"]:.2f}'
                if fallback.get('cost_usd') is not None else
                '；CNY→USD 汇率暂时不可用'
            )
        ),
    }

def live(duration=None,resolution=None,ratio='9:16',input_video_seconds=0.0,force_balance=False,force_price=False):
    cfg=settings.get_all()
    duration=duration if duration is not None else cfg.get('default_video_duration',10)
    resolution=resolution or cfg.get('default_video_resolution','720p')

    balance=fetch_balance(force_balance)
    estimate=estimate_video(
        duration,resolution,ratio,input_video_seconds,
        force_price=force_price,force_fx=force_price,
    )

    remaining=None
    if balance.get('available') is not None and estimate.get('configured') and float(estimate.get('cost') or 0)>0:
        remaining=int(float(balance['available'])//float(estimate['cost']))

    return {
        'balance':balance,
        'estimate':estimate,
        'remaining_conservative':remaining,
        'route':settings.route_info(cfg),
        'fallback_policy':{
            'cny_per_second':float(cfg.get('cost_fallback_cny_per_second') or 1.0),
            'fx_refresh_minutes':int(cfg.get('cost_fx_refresh_minutes') or 30),
            'description':'仅当 OpenLux /api/pricing 无法读取时启用：输出视频秒数 × 人民币/秒 × CNY→USD 最新可用汇率',
        },
    }

# ---------------- Local paid budget ceiling / abnormal-spend breaker ----------------

def paid_budget_status(estimated_new_usd=None):
    """本地付费保护。只统计本机已记录的 usage_events，不是 OpenLux 账单。

    spend_usd 由 actual_cost_usd 优先、缺失时用 estimated_cost_usd 补齐；
    unresolved_tasks 表示「可能已扣费但没有任何金额记录」的付费任务数量。
    """
    cfg=settings.get_all()
    limit_usd=max(0.0,_num(cfg.get('paid_budget_usd')) or 0.0)
    window_h=max(1,int(cfg.get('paid_budget_window_hours') or 24))
    max_tasks=max(0,int(cfg.get('paid_budget_max_tasks') or 0))
    since=time.time()-window_h*3600
    row=db.one(
        '''SELECT COUNT(*) n,
                  COALESCE(SUM(actual_cost_usd),0) actual,
                  COALESCE(SUM(CASE WHEN actual_cost_usd IS NULL THEN estimated_cost_usd END),0) estimated,
                  COUNT(actual_cost_usd) actual_known,
                  COALESCE(SUM(CASE WHEN actual_cost_usd IS NULL AND estimated_cost_usd IS NULL THEN 1 ELSE 0 END),0) unresolved
           FROM usage_events WHERE kind='video' AND created_at>=?''',
        (since,)
    ) or {}
    spend=round(float(row.get('actual') or 0)+float(row.get('estimated') or 0),6)
    tasks=int(row.get('n') or 0)
    new_usd=max(0.0,float(estimated_new_usd or 0))
    projected=round(spend+new_usd,6)
    reasons=[]
    if limit_usd>0 and projected>limit_usd:
        reasons.append(
            f'预算上限 ${limit_usd:g}/{window_h} 小时：本机已记录 ${spend:.2f}（实际+估算），本次预计 ${new_usd:.2f}'
        )
    if max_tasks>0 and tasks>=max_tasks:
        reasons.append(f'付费提交熔断：{window_h} 小时内已有 {tasks} 次付费提交，达到上限 {max_tasks} 次')
    return {
        'blocked':bool(reasons),
        'reasons':reasons,
        'enabled':limit_usd>0 or max_tasks>0,
        'limit_usd':limit_usd,
        'max_tasks':max_tasks,
        'window_hours':window_h,
        'spent_usd':spend,
        'actual_usd':round(float(row.get('actual') or 0),6),
        'estimated_usd':round(float(row.get('estimated') or 0),6),
        'tasks':tasks,
        'actual_known_tasks':int(row.get('actual_known') or 0),
        'unresolved_tasks':int(row.get('unresolved') or 0),
        'estimated_new_usd':new_usd,
        'projected_usd':projected,
        'basis':'本机 usage_events 记录（实际扣费优先，缺失时用估算；不代表 OpenLux 最终账单）',
    }


def clear_balance_cache():
    with _LOCK:
        _BALANCE_CACHE['ts']=0.0
        _BALANCE_CACHE['value']=None

def clear_price_cache():
    with _LOCK:
        _PRICE_CACHE['ts']=0.0
        _PRICE_CACHE['value']=None

def clear_fx_cache():
    with _LOCK:
        _FX_CACHE['ts']=0.0
        _FX_CACHE['value']=None

def clear_cache():
    clear_balance_cache()
    clear_price_cache()
    clear_fx_cache()
