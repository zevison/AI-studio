"""Fail-closed Hypit project preflight before ANY remote video submission.

【注意/Risk】Requires genuine Hypit v0.3.1 check/plan in production.
A stub validator may be used ONLY in offline tests. Agent writes and Runtime
configuration changes invalidate the proof. External remote media still need
immutable object version/hash binding in production.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .hypit_openlux_gateway import ContractError, request_sha256
from .hypit_provider_bridge import ProviderLifecycle, SpendEnvelope

SOURCE_EXTENSIONS = {'.svml', '.svrun', '.svs'}
CONFIG_FILES = {'hypit.runtime.json', 'package.json', 'package-lock.json', 'pnpm-lock.yaml', 'yarn.lock'}
PACKAGE_EXTENSIONS = {'.ts', '.tsx', '.js', '.mjs', '.cjs', '.json'}
MAX_SOURCE_FILES = 500
MAX_FILE_BYTES = 4_000_000
MAX_ASSET_BYTES = 400 * 1024 * 1024
MAX_ASSET_TOTAL = 1024 * 1024 * 1024


def source_digest(project: Path) -> str:
    root = Path(project).resolve(strict=True)
    files = []
    asset_bytes = 0
    for item in root.rglob('*'):
        if item.is_symlink() or (hasattr(item, 'is_junction') and item.is_junction()):
            raise ContractError('Project contains symlinks; sources cannot be trusted')
        if not item.is_file():
            continue
        rel = item.relative_to(root).as_posix()
        parts = item.relative_to(root).parts
        is_asset = parts[0] == 'assets'
        included = (item.suffix.lower() in SOURCE_EXTENSIONS or
                    rel in CONFIG_FILES or
                    is_asset or (parts[0] == 'packages' and item.suffix.lower() in PACKAGE_EXTENSIONS))
        if not included or any(x in {'node_modules', '.git', 'dist', 'build'} for x in parts):
            continue
        before = item.stat()
        if is_asset:
            asset_bytes += before.st_size
        if (len(files) >= MAX_SOURCE_FILES or before.st_size > (MAX_ASSET_BYTES if is_asset else MAX_FILE_BYTES)
                or asset_bytes > MAX_ASSET_TOTAL):
            raise ContractError('Hypit source manifest outside bounded limits')
        digest = hashlib.sha256()
        with item.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        after = item.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise ContractError('Project material changed during fingerprinting')
        files.append((rel, digest.hexdigest()))
    files.sort()
    names = {name for name,_ in files}
    if 'main.svml' not in names or 'build.svrun' not in names:
        raise ContractError('Hypit main.svml and build.svrun required')
    return hashlib.sha256(json.dumps(files,separators=(',',':'),ensure_ascii=False).encode('utf8')).hexdigest()


@dataclass(frozen=True)
class PreflightProof:
    account: str
    request_hash: str
    project_hash: str
    expires_at: float
    signature: str


class HypitProductionGate:
    """Backend-only authority. Proof signing key must never be in client state."""
    def __init__(self, project_dir: Path, project_tools, provider: ProviderLifecycle,
                 *, secret_key: bytes, clock: Callable = time.time):
        if not isinstance(secret_key, bytes) or len(secret_key) < 32:
            raise ValueError('Strong ephemeral server-side signing key required')
        self.project = Path(project_dir).resolve(strict=True)
        self.tools = project_tools
        self.provider = provider
        self.secret = secret_key
        self.clock = clock
        if self.tools.root != self.project:
            raise ValueError('Project tools must belong to this exact project')

    def _sign(self, account, request_hash, project_hash, expires):
        message = f'{account}\n{request_hash}\n{project_hash}\n{expires:.6f}'.encode()
        return hmac.new(self.secret,message,hashlib.sha256).hexdigest()

    def preflight(self, payload: dict, *, lifetime_seconds: int = 300) -> PreflightProof:
        if type(lifetime_seconds) is not int or not (30 <= lifetime_seconds <= 300):
            raise ValueError('Preflight validity must be 30 to 300 seconds')
        digest_before = source_digest(self.project)
        for command, source in (('check','main.svml'),('plan','build.svrun')):
            result = self.tools.call('validate',{'command':command,'source':source})
            if not isinstance(result,dict) or result.get('ok') is not True:
                raise ContractError('Official Hypit '+command+' did not pass; paid execution blocked')
        digest_after = source_digest(self.project)
        if digest_before != digest_after:
            raise ContractError('Hypit sources changed during validation')
        expires = self.clock() + lifetime_seconds
        account = self.provider.account
        fingerprint = request_sha256(payload)
        return PreflightProof(account,fingerprint,digest_after,expires,
                        self._sign(account,fingerprint,digest_after,expires))

    def start(self, payload: dict, spend: SpendEnvelope, proof: PreflightProof) -> dict:
        if not isinstance(proof,PreflightProof):
            raise PermissionError('Missing server-side Hypit preflight proof')
        now=self.clock()
        if not now < proof.expires_at <= now+300 or proof.account != self.provider.account:
            raise PermissionError('Hypit preflight invalid or expired')
        if not hmac.compare_digest(self._sign(proof.account,proof.request_hash,
                                  proof.project_hash,proof.expires_at),proof.signature):
            raise PermissionError('Invalid Hypit proof signature')
        if proof.request_hash != request_sha256(payload) or proof.project_hash != source_digest(self.project):
            raise PermissionError('Hypit sources or request changed after validation')
        return self.provider.start(payload, spend)
