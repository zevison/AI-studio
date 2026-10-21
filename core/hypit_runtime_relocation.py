"""Rebind only recognized app-owned local Runtime paths after migration.

This never edits SV business sources, assets, generation requests or spend
authorizations. A changed Runtime remains part of source_digest, so existing
preflight proofs naturally expire and must be established again.
"""
from __future__ import annotations
import hashlib
import html
import json
import os
from pathlib import Path, PureWindowsPath
import re
import tempfile

FORMAT = 'seedance.hypit-runtime-relocation@1'
PROFILE_NAME = 'hypit.runtime.json'
PROFILE_KEYS = {'format', 'dataRoot', 'endpoints'}
MEDIA_KEYS = {'ffmpegPath', 'ffprobePath'}
HTML_KEYS = MEDIA_KEYS | {'workers', 'maxWorkers', 'browserCapacity', 'defaultConcurrency',
                          'browserVersion', 'browserCacheDirectory', 'chromePath'}
RESOURCE_ATTR = re.compile(r'\b(?:src|source|from)\s*=\s*([\'"])(.*?)\1', re.S)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _native(path):
    path = Path(path).absolute()
    if os.name == 'nt' and not str(path).startswith('\\\\?\\'):
        return Path('\\\\?\\' + str(path))
    return path


def _not_link(path):
    if any(item.is_symlink() or getattr(item, 'is_junction', lambda: False)()
           for item in (path, *path.parents)):
        raise PermissionError('Hypit Runtime 迁移不接受链接或目录联接')


def _absolute(value, label):
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError(label + ' 必须是本机绝对路径')
    value = str(value)
    if (not value or '\x00' in value or value.startswith(('\\\\', '//'))
            or not (Path(value).is_absolute() or PureWindowsPath(value).is_absolute())):
        raise ValueError(label + ' 必须是本机绝对路径，不允许网络共享')
    return value


def _validate(profile):
    if (not isinstance(profile, dict) or set(profile) != PROFILE_KEYS
            or profile.get('format') != 'hypit.runtime-local@1'
            or profile.get('dataRoot') != '.hypit/execution'):
        raise PermissionError('非本程序管理的 Runtime 配置，禁止自动重写')
    endpoints = profile.get('endpoints')
    if (not isinstance(endpoints, dict) or not endpoints
            or 'media.local' not in endpoints or set(endpoints) - {'media.local', 'html.local'}):
        raise PermissionError('Runtime 含远程或未知服务端，禁止自动重写')
    for name, row in endpoints.items():
        if not isinstance(row, dict) or set(row) != {'use', 'pool', 'config'}:
            raise PermissionError('Runtime 服务端配置不属于受控格式')
        expected = ('@hypit/media-local', 'LOCAL-FREE') if name == 'media.local' else ('@hypit/provider-html-local', 'LOCAL-HTML')
        if (row['use'], row['pool']) != expected:
            raise PermissionError('只可迁移官方本地免费 Runtime 路径')
        settings = row['config']
        if not isinstance(settings, dict) or set(settings) - (MEDIA_KEYS if name == 'media.local' else HTML_KEYS):
            raise PermissionError('Runtime 含未知执行参数，禁止自动重写')
        for field in MEDIA_KEYS:
            if field not in settings:
                raise PermissionError('Runtime 缺少本程序生成的媒体组件参数')
            _absolute(settings[field], field)
        if name == 'html.local':
            for field in ('workers', 'maxWorkers', 'browserCapacity', 'defaultConcurrency'):
                if field in settings and (type(settings[field]) is not int or settings[field] != 1):
                    raise PermissionError('Runtime 并发配置不属于本程序生成格式')
            for field in ('chromePath', 'browserCacheDirectory'):
                if field in settings:
                    _absolute(settings[field], field)
            if 'browserVersion' in settings and not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', str(settings['browserVersion'])):
                raise PermissionError('Runtime 浏览器版本必须是确切版本号')


