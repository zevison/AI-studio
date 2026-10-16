from __future__ import annotations
from . import settings, tunnel, oss, media_preflight

_selected_provider = None
_last_error = None


def _clone_with_base(specs, base_url):
    out=[]
    for spec in specs:
        a=dict(spec.get('asset') or {})
        a['remote_url']=tunnel.public_asset_url(base_url,a)
        a['remote_provider']='fixed_https' if 'trycloudflare' not in base_url else 'quick_tunnel'
        out.append({'asset':a,'role':spec.get('role')})
    return out


def status() -> dict:
    cfg=settings.get_all()
    strategy=cfg.get('public_asset_strategy','oss_auto')
    fixed=(cfg.get('fixed_public_asset_base_url') or '').strip()
    quick=tunnel.status()
    osst=oss.status()
    return {
        'strategy': strategy,
        'selected_provider': _selected_provider,
        'error': _last_error,
        'oss': osst,
        'fixed_url': fixed or None,
        'quick_tunnel': quick,
        'effective_url': '阿里云 OSS 临时签名链接' if (_selected_provider=='aliyun_oss' or (strategy in ('oss_auto','oss_only') and osst.get('configured'))) else (fixed or quick.get('effective_url')),
    }


def _fixed_prepare(specs, base_url):
    if not specs:
        return {'ok':True,'provider':'fixed_https','specs':[],'checks':[],'base_url':base_url}
    first=specs[0].get('asset') or {}
    test=tunnel.self_test(first,base_url)
    if not test.get('ok'):
        return {'ok':False,'provider':'fixed_https','error':test.get('error') or '固定 HTTPS 自检失败','test':test,'specs':specs}
    return {'ok':True,'provider':'fixed_https','specs':_clone_with_base(specs,base_url),'checks':test.get('checks') or [],'test':test,'base_url':base_url}


def _quick_prepare(specs):
    st=tunnel.start()
    base=st.get('url') if st.get('running') else None
    if not base:
        return {'ok':False,'provider':'quick_tunnel','error':st.get('error') or 'Quick Tunnel 未取得公网地址','specs':specs,'status':st}
    if not specs:
        return {'ok':True,'provider':'quick_tunnel','specs':[],'checks':[],'base_url':base}
    first=specs[0].get('asset') or {}
    test=tunnel.self_test(first,base)
    if not test.get('ok'):
        return {'ok':False,'provider':'quick_tunnel','error':test.get('error') or 'Quick Tunnel 自检失败','test':test,'specs':specs,'status':st}
    return {'ok':True,'provider':'quick_tunnel','specs':_clone_with_base(specs,base),'checks':test.get('checks') or [],'test':test,'base_url':base}


