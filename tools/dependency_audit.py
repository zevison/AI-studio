"""Read-only dependency inventory and isolated candidate verification.

No installation, production database migration, automatic upgrade or promotion.
Network access is opt-in and limited to public official metadata endpoints.
"""
from __future__ import annotations
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request

ALLOWLIST = {
    'python': {'www.python.org', 'python.org', 'pypi.org', 'files.pythonhosted.org', 'devguide.python.org'},
    'node': {'nodejs.org', 'raw.githubusercontent.com'},
    'npm': {'registry.npmjs.org'},
    'browser': {'googlechromelabs.github.io', 'storage.googleapis.com', 'chromereleases.googleblog.com'},
    'ffmpeg': {'ffmpeg.org', 'www.ffmpeg.org', 'www.gyan.dev'},
    'webview2': {'learn.microsoft.com', 'developer.microsoft.com', 'msedge.sf.dl.delivery.mp.microsoft.com'},
}
REQUIRED_GATES = ('hashes', 'licenses', 'security', 'startup', 'configuration', 'database',
                  'sdk', 'local_media', 'clean_windows', 'restart', 'upgrade_rollback_uninstall')

def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()

def validate_source(url, family):
    parsed = urllib.parse.urlsplit(str(url))
    if (family not in ALLOWLIST or parsed.scheme != 'https' or parsed.username or parsed.password
        or parsed.hostname not in ALLOWLIST[family] or parsed.port not in (None, 443)
        or parsed.fragment or '\\' in str(url) or any(ord(c) < 33 for c in str(url))):
        raise ValueError('Dependency source is outside the HTTPS allowlist')
    if parsed.hostname == 'storage.googleapis.com' and not parsed.path.startswith('/chrome-for-testing-public/'):
        raise ValueError('Unapproved Google Storage bucket')
    if parsed.hostname == 'raw.githubusercontent.com' and not parsed.path.startswith('/nodejs/Release/'):
        raise ValueError('Unapproved repository metadata')
    return str(url)

def verify_artifact(path, record, family):
    validate_source(record['url'], family)
    expected = record.get('sha256', '')
    if not re.fullmatch('[a-fA-F0-9]{64}', expected): raise ValueError('SHA256 pin is required')
    actual = sha256_file(path)
    if actual.lower() != expected.lower(): raise ValueError('Dependency SHA256 mismatch')
    return {'status': 'PASS', 'sha256': actual, 'bytes': Path(path).stat().st_size}

class _MetadataRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, family): self.family = family
    def redirect_request(self, request, response, code, message, headers, new_url):
        validate_source(new_url, self.family)
        return super().redirect_request(request, response, code, message, headers, new_url)

def fetch_metadata(url, family, *, max_bytes=8 * 1024 * 1024):
    validate_source(url, family)
    opener = urllib.request.build_opener(_MetadataRedirect(family))
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'SeedanceDependencyAudit/1'}), timeout=30) as response:
        validate_source(response.geturl(), family)
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes: raise ValueError('Metadata exceeded size limit')
    return data

def python_inventory():
    items = []
    for distribution in importlib.metadata.distributions():
        direct = distribution.read_text('direct_url.json')
        provenance = None
        if direct:
            value = json.loads(direct)
            # Absolute local installation paths are evidence, not shareable SBOM.
            url = value.get('url', '')
            provenance = {'kind': 'local' if url.startswith('file:') else 'remote',
                          'archive_info': value.get('archive_info', {}), 'vcs_info': value.get('vcs_info', {})}
        items.append({'name': distribution.metadata['Name'], 'version': distribution.version,
                      'requires': distribution.requires or [], 'direct_provenance': provenance})
    return {'python': sys.version.split()[0], 'platform': sys.platform, 'packages': sorted(items, key=lambda x: x['name'].lower()),
            'hash_lock': False, 'note': 'Installed versions alone do not prove artifact hashes or reproducibility'}

