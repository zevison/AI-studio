r"""Lightweight remote version check for Seedance Studio.

Fetches latest-release.json from VERSION_CHECK_URL (env) and compares
against the local APP_VERSION + APP_BUILD. No TUF trust root, no Authenticode,
no auto-install — just a comparison + reminder for the user.

Defaults to the official public release feed (see DEFAULT_URL). Override with
VERSION_CHECK_URL, or set VERSION_CHECK_URL=off to disable outbound requests.
Note: a packaged EXE reads .env from %LOCALAPPDATA%\SeedanceStudio, not the
install folder.
"""
from __future__ import annotations
import json
import os
import re
import time
import urllib.request
import urllib.error
from . import config

# Public endpoint of the official release feed. VERSION_CHECK_URL overrides it;
# VERSION_CHECK_URL=off disables the outbound request entirely.
DEFAULT_URL = 'https://raw.githubusercontent.com/zevison/AI-studio/master/docs/latest-release.json'


def _version_tuple(v: str):
    """Parse '5.2.0' → (5,2,0). Returns None if malformed."""
    if not isinstance(v, str):
        return None
    m = re.fullmatch(r'(\d{1,5})\.(\d{1,5})\.(\d{1,5})', v.strip())
    return tuple(int(x) for x in m.groups()) if m else None


def _build_key(build: str):
    """Parse build like 'R3.18-plugins-candidate' → (3,18). None if not a R<major>.<minor> tag."""
    if not isinstance(build, str):
        return None
    m = re.match(r'\s*R(\d{1,5})\.(\d{1,5})', build)
    return (int(m.group(1)), int(m.group(2))) if m else None


def _parse_feed(body: str) -> dict:
    """Parse the release feed; tolerate a stray UTF-8 BOM or a PowerShell '@{...}' prefix."""
    text = body.lstrip('\ufeff').strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        if text.startswith('@'):
            return json.loads(text[1:])
        raise


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
    url = os.getenv('VERSION_CHECK_URL', '').strip() or DEFAULT_URL
    if url.lower() in ('0', 'off', 'false', 'none'):
        result['error'] = 'VERSION_CHECK_URL disabled'
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
        doc = _parse_feed(body)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        # Covers URLError/HTTPError/TimeoutError/ConnectionResetError and bad JSON.
        result['error'] = f'{type(exc).__name__}: {exc}'
        return result

    if not isinstance(doc, dict):
        result['error'] = 'Release feed is not a JSON object'
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
        elif remote_ver == local_ver and remote_build and local_build:
            # Same numeric version: only a strictly newer build is an update.
            # Older remote builds must never be offered (that would be a downgrade).
            result['update_available'] = remote_build > local_build
    return result
