"""Resolve immutable bundled runtime locations; development trees use local tools."""
import json
from pathlib import Path
import re
from . import config, profile_store


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
    return base/('sdk-'+hashes[0][:12]),base/('browser-'+hashes[1][:12])