def prepare_specs(specs: list[dict], min_ttl_minutes: int = 0) -> dict:
    global _selected_provider,_last_error

    # Local, zero-cost technical preflight happens before any upload or paid call.
    # It automatically makes reference images/videos comply with Seedance 2.5
    # width/height/aspect constraints, so users never have to enter API parameters.
    preflight=media_preflight.prepare_specs(specs)
    specs=preflight.get('specs') or specs
    auto_fixes=preflight.get('fixes') or []

    cfg=settings.get_all(); strategy=cfg.get('public_asset_strategy','oss_auto')
    fixed=(cfg.get('fixed_public_asset_base_url') or '').strip().rstrip('/')
    attempts=[]

    if not specs:
        _selected_provider='none';_last_error=None
        return {'ok':True,'provider':'none','specs':[],'attempts':[],'auto_fixes':[]}

    if strategy in ('oss_auto','oss_only'):
        if oss.configured():
            try:
                got=oss.prepare_specs(specs, min_ttl_minutes=min_ttl_minutes)
                attempts.append({'provider':'aliyun_oss','ok':True,'checks':got.get('checks')})
                _selected_provider='aliyun_oss';_last_error=None
                got['attempts']=attempts; got['auto_fixes']=auto_fixes
                return got
            except Exception as e:
                attempts.append({'provider':'aliyun_oss','ok':False,'error':str(e)})
                if strategy=='oss_only':
                    _selected_provider=None;_last_error=str(e)
                    return {'ok':False,'provider':'aliyun_oss','error':str(e),'specs':specs,'attempts':attempts}
        else:
            msg='阿里云 OSS 尚未配置完整。请在“状态与设置 → 公网素材”填写 Endpoint、Bucket、AccessKey ID、AccessKey Secret。'
            attempts.append({'provider':'aliyun_oss','ok':False,'error':msg})
            if strategy=='oss_only':
                _selected_provider=None;_last_error=msg
                return {'ok':False,'provider':'aliyun_oss','error':msg,'specs':specs,'attempts':attempts}

    # 自动模式仅降级到正式固定 HTTPS；Quick Tunnel 不再自动参与付费主链路。
    if strategy in ('oss_auto','fixed_only') and fixed:
        got=_fixed_prepare(specs,fixed)
        attempts.append({'provider':'fixed_https','ok':got.get('ok',False),'error':got.get('error'),'test':got.get('test')})
        if got.get('ok'):
            _selected_provider='fixed_https';_last_error=None;got['attempts']=attempts; got['auto_fixes']=auto_fixes
            return got
        if strategy=='fixed_only':
            _selected_provider=None;_last_error=got.get('error')
            got['attempts']=attempts; got['auto_fixes']=auto_fixes
            return got

    if strategy=='quick_only':
        got=_quick_prepare(specs)
        attempts.append({'provider':'quick_tunnel','ok':got.get('ok',False),'error':got.get('error'),'test':got.get('test')})
        if got.get('ok'):
            _selected_provider='quick_tunnel';_last_error=None;got['attempts']=attempts; got['auto_fixes']=auto_fixes
            return got
        _selected_provider=None;_last_error=got.get('error');got['attempts']=attempts; got['auto_fixes']=auto_fixes
        return got

    _selected_provider=None
    _last_error='没有可用的公网素材通道。推荐先配置阿里云 OSS；自动模式不会再使用 Quick Tunnel 进行付费提交。'
    return {'ok':False,'provider':None,'error':_last_error,'specs':specs,'attempts':attempts}


def self_test(asset: dict|None=None) -> dict:
    cfg=settings.get_all(); strategy=cfg.get('public_asset_strategy','oss_auto')
    fixed=(cfg.get('fixed_public_asset_base_url') or '').strip().rstrip('/')
    if strategy in ('oss_auto','oss_only'):
        if not oss.configured():
            if strategy=='oss_only' or not fixed:
                return {'ok':False,'provider':'aliyun_oss','checks':[],'error':'阿里云 OSS 尚未配置完整。'}
        else:
            if asset:
                try:
                    info=oss.prepare_asset(asset)
                    return {'ok':True,'provider':'aliyun_oss','checks':[{'name':'OSS 实际素材 GET','ok':True,'required':True,'status':info['test'].get('status'),'bytes_read':info['test'].get('bytes_read')}],
                            'asset_url':info['url'],'object_key':info['object_key'],'uploaded':info.get('uploaded',False)}
                except Exception as e:
                    if strategy=='oss_only' or not fixed:
                        return {'ok':False,'provider':'aliyun_oss','checks':[],'error':str(e)}
            else:
                tst=oss.test_connection(True)
                if tst.get('ok'):
                    return {'ok':True,'provider':'aliyun_oss','checks':tst.get('checks') or [],'endpoint':tst.get('endpoint'),'bucket':tst.get('bucket')}
                if strategy=='oss_only' or not fixed:
                    return {'ok':False,'provider':'aliyun_oss','checks':tst.get('checks') or [],'error':tst.get('error')}
    if strategy in ('oss_auto','fixed_only') and fixed:
        t=tunnel.self_test(asset,fixed)
        return {**t,'provider':'fixed_https'}
    if strategy=='quick_only':
        got=_quick_prepare([{'asset':asset,'role':'reference_video'}] if asset else [])
        return {'ok':got.get('ok',False),'provider':'quick_tunnel','checks':(got.get('test') or {}).get('checks',[]),'error':got.get('error'),'base_url':got.get('base_url')}
    return {'ok':False,'provider':None,'checks':[],'error':'没有配置可用公网素材通道。'}