def npm_inventory(path):
    document = json.loads(Path(path).read_text(encoding='utf-8'))
    if document.get('lockfileVersion') != 3: raise ValueError('Expected npm lockfileVersion 3')
    rows, problems = [], []
    for name, item in document.get('packages', {}).items():
        if not name: continue
        source, integrity = item.get('resolved', ''), item.get('integrity', '')
        if item.get('link'):
            if Path(source).is_absolute() or '..' in Path(source).parts: problems.append(name + ': unsafe local link')
        elif source:
            try: validate_source(source, 'npm')
            except ValueError: problems.append(name + ': unapproved registry URL')
            try:
                algorithm, digest = integrity.split('-', 1)
                decoded = base64.b64decode(digest, validate=True)
                if algorithm not in ('sha512', 'sha256') or len(decoded) not in (32, 64): raise ValueError()
            except (ValueError, TypeError): problems.append(name + ': missing/invalid integrity')
        rows.append({'path': name, 'version': item.get('version'), 'source': source,
                     'integrity': integrity, 'dev': bool(item.get('dev')), 'optional': bool(item.get('optional'))})
    return {'status': 'PASS' if not problems else 'FAIL', 'lock_sha256': sha256_file(path), 'packages': rows, 'problems': problems}

def verify_wheelhouse(manifest, wheelhouse):
    """Require complete name/version/hash entries; never trust pip freeze alone."""
    manifest = json.loads(Path(manifest).read_text(encoding='utf-8')) if not isinstance(manifest, dict) else manifest
    records = manifest.get('packages', [])
    if not records or manifest.get('complete') is not True: raise ValueError('Python hash lock is incomplete')
    seen = set()
    for record in records:
        name = record['name'].lower().replace('_', '-')
        if name in seen: raise ValueError('Duplicate dependency in hash lock')
        seen.add(name)
        filename = record['filename']
        if Path(filename).name != filename or not filename.endswith('.whl'): raise ValueError('Unsafe wheel filename')
        verify_artifact(Path(wheelhouse) / filename, record, 'python')
    return {'status': 'PASS', 'count': len(records), 'installation_test': 'NOT TESTED by artifact verification'}

def candidate_decision(manifest):
    """A candidate never becomes the known-good/stable baseline automatically."""
    if manifest.get('channel') != 'candidate': raise ValueError('Expected isolated candidate metadata')
    missing = [key for key in REQUIRED_GATES if manifest.get('gates', {}).get(key) != 'PASS']
    return {'eligible_for_human_promotion': not missing, 'automatic_promotion': False,
            'missing_gates': missing, 'stable_baseline_unchanged': True}

def probe_candidate_database(original, candidate_directory, command, *, timeout=30):
    """Run only against an online-backup copy; never replace the original DB.

    Caller supplies an explicit test command. A dependency crash/timeout or
    forward schema upgrade leaves the original WAL database untouched.
    """
    original = Path(original).resolve(); destination = Path(candidate_directory).resolve()
    if destination == original.parent or original.is_relative_to(destination):
        raise ValueError('Candidate directory must be separate from original data')
    destination.mkdir(parents=True, exist_ok=True)
    copied = destination / 'candidate.db'
    if copied.exists(): raise ValueError('Refusing to overwrite a previous candidate snapshot')
    before = None
    with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source:
        if source.execute('PRAGMA integrity_check').fetchone()[0] != 'ok': raise ValueError('Original database is not healthy')
        before = list(source.iterdump())
        with closing(sqlite3.connect(copied)) as target: source.backup(target)
    env = dict(os.environ); env['SEEDANCE_CANDIDATE_DB'] = str(copied)
    try:
        process = subprocess.run(command, cwd=destination, env=env, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, timeout=timeout, check=False)
        status, exit_code, output = ('PASS' if process.returncode == 0 else 'FAIL'), process.returncode, process.stdout
    except subprocess.TimeoutExpired as error:
        status, exit_code, output = 'FAIL', None, error.stdout or b''
    (destination / 'candidate-startup.log').write_bytes(output)
    with closing(sqlite3.connect(original.as_uri() + '?mode=ro', uri=True)) as source:
        unchanged = before == list(source.iterdump()) and source.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
    if not unchanged: raise ValueError('Original database changed during isolated candidate test')
    return {'status': status, 'exit_code': exit_code, 'original_unchanged': True,
            'stable_baseline_unchanged': True, 'candidate_database': str(copied),
            'real_installer_rollback': 'NOT TESTED'}

