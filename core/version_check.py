"""Lightweight remote version check for Seedance Studio.

Fetches latest-release.json from VERSION_CHECK_URL (env) and compares
against the local APP_VERSION + APP_BUILD. No TUF trust root, no Authenticode,
no auto-install — just a comparison + reminder for the user.

To enable:
  1. Set VERSION_CHECK_URL in your .env, e.g.:
     VERSION_CHECK_URL=https://raw.githubusercontent.com/YOUR/REPO/main/docs/latest-release.json
  2. Push docs/latest-release.json to GitHub with every release.
"""
from __future__ import annotations
import json
import os
import re
import time
import urllib.request
import urllib.error
from . import config


def _version_tuple(v: str):
    """Parse '5.2.0' → (5,2,2). Returns None if malformed."""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r'(\d{1,5})\.(\d{1,5})\.(\d{1,5})', v.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _build_key(build: str):
    """Normalize build like 'R3.18-plugins-candidate' → 'R3.18'."""
    if not isinstance(build, str):
        return ''
    m = re.match(r'(R\d+\.\d+)', build)
    return m.group(1) if m else build.strip()


def check_remote(timeout: int = 8) -> dict:
    """Fetch the remote latest-release.json and compare with local version.

    Returns a dict with keys:
      - local_version, local_build: current running version
      - remote_version, remote_build, remote_channel: from remote JSON (on success)
      - update_available: bool
      - release_notes, installer_url, release_date (on success)
      - checked_at: unix timestamp
      - error: str on failure, None on success
      - configured: bool — whether VERSION_CHECK_URL is set
    """
    result = {
        'local_version': config.APP_VERSION,
        'local_build': config.APP_BUILD,
        'checked_at': int(time.time()),
        'update_available': False,
        'error': None,
        'configured': False,
    }
    url = os.getenv('VERSION_CHECK_URL', '').strip()
    if not url:
        result['error'] = 'VERSION_CHECK_URL not configured'
        return result
    result['configured'] = True

    # Only allow http(s); reject file:// or other schemes.
    if not url.startswith(('http://', 'https://')):
        result['error'] = 'VERSION_CHECK_URL must be http(s)://'
        return result

    try:
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'SeedanceStudio-VersionCheck/1', 'Accept': 'application/json'},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(65536).decode('utf-8', errors='replace')
        doc = json.loads(body)
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
        return result

    rv = doc.get('version', '')
    rb = doc.get('build', '')
    result['remote_version'] = rv
    result['remote_build'] = rb
    result['remote_channel'] = doc.get('channel', 'stable')
    result['release_notes'] = doc.get('release_notes', '')
    result['installer_url'] = doc.get('installer_url', '')
    result['release_date'] = doc.get('release_date', '')

    local_ver = _version_tuple(config.APP_VERSION)
    remote_ver = _version_tuple(rv)
    local_build = _build_key(config.APP_BUILD)
    remote_build = _build_key(rb)

    if remote_ver and local_ver:
        if remote_ver > local_ver:
            result['update_available'] = True
        elif remote_ver == local_ver and remote_build and local_build and remote_build != local_build:
            # Same numeric version but newer build (e.g. 5.2.0 R3.18 vs R3.17)
            result['update_available'] = remote_build != local_build
    return result