def _check_business_sources(project):
    count = 0
    for file in project.rglob('*'):
        if any(part in {'.hypit', '.agent_backups', '.runtime-relocation', 'node_modules'}
               for part in file.relative_to(project).parts):
            continue
        if file.suffix.lower() not in {'.svml', '.svrun', '.svs'} or not file.is_file():
            continue
        _not_link(file)
        count += 1
        if count > 500 or file.stat().st_size > 192000:
            raise PermissionError('Hypit 源码超出可安全检查范围')
        text = file.read_text(encoding='utf8')
        for _, raw in RESOURCE_ATTR.findall(text):
            value = html.unescape(raw)
            if value.startswith('@hypit/'):
                continue
            if (value.startswith(('/', '\\')) or Path(value).is_absolute() or PureWindowsPath(value).is_absolute()
                    or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', value)):
                raise PermissionError('旧 Hypit 业务源码含绝对或外部素材路径，无法安全自动映射；源码保持不变，请重新绑定素材')


def _atomic(path, raw):
    fd, temporary = tempfile.mkstemp(prefix='.runtime-', suffix='.tmp', dir=_native(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, _native(path))
    finally:
        Path(temporary).unlink(missing_ok=True)


def relocate_runtime(project, *, ffmpeg, ffprobe, browser_version=None,
                     browser_cache=None, chrome_path=None, busy=False):
    """Replace recognized local-host fields, retain exact old bytes and hashes.

    Callers supply the current server-controlled runtime selections, never raw
    request parameters. `busy` must include active and uncertain paid operations.
    No database/state or external service is accessed by this helper.
    """
    project = Path(project).absolute()
    _not_link(project)
    file = project / PROFILE_NAME
    _not_link(file)
    if not file.is_file() or file.stat().st_size > 128000:
        raise ValueError('Hypit Runtime 配置缺失或过大，原文件保持不变')
    original = file.read_bytes()
    try:
        profile = json.loads(original.decode('utf8'))
    except (UnicodeError, ValueError):
        raise ValueError('Hypit Runtime 配置损坏，原文件保持不变') from None
    _validate(profile)
    ffmpeg, ffprobe = _absolute(ffmpeg, 'FFmpeg'), _absolute(ffprobe, 'FFprobe')
    for row in profile['endpoints'].values():
        row['config'].update(ffmpegPath=ffmpeg, ffprobePath=ffprobe)
    if 'html.local' in profile['endpoints']:
        options = profile['endpoints']['html.local']['config']
        if chrome_path:
            options['chromePath'] = _absolute(chrome_path, 'Chromium')
            options.pop('browserVersion', None)
            options.pop('browserCacheDirectory', None)
        else:
            if not isinstance(browser_version, str) or not re.fullmatch(r'\d+\.\d+\.\d+\.\d+', browser_version):
                raise ValueError('必须提供当前受控 Chromium 确切版本')
            options['browserVersion'] = browser_version
            options['browserCacheDirectory'] = _absolute(browser_cache, 'Chromium 缓存')
            options.pop('chromePath', None)
    _validate(profile)
    before = _hash(original)
    if json.loads(original.decode('utf8')) == profile:
        return {'format': FORMAT, 'changed': False, 'before_sha256': before, 'after_sha256': before}
    if busy:
        raise PermissionError('Hypit 有执行中或结果未知的任务，禁止改写 Runtime；先完成旧任务核对')
    _check_business_sources(project)
    updated = (json.dumps(profile, ensure_ascii=False, indent=2) + '\n').encode('utf8')
    after = _hash(updated)
    backups = project / '.runtime-relocation'
    _not_link(backups)
    backups.mkdir(exist_ok=True)
    backup = backups / (before + '.runtime.json')
    _not_link(backup)
    if backup.exists():
        if not backup.is_file() or _hash(backup.read_bytes()) != before:
            raise PermissionError('原 Runtime 备份已变化，禁止覆盖')
    else:
        with _native(backup).open('xb') as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
    _not_link(file)
    if file.read_bytes() != original:
        raise RuntimeError('Runtime 在迁移中发生变化，未发布替换')
    receipt = {'format': FORMAT, 'changed': True, 'before_sha256': before,
               'after_sha256': after, 'backup': str(backup),
               'business_sources_changed': False, 'paid_requests_replayed': False,
               'requires_preflight': True}
    # Receipt is written first so an interrupted publication remains auditable.
    _atomic(backups / (before[:16] + '-' + after[:16] + '.json'), json.dumps(receipt, ensure_ascii=False, indent=2).encode('utf8'))
    _atomic(file, updated)
    return receipt
