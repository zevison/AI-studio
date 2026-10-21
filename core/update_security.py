"""TUF trust, release policy and Windows publisher checks for Seedance only.

No production trust root or endpoint is invented here. A missing bundled trust
configuration disables networking and installation. Test repositories are
injected Python adapters, never enabled by a frontend request or user JSON.
"""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import sys
import time
from urllib.parse import unquote, urlsplit
import urllib.error

PRODUCT_ID = 'SeedanceStudio'


class UpdateBlocked(ValueError): pass
class UpdateRejected(ValueError): pass


def sha256(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def plain_path(path):
    path = Path(path).absolute()
    if any(p.is_symlink() or getattr(p, 'is_junction', lambda: False)() for p in (path, *path.parents)):
        raise UpdateRejected('更新路径包含链接或目录联接')
    return path.resolve()


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,5}\.\d{1,5}\.\d{1,5}', value):
        raise UpdateRejected('更新版本必须是三段数字版本')
    return tuple(map(int, value.split('.')))


@dataclass(frozen=True)
class TrustPolicy:
    bootstrap: bytes
    metadata_url: str
    targets_url: str
    signer_sha256: tuple[str, ...]
    channels: tuple[str, ...] = ('stable', 'beta')
    max_target_bytes: int = 2 * 1024 * 1024 * 1024


def load_bundled_policy(resource_root):
    folder = plain_path(Path(resource_root) / 'assets')
    file = folder / 'update-trust.json'
    if not file.is_file():
        return None
    plain_path(file)
    if file.stat().st_size > 65536:
        raise UpdateRejected('更新信任配置过大')
    try:
        doc = json.loads(file.read_text(encoding='utf8'))
        if doc.get('format') != 'seedance.update-trust@1' or doc.get('product_id') != PRODUCT_ID:
            raise UpdateRejected('更新信任配置的产品身份不匹配')
        if doc.get('enabled') is not True:
            return None
        if doc.get('root_file') != 'update-root.json':
            raise UpdateRejected('更新信任根路径无效')
        root = plain_path(folder / doc['root_file'])
        if root.parent != folder or not root.is_file() or root.stat().st_size > 512000:
            raise UpdateRejected('更新信任根缺失或无效')
        if sha256(root) != doc.get('root_sha256'):
            raise UpdateRejected('更新信任根哈希不匹配')
        from .configuration_service import validate_base_url
        metadata = validate_base_url(doc['metadata_url']).rstrip('/') + '/'
        targets = validate_base_url(doc['targets_url']).rstrip('/') + '/'
        signers = doc.get('signer_sha256')
        if not isinstance(signers, list) or not signers or any(not isinstance(s, str) or not re.fullmatch('[0-9a-f]{64}', s) for s in signers):
            raise UpdateBlocked('尚未配置正式 Authenticode 发布证书身份')
        return TrustPolicy(root.read_bytes(), metadata, targets, tuple(signers))
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise UpdateBlocked('正式更新信任配置不可用；不会联网或安装') from exc


