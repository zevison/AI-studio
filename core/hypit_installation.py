"""Resolve immutable bundled runtime locations; development trees use local tools."""
import json
from pathlib import Path
import re
import threading
from . import config, profile_store

_sdk_ready=set()
_sdk_lock=threading.Lock()


def ensure_sdk():
    manifest=config.ROOT/'tools/hypit_bundle.json'
    if not manifest.is_file():return
    document=json.loads(manifest.read_text('utf8'))
    if document.get('format')!='seedance.hypit-bundle@1':raise PermissionError('未知的 Hypit 内置环境清单')
    if document.get('sdk_lazy') is not True:return
    item=document['sdk']
    if not re.fullmatch('[a-f0-9]{64}',item.get('sha256','')):raise PermissionError('Hypit 内置环境身份无效')
    key=(str(config.ROOT),str(profile_store.BASE_DIR),item['sha256'])
    with _sdk_lock:
        if key not in _sdk_ready:
            from .bundled_runtime import install
            install(config.ROOT,profile_store.BASE_DIR,'sdk',item)
            _sdk_ready.add(key)


def runtime_paths():
    manifest=config.ROOT/'tools/hypit_bundle.json'
    if not manifest.exists():
        return config.ROOT/'tools/hypit_provider',config.ROOT/'tools/hypit_browser'
    doc=json.loads(manifest.read_text(encoding='utf8'))
    if doc.get('format')!='seedance.hypit-bundle@1':raise PermissionError('Unknown bundled runtime manifest')
    hashes=[doc[name]['sha256'] for name in ('sdk','browser')]
    if any(not isinstance(h,str) or not re.fullmatch('[0-9a-f]{64}',h) for h in hashes):
        raise PermissionError('Invalid bundled runtime identity')
    base=profile_store.BASE_DIR/'assets/hypit-host'
    if doc['browser'].get('deferred') is True:
        from .component_manager import catalog,embedded_catalog,location
        original=embedded_catalog(config.ROOT).get('browser')
        if not original or original['files'][0]['sha256']!=hashes[1]:raise PermissionError('Deferred browser identity mismatch')
        item=catalog(config.ROOT,home=config.USER_HOME).get('browser')
        return base/('sdk-'+hashes[0][:12]),location(config.USER_HOME,'browser',item)
    return base/('sdk-'+hashes[0][:12]),base/('browser-'+hashes[1][:12])


def browser_configuration(cache):
    """Exactly one official browser selection method, derived from the build lock."""
    custom=getattr(config,'STARTUP_OPTIONS',{}).get('chromium_path')
    if custom:return {'chromePath':custom}
    lock=config.ROOT/'tools/windows-runtime.lock.json'
    if lock.is_file():
        item=json.loads(lock.read_text(encoding='utf8'))['browser']
        version=item.get('version') or item['cache_root'].rsplit('win64-',1)[-1]
        from .component_manager import catalog
        selected=catalog(config.ROOT,home=config.USER_HOME).get('browser')
        if selected:version=selected['version']
    else:
        # Unbundled test/development trees can supply the official recommendation.
        version='152.0.7928.2'
    if not re.fullmatch(r'\d+\.\d+\.\d+\.\d+',version):raise ValueError('Invalid browser runtime version')
    # Keep extended paths for Python IO, but serialize local drive paths in the
    # existing Runtime contract (which correctly rejects network-share paths).
    cache_text=str(cache)
    if cache_text.startswith('\\\\?\\') and not cache_text.startswith('\\\\?\\UNC\\'):
        cache_text=cache_text[4:]
    return {'browserVersion':version,'browserCacheDirectory':cache_text}
