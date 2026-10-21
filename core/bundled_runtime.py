"""Locally provision one verified archive; no service configuration or network imports."""
import json
import secrets
import sys
from pathlib import Path
from .component_download import digest
from .component_locks import file_lease
from .runtime_layout import native_path


def install(root,home,kind,item):
    if kind not in {'sdk','browser'} or item.get('file')!=kind+'-payload.zip':raise ValueError('无效的内置运行环境')
    root,home=native_path(root),native_path(home)
    archive=root/'tools'/item['file']
    destination=home/'assets/hypit-host'/(kind+'-'+item['sha256'][:12])
    lease=home/'components/leases'/(kind+'-bundle.lock')
    for path in (archive,destination,lease,*lease.parents):
        if path.is_symlink() or getattr(path,'is_junction',lambda:False)():raise PermissionError('内置组件路径包含链接')
    sys.path.insert(0,str(root/'tools'))
    from install_hypit_bundle import extract_verified
    with file_lease(lease):
        try:return extract_verified(archive,destination,item['sha256'])
        except RuntimeError:
            if not destination.exists() or digest(archive)!=item['sha256']:raise
            quarantine=destination.with_name(destination.name+'.damaged-'+secrets.token_hex(6))
            destination.rename(quarantine)
            extract_verified(archive,destination,item['sha256'])
            report=home/'runtime-recovery.json'
            report.write_text(json.dumps({'component':kind,'preserved':str(quarantine),
                'status':'restored-from-verified-bundle'}),encoding='utf8')
            return 'recovered'