@dataclass(frozen=True)
class Release:
    target_path: str
    version: str
    build: str
    sequence: int
    channel: str
    length: int
    sha256: str
    schema_min: int
    schema_max: int
    schema_new: int
    notes: str

    def identity(self):
        return hashlib.sha256(json.dumps(self.__dict__, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_release(target, *, policy, channel, current_version, current_schema,
                     os_name=None, architecture=None, os_build=None):
    if channel not in policy.channels:
        raise UpdateRejected('更新通道不受信任')
    path = target.path
    pure = PurePosixPath(path)
    if (not isinstance(path, str) or pure.is_absolute() or '..' in pure.parts or '\\' in path
            or ':' in path or not re.fullmatch(r'[A-Za-z0-9_./-]+\.exe', path)):
        raise UpdateRejected('更新安装包路径无效')
    custom = target.custom
    if not isinstance(custom, dict) or custom.get('product_id') != PRODUCT_ID:
        raise UpdateRejected('更新安装包属于其他产品')
    if custom.get('channel') != channel:
        raise UpdateRejected('更新通道不匹配')
    version = custom.get('version')
    if version_tuple(version) < version_tuple(current_version):
        raise UpdateRejected('禁止降级应用版本')
    if version_tuple(custom.get('minimum_app_version')) > version_tuple(current_version):
        raise UpdateRejected('当前版本过旧，需要经过支持的中间版本升级')
    system = os_name or platform.system()
    machine = (architecture or platform.machine()).upper()
    build = os_build if os_build is not None else (sys.getwindowsversion().build if os.name == 'nt' else 0)
    if system != 'Windows' or machine not in ('AMD64', 'X86_64') or custom.get('platform') != 'windows-x64':
        raise UpdateRejected('更新安装包与当前系统架构不兼容')
    minimum = custom.get('minimum_windows_build')
    if type(minimum) is not int or minimum < 19041 or build < minimum:
        raise UpdateRejected('当前 Windows 版本不满足更新要求')
    sequence = custom.get('release_sequence')
    if type(sequence) is not int or sequence < 1:
        raise UpdateRejected('更新版本序列无效')
    schema = [custom.get(key) for key in ('schema_min', 'schema_max', 'schema_new')]
    if any(type(value) is not int or not 0 <= value <= 1000000 for value in schema):
        raise UpdateRejected('更新数据库兼容范围无效')
    if not schema[0] <= current_schema <= schema[1] or schema[2] < current_schema:
        raise UpdateRejected('更新不能安全读取当前数据库，禁止安装')
    if type(target.length) is not int or not 1 <= target.length <= policy.max_target_bytes:
        raise UpdateRejected('更新安装包大小超出安全范围')
    digest = target.hashes.get('sha256', '')
    if not re.fullmatch('[0-9a-f]{64}', digest):
        raise UpdateRejected('更新缺少 SHA256 真实性绑定')
    label = custom.get('build')
    if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9._+-]{1,100}', label):
        raise UpdateRejected('更新构建身份无效')
    return Release(path, version, label, sequence, channel, target.length, digest,
                   *schema, str(custom.get('notes') or '')[:8000])


def verify_package(path, release):
    path = plain_path(path)
    if not path.is_file() or path.stat().st_size != release.length or sha256(path) != release.sha256:
        raise UpdateRejected('更新安装包大小或 SHA256 不匹配；禁止执行')
    return path


def verify_authenticode(path, allowed_signers):
    if os.name != 'nt' or not allowed_signers:
        raise UpdateBlocked('正式安装需要 Windows Authenticode 与受信任发布证书')
    path = plain_path(path)
    literal = str(path).replace("'", "''")
    script = ("$s=Get-AuthenticodeSignature -LiteralPath '" + literal + "';"
              "$h='';if($s.SignerCertificate){$a=[Security.Cryptography.SHA256]::Create();"
              "$h=([BitConverter]::ToString($a.ComputeHash($s.SignerCertificate.RawData))).Replace('-','').ToLower();$a.Dispose()};"
              "@{status=$s.Status.ToString();certificate_sha256=$h}|ConvertTo-Json -Compress")
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
                            capture_output=True, timeout=30, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        info = json.loads(result.stdout.decode('utf8-sig'))
    except (ValueError, UnicodeError):
        raise UpdateRejected('无法验证更新安装包发布者') from None
    if result.returncode or info.get('status') != 'Valid' or info.get('certificate_sha256') not in allowed_signers:
        raise UpdateRejected('更新安装包 Authenticode 签名或发布证书不受信任')
    return info


def public_fetcher(policy):
    """No environment proxies, redirects, LAN destinations or foreign origins."""
    from tuf.ngclient.fetcher import FetcherInterface
    from tuf.api.exceptions import DownloadHTTPError
    allowed = {(urlsplit(url).scheme, urlsplit(url).netloc) for url in (policy.metadata_url, policy.targets_url)}
    class PinnedFetcher(FetcherInterface):
        def _fetch(self, url):
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.netloc) not in allowed or parsed.username or parsed.password:
                raise UpdateRejected('更新源跨越受信任网络边界')
            from .provider_transport import urlopen
            import urllib.request
            started = time.monotonic()
            try:
                with urlopen(urllib.request.Request(url, headers={'User-Agent': 'SeedanceStudio-Updater/1'}), timeout=20) as response:
                    while chunk := response.read(65536):
                        if time.monotonic() - started > 600:
                            raise TimeoutError('更新下载超过十分钟')
                        yield chunk
            except urllib.error.HTTPError as exc:
                raise DownloadHTTPError('Update repository HTTP error', exc.code) from exc
    return PinnedFetcher()


def progress_fetcher(fetcher, callback):
    from tuf.ngclient.fetcher import FetcherInterface
    class ProgressFetcher(FetcherInterface):
        def _fetch(self,url):
            count=0
            for chunk in fetcher.fetch(url):
                count+=len(chunk);callback(url,count);yield chunk
    return ProgressFetcher()


def local_repository_fetcher(repository, policy):
    """Explicit local test adapter. No frontend or production loader selects it."""
    from tuf.ngclient.fetcher import FetcherInterface
    from tuf.api.exceptions import DownloadHTTPError
    root = plain_path(repository)
    prefix = urlsplit(policy.metadata_url)
    target_prefix = urlsplit(policy.targets_url)
    class LocalFetcher(FetcherInterface):
        def _fetch(self, url):
            parsed = urlsplit(url)
            if (parsed.scheme, parsed.netloc) != (prefix.scheme, prefix.netloc):
                raise UpdateRejected('测试更新源越界')
            decoded = unquote(parsed.path)
            if parsed.path.startswith(prefix.path):
                relative = 'metadata/' + decoded[len(prefix.path):]
            elif parsed.path.startswith(target_prefix.path):
                relative = 'targets/' + decoded[len(target_prefix.path):]
            else:
                raise UpdateRejected('测试更新路径越界')
            parts = PurePosixPath(relative)
            if '..' in parts.parts or '\\' in relative or ':' in relative:
                raise UpdateRejected('测试更新路径越界')
            file = plain_path(root.joinpath(*parts.parts))
            if not file.is_relative_to(root) or not file.is_file():
                raise DownloadHTTPError('Local test repository file missing', 404)
            with file.open('rb') as stream:
                for chunk in iter(lambda: stream.read(65536), b''):
                    yield chunk
    return LocalFetcher()
