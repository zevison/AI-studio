"""Hypit/OpenLux START-POLL-COLLECT bridge, developing against Hypit 0.3.1.

【注意/Risk】Not an activated @hypit/hypit/endpoint package. OpenLux video
endpoints/account-specific request and pricing are UNVERIFIED. No live paid
request is authorized by default. Official SDK adapter still needs compilation
and Runtime verification under the pinned Hypit release.

The only public orchestration path uses a trusted, bounded approval issued by
backend policy. NEVER deserialize `SpendEnvelope` from an untrusted HTTP body.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import tempfile
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable

from .hypit_openlux_gateway import (
    ContractError, DurableVideoGateway, ExplicitApproval, SubmissionUnknown,
    _external_https, request_sha256,
)

_ALLOWED_BASE = 'api.openlux.ai'
_MAX_HTTP_RESPONSE = 256 * 1024
_MAX_VIDEO = 400 * 1024 * 1024
_ID_RE = re.compile(r'^[A-Za-z0-9_.:-]{1,256}$')


class ContractNotVerified(PermissionError):
    """Default denial until the actual vendor protocol is confirmed."""


class MediaFailure(RuntimeError):
    """Downloaded bytes are not a verified, usable MP4 artifact."""


@dataclass(frozen=True)
class VerifiedRoute:
    """Only backend provisioner may create a production route.

    Routing is literal and fixed per account. No fallback, redirect or
    auto-adaptation to alternate OpenLux paths. The default route is UNVERIFIED.
    """
    submit_path: str = '/api/v3/contents/generations/tasks'
    query_path: str = '/api/v3/contents/generations/tasks/{task_id}'
    payload_shape: str = 'direct'
    account_protocol_verified: bool = False

    def __post_init__(self):
        for path in (self.submit_path, self.query_path):
            if (not isinstance(path, str) or not path.startswith('/') or
                path.startswith('//') or len(path) > 240 or '?' in path or '#' in path or
                '\\' in path or '..' in path):
                raise ValueError('Route must be a fixed, local API path')
        if self.query_path.count('{task_id}') != 1:
            raise ValueError('Query route requires exactly one task_id placeholder')
        if '{' in self.submit_path or '}' in self.submit_path:
            raise ValueError('Submit route cannot contain placeholders')
        if self.payload_shape not in {'direct', 'metadata'}:
            raise ValueError('Unknown OpenLux payload wrapper')


@dataclass(frozen=True)
class SpendEnvelope:
    """Internal-only authorization after verified pricing and consent."""
    account_id: str
    request_hash: str
    quoted_usd: str
    limit_usd: str
    issued_at_epoch: float
    expires_at_epoch: float
    contract_verified: bool = False
    approved_by_user: bool = False

    def authorizes(self, account: str, payload: dict, now: float) -> bool:
        try:
            quote = Decimal(self.quoted_usd)
            limit = Decimal(self.limit_usd)
        except (InvalidOperation, TypeError, ValueError):
            return False
        return (self.account_id == account and
                self.request_hash == request_sha256(payload) and
                self.contract_verified and self.approved_by_user and
                quote.is_finite() and limit.is_finite() and
                Decimal('0') < quote <= limit and Decimal('0') < limit and
                isinstance(self.issued_at_epoch, (float, int)) and
                isinstance(self.expires_at_epoch, (float, int)) and
                self.issued_at_epoch <= now < self.expires_at_epoch <= self.issued_at_epoch + 3600)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        raise ContractError('HTTP redirects forbidden for paid or signed media URL')


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """Resolve once, dial the verified numeric address, retain original TLS/Host identity."""
    def __init__(self, host, *, pinned_host, addresses, **kwargs):
        super().__init__(host, **kwargs)
        if self.host != pinned_host or self.port != 443:
            raise MediaFailure('Connection destination differs from verified CDN')
        self._addresses = addresses

    def connect(self):
        last_error = None
        for address in self._addresses:
            sock = None
            try:
                family = socket.AF_INET6 if ':' in address else socket.AF_INET
                sock = socket.socket(family, socket.SOCK_STREAM)
                sock.settimeout(self.timeout)
                sock.connect((address, 443))
                self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
                return
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                if sock is not None:
                    sock.close()
        raise MediaFailure('Verified CDN TLS connection failed') from last_error


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, host, addresses):
        super().__init__(context=ssl.create_default_context())
        self.host, self.addresses = host, addresses

    def https_open(self, request):
        return self.do_open(lambda host, **kw: _PinnedHTTPSConnection(
            host, pinned_host=self.host, addresses=self.addresses, **kw),
            request, context=self._context)


def _bound_json(response) -> dict:
    size = 0
    chunks = []
    while True:
        part = response.read(min(64 * 1024, _MAX_HTTP_RESPONSE + 1 - size))
        if not part:
            break
        chunks.append(part)
        size += len(part)
        if size > _MAX_HTTP_RESPONSE:
            raise ContractError('OpenLux JSON response exceeds bounded limit')
    try:
        doc = json.loads(b''.join(chunks).decode('utf-8'))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ContractError('Invalid OpenLux JSON response') from exc
    if not isinstance(doc, dict):
        raise ContractError('OpenLux response root must be object')
    return doc


class OpenLuxHttpTransport:
    """Literal, non-retrying vendor wire transport. Never upload private files."""
    def __init__(self, api_key: str, route: VerifiedRoute, *, opener=None):
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError('API key must come from backend encrypted secrets store')
        self.__api_key = api_key
        self.route = route
        self.__opener = opener or urllib.request.build_opener(_NoRedirect())

    def _call(self, method: str, path: str, payload: dict | None, timeout: float):
        if not self.route.account_protocol_verified:
            raise ContractNotVerified('Vendor account/API contract must be verified first')
        if not (1 <= timeout <= 120):
            raise ValueError('HTTP timeout out of bounds')
        url = 'https://' + _ALLOWED_BASE + path
        data = None
        if payload is not None:
            if self.route.payload_shape == 'metadata':
                # Vendor-specific wrapper must be tested before this route can be enabled.
                wire = {'model': payload['model'], 'prompt': payload['content'][0]['text'],
                        'metadata': {k:v for k,v in payload.items() if k != 'model'}}
            else:
                wire = payload
            data = json.dumps(wire, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        req = urllib.request.Request(url, data=data, method=method,
              headers={'Authorization': 'Bearer ' + self.__api_key,
                       'Content-Type': 'application/json', 'Accept': 'application/json',
                       'User-Agent': 'SeedanceStudio-Hypit-Provider-DEV/0.1'})
        try:
            with self.__opener.open(req, timeout=timeout) as resp:
                if not 200 <= resp.status < 300:
                    raise ContractError(f'Unexpected HTTP {resp.status}')
                return _bound_json(resp)
        except urllib.error.HTTPError as exc:
            # Never stringify body/headers to logs; might contain signed URLs or secrets.
            raise ContractError(f'OpenLux HTTP error {exc.code}; action needs review') from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # The caller must quarantine the POST. No retry here, even on HTTP 5xx.
            raise ConnectionError('OpenLux network outcome uncertain') from exc

    def post(self, payload: dict, *, timeout: float) -> dict:
        return self._call('POST', self.route.submit_path, payload, timeout)

    def get(self, task_id: str, *, timeout: float) -> dict:
        if not _ID_RE.fullmatch(task_id):
            raise ContractError('Invalid task identifier')
        path = self.route.query_path.replace('{task_id}', urllib.parse.quote(task_id, safe=''))
        return self._call('GET', path, None, timeout)


class SafeMp4Collector:
    """Collect a signed MP4 URL only from an explicitly trusted CDN host.

    Default transport pins the verified public addresses while preserving TLS SNI
    and certificate checking. Redirects and proxy re-resolution are forbidden.
    Injected openers are reserved for trusted offline tests.
    """
    def __init__(self, cdn_hosts: set[str], *, opener=None, max_bytes: int = _MAX_VIDEO,
                 resolver: Callable | None = None, video_probe: Callable | None = None):
        self.hosts = set(cdn_hosts)
        if not self.hosts or any(not isinstance(x, str) or not x or x.lower() != x
                                 or '.' not in x or '*' in x for x in self.hosts):
            raise ValueError('Explicit lowercase CDN allowlist required')
        if type(max_bytes) is not int or not (1024 <= max_bytes <= _MAX_VIDEO):
            raise ValueError('Invalid maximum download bytes')
        self.max_bytes = max_bytes
        self._opener = opener
        self._resolver = resolver or socket.getaddrinfo
        self._probe = video_probe or self._ffprobe

    @staticmethod
    def _ffprobe(path: str) -> bool:
        """Check MP4 video stream and real duration without executing shell."""
        try:
            p = subprocess.run(['ffprobe', '-v', 'error', '-show_streams',
                      '-show_format', '-of', 'json', path],
                      capture_output=True, text=True, timeout=20, check=False)
            if p.returncode != 0 or len(p.stdout) > 500000:
                return False
            doc = json.loads(p.stdout)
            streams = doc.get('streams') or []
            duration = float((doc.get('format') or {}).get('duration') or 0)
            return (duration > 0 and duration < 24 * 60 * 60 and
                    isinstance(streams, list) and
                    any(x.get('codec_type') == 'video' for x in streams if isinstance(x, dict)))
        except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, json.JSONDecodeError):
            return False

    def _verify_host(self, url: str):
        _external_https(url, 'signed video result')
        host = urllib.parse.urlsplit(url).hostname
        if host not in self.hosts:
            raise MediaFailure('Result host not in trusted CDN allowlist')
        try:
            addresses = self._resolver(host, 443, proto=socket.IPPROTO_TCP)
        except OSError as exc:
            raise MediaFailure('CDN DNS resolution failed') from exc
        if not addresses:
            raise MediaFailure('CDN hostname has no resolved addresses')
        for result in addresses:
            address = result[4][0].split('%')[0]
            if not ipaddress.ip_address(address).is_global:
                raise MediaFailure('CDN DNS resolved to nonpublic address')
        return host, tuple(dict.fromkeys(result[4][0].split('%')[0] for result in addresses))

    def collect(self, url: str, destination: Path) -> dict:
        verified = self._verify_host(url)
        opener = self._opener
        if opener is None:
            host, addresses = verified
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}),
                _NoRedirect(), _PinnedHTTPSHandler(host, addresses))
        dest = Path(destination)
        if dest.suffix.lower() != '.mp4' or dest.is_symlink():
            raise MediaFailure('Destination must be a regular MP4 path')
        if not dest.parent.is_dir() or dest.exists():
            raise MediaFailure('Destination directory missing or output already exists')
        fd, temp_name = tempfile.mkstemp(prefix='.hypit_download_', suffix='.part', dir=str(dest.parent))
        written, digest = 0, hashlib.sha256()
        try:
            # Signed URL: NO OpenLux Authorization or cookies forwarded.
            req = urllib.request.Request(url, headers={'Accept': 'video/mp4',
                                        'User-Agent': 'SeedanceStudio-Hypit-Collector/0.1'})
            deadline = time.monotonic() + 120
            with opener.open(req, timeout=45) as response, os.fdopen(fd, 'wb') as out:
                fd = -1
                if response.status != 200:
                    raise MediaFailure(f'Unexpected download HTTP {response.status}')
                media_type = response.headers.get('Content-Type', '').split(';')[0].strip().lower()
                if media_type not in {'video/mp4', 'application/mp4', 'application/octet-stream'}:
                    raise MediaFailure('Result Content-Type is not MP4-compatible')
                declared = response.headers.get('Content-Length')
                if declared is not None:
                    try:
                        if int(declared) > self.max_bytes or int(declared) <= 0:
                            raise MediaFailure('Video declared length exceeds limit')
                    except ValueError as exc:
                        raise MediaFailure('Invalid Content-Length') from exc
                head = b''
                while True:
                    if time.monotonic() > deadline:
                        raise MediaFailure('Download exceeded total time budget')
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > self.max_bytes:
                        raise MediaFailure('Video exceeds bounded download limit')
                    digest.update(chunk)
                    if len(head) < 32:
                        head += chunk[:32 - len(head)]
                    out.write(chunk)
                out.flush()
                os.fsync(out.fileno())
            if declared is not None and written != int(declared):
                raise MediaFailure('Downloaded bytes do not match Content-Length')
            if written < 16 or head[4:8] != b'ftyp':
                raise MediaFailure('Video is not an MP4 file (ftyp signature missing)')
            if self._probe(temp_name) is not True:
                raise MediaFailure('Video failed ffprobe playable-stream verification')
            # Exclusive create: never overwrite an existing file, even if it appears
            # between the preflight and the final publish. Hard link is atomic.
            os.link(temp_name, dest)
            return {'size': written, 'sha256': digest.hexdigest(), 'path': str(dest)}
        except FileExistsError as exc:
            raise MediaFailure('Destination concurrently created; not overwritten') from exc
        except (OSError, urllib.error.URLError) as exc:
            raise MediaFailure('Video download or write failed') from exc
        finally:
            if fd >= 0:
                os.close(fd)
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


class ProviderLifecycle:
    """SDK-independent bridge. Not registered as a Hypit Provider yet."""
    def __init__(self, ledger: DurableVideoGateway, collector: SafeMp4Collector,
                 *, backend_account_id: str):
        if ledger.account != backend_account_id:
            raise ValueError('Account binding differs from task ledger')
        self.ledger = ledger
        self.collector = collector
        self.account = backend_account_id

    def start(self, payload: dict, spend: SpendEnvelope) -> dict:
        if not spend.authorizes(self.account, payload, time.time()):
            raise PermissionError('Exact budget, trusted contract and consent are required')
        approval = ExplicitApproval(request_hash=spend.request_hash,
            account_id=spend.account_id, max_submissions=1,
            contract_confirmed=spend.contract_verified, spend_confirmed=spend.approved_by_user,
            expires_at_epoch=spend.expires_at_epoch)
        task_id = self.ledger.submit(payload, approval)
        return {'phase': 'pending', 'task_id': task_id}

    def poll(self, payload: dict) -> dict:
        result = self.ledger.poll(payload)
        return {k: v for k,v in result.items() if k != 'result_url'} | {'phase': result['phase']}

    def collect(self, payload: dict, dest: Path) -> dict:
        previous = self.ledger.info(payload)
        if not previous or previous['phase'] != 'ready' or not previous['task_id']:
            raise PermissionError('A confirmed completed remote task is required for collection')
        # Query again to obtain short-lived URL; fails if task identity changes.
        latest = self.ledger.poll(payload)
        if latest['phase'] != 'ready' or latest['task_id'] != previous['task_id']:
            raise ContractError('Result task changed before collection')
        if not latest.get('result_url'):
            raise ContractError('Ready state has no signed result URL')
        return self.collector.collect(latest['result_url'], dest) | {'task_id': previous['task_id']}
