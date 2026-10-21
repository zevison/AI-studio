"""OpenLux paid-video contract & durable asynchronous task gateway.

【注意/Risk】DEVELOPMENT ONLY. This is NOT a Hypit SDK Endpoint Provider.
The upstream OpenLux account-specific task schema, availability and pricing
have NOT been verified. The default is NO REMOTE VIDEO SUBMISSION.
The future @hypit/hypit/endpoint Provider must consume these same validated
semantics, but do not register this module as an official Hypit package.

No credentials, signed media links or user prompts are stored in the ledger.
A lost acknowledgement causes QUARANTINE, NEVER automatic resubmission.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import ipaddress
import json
import re
import sqlite3
import threading
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

MAX_REFS = {'image': 30, 'video': 10, 'audio': 10}
ROLES = {'reference_image': 'image', 'first_frame': 'image', 'last_frame': 'image',
         'reference_video': 'video', 'reference_audio': 'audio'}
OK_STATUSES = {'succeeded', 'success', 'completed', 'complete'}
PENDING_STATUSES = {'queued', 'pending', 'submitted', 'running', 'processing', 'in_progress'}
FAIL_STATUSES = {'failed', 'error', 'expired', 'cancelled', 'canceled'}
MODEL_NAME = 'doubao-seedance-2-5-260628'


class ContractError(ValueError):
    """Local input or API contract differs; never assume a successful paid call."""


class SubmissionUnknown(RuntimeError):
    """Remote may have accepted task. Do not retry until reconciled."""


class Rejected(RuntimeError):
    """Proven pre-submission or service validation rejection."""


def _object(value: object, label='value') -> dict:
    if not isinstance(value, dict):
        raise ContractError(f'{label} must be a JSON object')
    return value


def _external_https(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) > 4096 or not value:
        raise ContractError(f'{label}: missing HTTPS link')
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme.lower() != 'https' or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        raise ContractError(f'{label}: HTTPS URL without credentials/fragment required')
    host = (parsed.hostname or '').lower()
    if host in {'localhost', 'localhost.localdomain'} or host.endswith(('.localhost', '.local')):
        raise ContractError(f'{label}: local host forbidden')
    try:
        host_ip = ipaddress.ip_address(host.strip('[]'))
    except ValueError:
        if not re.fullmatch(r'[a-z0-9.-]+', host) or '.' not in host or '..' in host:
            raise ContractError(f'{label}: invalid public hostname')
    else:
        if not host_ip.is_global:
            raise ContractError(f'{label}: nonpublic IP forbidden')
    if parsed.port not in (None, 443):
        raise ContractError(f'{label}: unexpected port')
    return value


def _media_reference(ref: object) -> tuple[str, dict]:
    ref = _object(ref, 'reference')
    if set(ref) - {'role', 'url', 'person_reference'}:
        raise ContractError('Unknown media reference field: cannot silently drop it')
    role = ref.get('role')
    if role not in ROLES:
        raise ContractError(f'Unsupported reference role {role!r}')
    kind = ROLES[role]
    person = ref.get('person_reference')
    if person is not None and (kind != 'image' or type(person) is not bool):
        raise ContractError('person_reference only supports boolean image roles')
    url = _external_https(ref.get('url'), 'reference media')
    ty = {'image': 'image_url', 'video': 'video_url', 'audio': 'audio_url'}[kind]
    item = {'type': ty, ty: {'url': url}, 'role': role}
    if person is not None:
        # Deliberately refuse until a named OpenLux contract proves this field
        # can be transmitted without semantic loss, rather than silently drop it.
        raise ContractError('person_reference transport is not yet verified')
    return kind, item


def build_video_request(*, prompt: str, references: list[dict], task_type: str,
                        duration: int, ratio: str, resolution: str, generate_audio: bool,
                        model: str = MODEL_NAME) -> dict:
    """Build explicit Seedance request without ever touching the network.

    Fields follow existing V5.0.0 OpenLux client; acceptance by user's selected
    account/provider is UNVERIFIED. No fallback model, parameter clamping or role dropping.
    """
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 10_000:
        raise ContractError('Invalid prompt length')
    if task_type not in {'text', 'reference', 'edit', 'extend'}:
        raise ContractError('Unknown video task type')
    if not isinstance(model, str) or not re.fullmatch(r'[a-zA-Z0-9._:-]{3,128}', model):
        raise ContractError('Invalid model identifier')
    if model != MODEL_NAME:
        # Other models need their own tested contract and admissible input ports.
        raise ContractError('Model not matched to this contract')
    if resolution not in {'480p', '720p', '1080p'}:
        raise ContractError('Unsupported resolution')
    if type(duration) is not int:
        raise ContractError('Video duration must be an integer')
    if type(generate_audio) is not bool:
        raise ContractError('generate_audio must be a boolean')
    if not isinstance(references, list) or len(references) > 50:
        raise ContractError('Invalid media reference collection')
    counts = {'image': 0, 'video': 0, 'audio': 0}
    roles, content = [], [{'type': 'text', 'text': prompt.strip()}]
    for ref in references:
        kind, item = _media_reference(ref)
        counts[kind] += 1
        if counts[kind] > MAX_REFS[kind]:
            raise ContractError(f'Too many {kind} references')
        roles.append(item['role'])
        content.append(item)
    if roles.count('first_frame') > 1 or roles.count('last_frame') > 1:
        raise ContractError('At most one first and last frame allowed')
    if task_type == 'text':
        if references:
            raise ContractError('Text-only task cannot silently consume references')
        if not (4 <= duration <= 30) or ratio not in {'16:9', '9:16', '1:1', '4:3', '3:4', '21:9'}:
            raise ContractError('Text generation requires explicit duration and ratio')
    elif task_type == 'reference':
        if not references:
            raise ContractError('Reference task requires at least one reference')
        if not (duration == -1 or 4 <= duration <= 30):
            raise ContractError('Reference generation duration outside model bounds')
        if ratio not in {'adaptive', '16:9', '9:16', '1:1', '4:3', '3:4', '21:9'}:
            raise ContractError('Invalid reference aspect ratio')
    elif task_type in {'edit', 'extend'}:
        if 'reference_video' not in roles:
            raise ContractError(f'{task_type} requires a source reference video')
        if roles.count('reference_video') != 1:
            raise ContractError(f'{task_type} currently supports one source video; refuse ambiguous role')
        if ratio != 'adaptive' or (task_type=='edit' and duration!=-1) or (task_type=='extend' and duration!=-1 and not 4<=duration<=30):
            raise ContractError(f'{task_type} duration/ratio does not match the official 2.5 contract')
        if 'first_frame' in roles or 'last_frame' in roles:
            raise ContractError('Edit/extend frame+video combination has not been verified')
    payload = {
        'model': model, 'content': content, 'ratio': ratio,
        'resolution': resolution, 'duration': duration,
        'generate_audio': generate_audio, 'output_format': 'mp4',
    }
    if task_type != 'text':
        payload['omni_reference_task_type'] = task_type
    return payload


def task_id_from(response: object) -> str:
    row = _object(response, 'submit response')
    containers = [row]
    if isinstance(row.get('data'), dict):
        containers.append(row['data'])
    present = [str(x[k]) for x in containers for k in ('id', 'task_id') if isinstance(x.get(k), (str, int)) and str(x[k])]
    if len(set(present)) != 1 or not present or len(present[0]) > 256:
        raise ContractError('Missing or conflicting service task identifiers')
    if not re.fullmatch(r'[A-Za-z0-9_.:-]+', present[0]):
        raise ContractError('Invalid task identifier')
    return present[0]


def state_from(response: object) -> str:
    row = _object(response, 'task response')
    containers = [row]
    if isinstance(row.get('data'), dict):
        containers.append(row['data'])
    values = [str(x[k]).strip().lower() for x in containers for k in ('status', 'task_status', 'state') if x.get(k) is not None]
    if not values or len(set(values)) != 1:
        raise ContractError('Missing or conflicting task status')
    value = values[0]
    if value in OK_STATUSES:
        return 'ready'
    if value in PENDING_STATUSES:
        return 'pending'
    if value in FAIL_STATUSES:
        return 'failed'
    raise ContractError(f'Unknown task status: {value[:48]}')


def result_url_from(response: object) -> str:
    """Allow a URL only from specific result nodes, never find arbitrary links."""
    row = _object(response, 'completed response')
    candidates = []
    for parent in (row, row.get('data'), row.get('output')):
        if not isinstance(parent, dict):
            continue
        for k in ('video_url', 'output_url'):
            if k in parent:
                candidates.append(parent[k])
        if 'output' in parent and isinstance(parent['output'], str):
            candidates.append(parent['output'])
        content = parent.get('content')
        if isinstance(content, dict):
            for k in ('video_url', 'output_url'):
                if k in content:
                    candidates.append(content[k])
    candidates = list(dict.fromkeys(str(x) for x in candidates if x))
    if len(candidates) != 1:
        raise ContractError('No unique final video URL in known result fields')
    return _external_https(candidates[0], 'generated video')


def request_sha256(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


class Transport(Protocol):
    def post(self, payload: dict, *, timeout: float) -> dict: ...
    def get(self, task_id: str, *, timeout: float) -> dict: ...


@dataclass(frozen=True)
class ExplicitApproval:
    """Approval is tied to one exact payload, identity & chosen endpoint contract."""
    request_hash: str
    account_id: str
    max_submissions: int = 1
    contract_confirmed: bool = False
    spend_confirmed: bool = False
    expires_at_epoch: float = 0.0


class DurableVideoGateway:
    """Atomic submit ledger; injects transport; never stores API keys or URLs."""
    def __init__(self, ledger: Path, transport: Transport, *, expected_account: str,
                 verified_transport: bool = False, timeout_seconds: float = 45.0):
        if not re.fullmatch(r'[A-Za-z0-9_.:-]{2,128}', expected_account):
            raise ValueError('Invalid non-secret account identifier')
        self.path = Path(ledger)
        self.transport = transport
        self.account = expected_account
        self.verified_transport = verified_transport
        self.timeout_seconds = timeout_seconds
        self._mutex = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS submissions (
              fingerprint TEXT PRIMARY KEY, account TEXT NOT NULL,
              phase TEXT NOT NULL, task_id TEXT UNIQUE, updated REAL NOT NULL)''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, isolation_level=None, timeout=20)
        try:
            db.execute('PRAGMA busy_timeout=20000')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    def info(self, payload: dict) -> dict | None:
        fp = request_sha256(payload)
        with self._connect() as db:
            row = db.execute('SELECT account,phase,task_id,updated FROM submissions WHERE fingerprint=?', (fp,)).fetchone()
        if row and row[0]!=self.account:raise PermissionError('Stored task belongs to another account')
        return dict(zip(('phase', 'task_id', 'updated'), row[1:])) if row else None

    def submit(self, payload: dict, approval: ExplicitApproval) -> str:
        # Checks before *any* ledger write or network event.
        if not isinstance(payload, dict) or not isinstance(approval, ExplicitApproval):
            raise ContractError('Invalid submission or approval')
        fp = request_sha256(payload)
        if not self.verified_transport or not approval.contract_confirmed:
            raise PermissionError('Account-specific OpenLux request/status contract not verified; NO submission')
        if approval.account_id != self.account or approval.request_hash != fp or not approval.spend_confirmed:
            raise PermissionError('No explicit spend approval for this exact request and account')
        if not isinstance(approval.expires_at_epoch, (int, float)) or not time.time() < approval.expires_at_epoch <= time.time() + 3600:
            raise PermissionError('Spend approval expired or validity window exceeds one hour')
        if approval.max_submissions != 1:
            raise PermissionError('Only one authorised submission is supported')
        # Strictly validate payload, not just hash it. Reconstruct from exact ports.
        role_refs = []
        items = payload.get('content')
        if not isinstance(items, list) or not items or not isinstance(items[0], dict):
            raise ContractError('Missing task content')
        if items[0].get('type') != 'text' or not isinstance(items[0].get('text'), str):
            raise ContractError('Missing text prompt')
        for item in items[1:]:
            item = _object(item, 'media item')
            role = item.get('role')
            kind = ROLES.get(role)
            typ = {'image': 'image_url', 'video': 'video_url', 'audio': 'audio_url'}.get(kind)
            if not typ or item.get('type') != typ or not isinstance(item.get(typ), dict):
                raise ContractError('Unsupported media item; no silent omission')
            if set(item) != {'role', 'type', typ} or set(item[typ]) != {'url'}:
                raise ContractError('Undocumented media fields are not permitted')
            role_refs.append({'role': role, 'url': item[typ]['url']})
        subtype = payload.get('omni_reference_task_type', 'text')
        rebuilt = build_video_request(prompt=items[0]['text'], references=role_refs,
                   task_type=subtype, duration=payload.get('duration'), ratio=payload.get('ratio'),
                   resolution=payload.get('resolution'), generate_audio=payload.get('generate_audio'),
                   model=payload.get('model'))
        if rebuilt != payload:
            raise ContractError('Submitted payload differs from validated contract')
        with self._mutex:
            with self._connect() as db:
                db.execute('BEGIN IMMEDIATE')
                old = db.execute('SELECT phase,task_id FROM submissions WHERE fingerprint=?', (fp,)).fetchone()
                if old:
                    db.execute('COMMIT')
                    raise SubmissionUnknown(f'Existing request phase={old[0]}; never auto-resubmit')
                db.execute('INSERT INTO submissions VALUES(?,?,?,?,?)',
                           (fp, self.account, 'submission_unknown', None, time.time()))
                db.execute('COMMIT')
            try:
                response = self.transport.post(payload, timeout=self.timeout_seconds)
                task_id = task_id_from(response)
            except Exception as exc:
                # Even HTTP 5xx and network timeout may represent an accepted paid job.
                # Never unreserve the fingerprint or blindly replay.
                raise SubmissionUnknown('Submission outcome unknown; reconcile with provider before retry') from exc
            with self._connect() as db:
                db.execute('UPDATE submissions SET phase=?, task_id=?, updated=? WHERE fingerprint=?',
                           ('submitted', task_id, time.time(), fp))
            return task_id

    def poll(self, payload: dict) -> dict:
        record = self.info(payload)
        if not record or not record['task_id']:
            raise SubmissionUnknown('No acknowledged remote task to poll')
        task_id = record['task_id']
        response = self.transport.get(task_id, timeout=self.timeout_seconds)
        report = _object(response, 'query response')
        if task_id_from(report)!=task_id:raise ContractError('Task response does not prove the acknowledged identity')
        for part in (report, report.get('data')):
            if isinstance(part, dict):
                identifiers = [str(part[key]) for key in ('id', 'task_id') if part.get(key) is not None]
                if identifiers and (len(set(identifiers)) != 1 or identifiers[0] != task_id):
                    raise ContractError('Task identity differs from the recorded acknowledgement')
        phase = state_from(response)
        if phase == 'ready':
            url = result_url_from(response)
        else:
            url = None
        with self._connect() as db:
            db.execute('UPDATE submissions SET phase=?, updated=? WHERE fingerprint=?',
                       (phase, time.time(), request_sha256(payload)))
        # Never save signed links into durable ledger.
        return {'phase': phase, 'task_id': task_id, 'result_url': url}