def upstream_metadata():
    sources = {
        'node_releases': ('node', 'https://nodejs.org/dist/index.json'),
        'node_lifecycle': ('node', 'https://raw.githubusercontent.com/nodejs/Release/main/schedule.json'),
        'chrome_channels': ('browser', 'https://googlechromelabs.github.io/chrome-for-testing/last-known-good-versions-with-downloads.json'),
        'pyinstaller': ('python', 'https://pypi.org/pypi/pyinstaller/json'),
        'python_lifecycle': ('python', 'https://devguide.python.org/versions/'),
        'ffmpeg_security': ('ffmpeg', 'https://ffmpeg.org/security.html'),
        'webview2_lifecycle': ('webview2', 'https://learn.microsoft.com/en-us/deployedge/microsoft-edge-support-lifecycle'),
        'webview2_fixed_packages': ('webview2', 'https://developer.microsoft.com/en-us/microsoft-edge/webview2'),
    }
    result = {}
    for name, (family, url) in sources.items():
        try:
            raw = fetch_metadata(url, family)
            data = json.loads(raw) if name in ('node_releases', 'node_lifecycle', 'chrome_channels', 'pyinstaller') else None
            if name == 'node_releases': data = [{'version': x['version'], 'date': x['date'], 'lts': x['lts'], 'security': x.get('security')} for x in data[:30]]
            if name == 'pyinstaller': data = {'version': data['info']['version'], 'requires_python': data['info']['requires_python']}
            if name == 'webview2_fixed_packages':
                text = raw.decode('utf-8').replace('\\u002F', '/')
                data = sorted(set(re.findall(r'https://msedge\.sf\.dl\.delivery\.mp\.microsoft\.com/filestreamingservice/files/[^\s"<>]+\.x64\.cab', text)))
            result[name] = {'status': 'PASS', 'url': url, 'metadata_sha256': hashlib.sha256(raw).hexdigest(), 'data': data,
                            'security_clearance': 'NOT TESTED; metadata retrieval is not a vulnerability scan'}
        except Exception as error:
            result[name] = {'status': 'BLOCKED', 'url': url, 'error_type': type(error).__name__}
    return result

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--refresh', action='store_true', help='Read allowlisted public upstream metadata; never install/update')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report = {'schema': 'seedance.dependency-audit@1', 'checked_at_utc': datetime.now(timezone.utc).isoformat(),
              'stable_baseline': None, 'release_candidate': 'R3.9; not a stable release',
              'automatic_upgrade': False, 'python': python_inventory(),
              'npm': npm_inventory(root / 'tools/hypit_provider/package-lock.json'),
              'runtime': json.loads((root / 'tools/windows-runtime.lock.json').read_text(encoding='utf-8')),
              'vulnerability_scan': 'NOT TESTED by this inventory command; run pip-audit and npm audit separately',
              'remote_ci': 'NOT TESTED; local execution is not evidence of GitHub workflow execution'}
    if args.refresh: report['upstream'] = upstream_metadata()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output': str(args.output), 'npm_integrity': report['npm']['status'], 'automatic_upgrade': False}))
    if args.refresh and any(item.get('status') != 'PASS' for item in report.get('upstream', {}).values()): return 2
    return 0 if report['npm']['status'] == 'PASS' else 1

if __name__ == '__main__': raise SystemExit(main())
