"""Versioned provider configuration. Secrets never enter public documents.

Existing settings remain the compatibility facade for business preferences.
Custom providers implement an explicit protocol, not a claim of certification.
"""
from __future__ import annotations
import copy
from contextlib import contextmanager
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import tempfile
import threading
import uuid
from types import MappingProxyType
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from . import profile_store

_LOCK = threading.RLock()
SCHEMA_VERSION = 1
KINDS = ('chat', 'image', 'video')
PATH_DEFAULTS = {'chat': '/v1/chat/completions', 'responses': '/v1/responses',
                 'models': '/v1/models', 'image': '/v1/images/generations',
                 'image_edit': '/v1/images/edits', 'video': '/api/v3/contents/generations/tasks',
                 'video_query': '/api/v3/contents/generations/tasks/{task_id}'}

class ConfigurationConflict(ValueError): pass
class CredentialUnavailable(ValueError): pass
class FutureConfiguration(ValueError): pass

def _path(): return profile_store.BASE_DIR / 'configuration.json'
def _vault_path(): return profile_store.BASE_DIR / ('provider_secrets.dpapi' if os.name == 'nt' else 'provider_secrets.json')

@contextmanager
def _file_lock():
    profile_store.BASE_DIR.mkdir(parents=True, exist_ok=True)
    with (profile_store.BASE_DIR / 'configuration.lock').open('a+b') as stream:
        if stream.tell() == 0: stream.write(b'0'); stream.flush()
        stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try: yield
        finally:
            stream.seek(0)
            if os.name == 'nt': msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)

def assert_no_active_requests(connection):
    tables = {x[0] for x in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'tasks' in tables and connection.execute("SELECT 1 FROM tasks WHERE status IN ('pending','running','result_pending','submission_unknown') LIMIT 1").fetchone():
        raise ConfigurationConflict('有执行中或结果未知的生成任务；请先完成恢复查询，再切换服务商或凭据，避免串账号和重复付费')
    if 'ai_calls' in tables and connection.execute("SELECT 1 FROM ai_calls WHERE status IN ('submission_unknown','partial') LIMIT 1").fetchone():
        raise ConfigurationConflict('聊天请求结果尚未确定，请先完成或确认中断，再修改服务商或凭据')
    if 'token_calls' in tables and connection.execute("SELECT 1 FROM token_calls WHERE state IN ('submission_unknown','usage_unverified','over_budget') LIMIT 1").fetchone():
        raise ConfigurationConflict('Agent 调用尚未结算确认，请先核实费用再切换配置')

def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.stem, dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if os.name != 'nt': os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def _vault():
    path = _vault_path()
    if not path.exists(): return {}
    try:
        if path.stat().st_size > 4 * 1024 * 1024: raise ValueError()
        raw = profile_store._dpapi(path.read_bytes(), decrypt=True)
        data = json.loads(raw.decode('utf-8'))
        if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) or len(v) > 4096 for k, v in data.items()): raise ValueError()
        return data
    except (OSError, ValueError, UnicodeError):
        raise CredentialUnavailable('当前 Windows 用户无法读取服务商凭据；原文件已保留，请重新授权或恢复备份。') from None

def validate_base_url(value, *, resolve=False):
    value = str(value or '').strip().rstrip('/')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password
        or parsed.query or parsed.fragment or parsed.port not in (None, 443)
        or '\\' in value or any(ord(c) < 33 for c in value)):
        raise ValueError('接口地址必须为不含账号、查询参数的公网 HTTPS 地址，仅允许 443 端口')
    host = parsed.hostname.lower()
    if host == 'localhost' or host.endswith(('.localhost', '.local', '.internal')):
        raise ValueError('不允许访问本机或内网接口')
    try: addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = []
        if resolve:
            try: addresses = [ipaddress.ip_address(x[4][0]) for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]
            except socket.gaierror: raise ValueError('无法解析接口域名；请检查域名或网络后重试') from None
    if any(not address.is_global for address in addresses): raise ValueError('接口域名指向本机、内网或保留地址，已阻止请求')
    return value

def validate_path(value):
    value = str(value or '')
    if (not value.startswith('/') or value.startswith('//') or '\\' in value
        or any(ord(c) < 33 for c in value) or urlsplit(value).netloc or '..' in value.split('/')):
        raise ValueError('接口路径必须是同一服务商下以 / 开头的相对路径')
    return value

def _legacy():
    from . import config
    paths = dict(PATH_DEFAULTS)
    paths.update(image=config.IMAGE_CREATE_PATH, image_edit=config.IMAGE_EDIT_PATH,
                 video=config.VIDEO_CREATE_PATH, video_query=config.VIDEO_QUERY_PATH)
    model = config.VIDEO_MODEL
    timeout = config.HTTP_TIMEOUT
    # One-time virtual migration preserves existing model/path preferences.
    # No writes and no call to settings.get_all (which would form a cycle).
    from . import db
    if db.DB_PATH.exists():
        import sqlite3
        from contextlib import closing
        try:
            with closing(sqlite3.connect(db.DB_PATH.as_uri() + '?mode=ro', uri=True)) as connection:
                rows = dict(connection.execute("SELECT key,value_json FROM user_settings WHERE key IN ('video_base_model','video_endpoint_strategy','custom_video_create_path','custom_video_query_path','http_timeout')"))
                rows = {key: json.loads(value) for key, value in rows.items()}
                model = rows.get('video_base_model') or model
                timeout = rows.get('http_timeout') or timeout
                if rows.get('video_endpoint_strategy') == 'custom':
                    paths['video'] = rows.get('custom_video_create_path') or paths['video']
                    paths['video_query'] = rows.get('custom_video_query_path') or paths['video_query']
        except (sqlite3.Error, ValueError): pass
    provider = {'id': 'legacy-openlux', 'name': 'OpenLux（原有配置）', 'adapter': 'openlux',
                'enabled': True, 'base_url': config.OPENLUX_BASE_URL, 'paths': paths,
                'models': {'chat': 'gpt-6-astra', 'image': config.IMAGE_MODEL, 'video': model},
                'parameters': {'chat': {}, 'image': {}, 'video': {}}, 'timeout': timeout,
                'legacy_credential': True}
    return {'schema_version': SCHEMA_VERSION, 'revision': 0, 'providers': [provider],
            'defaults': {kind: provider['id'] for kind in KINDS}, 'runtime': {}}

def _read():
    if not _path().exists(): return _legacy()
    try:
        if _path().stat().st_size > 1024 * 1024: raise ValueError()
        value = json.loads(_path().read_text(encoding='utf-8'))
        if isinstance(value, dict) and isinstance(value.get('schema_version'), int) and value['schema_version'] > SCHEMA_VERSION:
            raise FutureConfiguration('配置来自更新版本；请升级应用，不能覆盖或降级配置')
        if not isinstance(value, dict) or value.get('schema_version') != SCHEMA_VERSION: raise ValueError()
        if type(value.get('revision')) is not int or value['revision'] < 1: raise ValueError()
        # Validate stored documents as strictly as an incoming API payload.
        # Truncated or manually edited JSON must not crash the settings screen.
        clean = _validate(value)
        clean['revision'] = value['revision']
        for source, target in zip(value['providers'], clean['providers']):
            reference = source.get('credential_ref')
            if reference is not None:
                if not isinstance(reference, str) or not re.fullmatch(r'cred_[a-f0-9]{32}', reference): raise ValueError()
                target['credential_ref'] = reference
        # Never reflect arbitrary on-disk fields in snapshot/export. In
        # particular a manually added api_key/token must remain local and must
        # not become an authenticated HTTP response or exported JSON.
        return clean
    except FutureConfiguration: raise
    except (OSError, ValueError, UnicodeError):
        # Never replace a corrupt/future document with an empty configuration.
        raise ValueError('配置文件损坏或版本不兼容；原文件已保留，可导入备份恢复。') from None

def snapshot():
    try: document = copy.deepcopy(_read()); document['error'] = None
    except ValueError as exc: document = _legacy(); document['error'] = str(exc)
    document['configuration_error'] = document['error']
    try: secrets = _vault(); credential_error = None
    except CredentialUnavailable as exc: secrets = {}; credential_error = str(exc)
    for provider in document['providers']:
        configured = bool(secrets.get(provider.get('credential_ref', provider['id'])))
        if provider.get('legacy_credential'):
            try:
                from . import secrets_store
                configured = bool(secrets_store.get('openlux_api_key'))
            except (OSError, ValueError, UnicodeError): credential_error = '原有凭据无法读取，原文件已保留'
        provider['key_configured'] = configured
        provider['verification'] = '协议适配，当前账号与模型须实际验证'
    document['credential_error'] = credential_error
    document['restart_required'] = ['runtime.output_directory', 'runtime.ffmpeg_path', 'runtime.ffprobe_path', 'runtime.chromium_path']
    return document

def export_document():
    document = copy.deepcopy(_read())
    for provider in document['providers']:
        provider.pop('legacy_credential', None)
        provider.pop('credential_ref', None)
    return document

def _validate(document):
    if not isinstance(document, dict) or type(document.get('schema_version')) is not int or document.get('schema_version') != SCHEMA_VERSION: raise ValueError('配置版本不支持')
    providers = document.get('providers')
    if not isinstance(providers, list) or not 1 <= len(providers) <= 50: raise ValueError('服务商配置数量须为 1—50')
    clean = {'schema_version': SCHEMA_VERSION, 'providers': [], 'defaults': {}, 'runtime': {}}
    if not isinstance(document.get('defaults'), dict): raise ValueError('默认账号配置无效')
    ids = set()
    for row in providers:
        if not isinstance(row, dict): raise ValueError('服务商配置无效')
        if any(not isinstance(row.get(field, {}), dict) for field in ('paths', 'models', 'parameters')):
            raise ValueError('接口路径、模型和参数必须为配置对象')
        pid = str(row.get('id') or '')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', pid) or pid in ids: raise ValueError('服务商编号无效或重复')
        ids.add(pid)
        adapter = row.get('adapter')
        if adapter not in ('openlux', 'openai_chat'): raise ValueError('尚未实现此适配器；仅支持 OpenLux 与 OpenAI 兼容聊天协议')
        timeout = row.get('timeout', 120)
        if type(timeout) is not int or not 5 <= timeout <= 300: raise ValueError('请求超时须为 5—300 秒')
        if type(row.get('enabled')) is not bool: raise ValueError('启用状态须为布尔值')
        paths = {k: validate_path(row.get('paths', {}).get(k, v)) for k, v in PATH_DEFAULTS.items()}
        models = {}
        for kind in KINDS:
            model = str(row.get('models', {}).get(kind) or '').strip()
            if model and not re.fullmatch(r'[A-Za-z0-9._:/@+\-]{1,160}', model): raise ValueError('模型 ID 格式不正确')
            models[kind] = model
        params = row.get('parameters', {})
        allowed = {'chat': {'temperature': (0, 2), 'top_p': (0, 1), 'max_tokens': (1, 32768)},
                   'image': {}, 'video': {}}
        checked = {}
        for kind in KINDS:
            entry = params.get(kind, {})
            if not isinstance(entry, dict) or set(entry) - set(allowed[kind]): raise ValueError(f'{kind} 含未经适配验证的参数')
            checked[kind] = {}
            for key, number in entry.items():
                low, high = allowed[kind][key]
                if type(number) not in (float, int) or not math.isfinite(number) or not low <= number <= high: raise ValueError(f'{key} 超出允许范围')
                if key == 'max_tokens' and type(number) is not int: raise ValueError('max_tokens 必须为整数')
                checked[kind][key] = number
        clean['providers'].append({'id': pid, 'name': str(row.get('name') or pid)[:100], 'enabled': row['enabled'],
            'adapter': adapter, 'base_url': validate_base_url(row.get('base_url')), 'paths': paths,
            'models': models, 'parameters': checked, 'timeout': timeout,
            'legacy_credential': bool(row.get('legacy_credential')) and pid == 'legacy-openlux'})
    for kind in KINDS:
        pid = document.get('defaults', {}).get(kind)
        row = next((x for x in clean['providers'] if x['id'] == pid and x['enabled']), None)
        if row is None or not row['models'].get(kind): raise ValueError(f'{kind} 默认配置须启用并指定模型')
        if kind != 'chat' and row['adapter'] != 'openlux': raise ValueError('图片与视频尚仅支持 OpenLux 适配器')
        clean['defaults'][kind] = pid
    runtime = document.get('runtime', {})
    if not isinstance(runtime, dict): raise ValueError('运行配置无效')
    # These values are consumed by the existing typed settings facade.
    from . import settings
    for key, value in runtime.items():
        if key not in settings.DEFAULTS: raise ValueError(f'运行参数未接通：{key}')
        if key in settings.BOOL_KEYS and type(value) is not bool: raise ValueError(f'{key} 必须是布尔值')
        if key in settings.INT_RANGES and type(value) is not int: raise ValueError(f'{key} 必须是整数')
        if key in settings.FLOAT_RANGES and (type(value) not in (int, float) or not math.isfinite(value)): raise ValueError(f'{key} 必须是有效数字')
        clean['runtime'][key] = settings._validate(key, value)
    return clean

def update_document(document, expected_revision, secret_updates=None, *, _recover=False):
    from . import db
    with _LOCK, _file_lock(), db.tx() as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert_no_active_requests(connection)
        try: current = _read()
        except FutureConfiguration: raise
        except ValueError:
            if not _recover: raise
            current = _legacy()
            if _path().exists(): _atomic(_path().with_suffix('.json.corrupt-backup'), _path().read_bytes())
        if type(expected_revision) is not int or current['revision'] != expected_revision:
            raise ConfigurationConflict('配置已被其他窗口修改，请刷新后重试')
        clean = _validate(document)
        clean['revision'] = expected_revision + 1
        # References are server controlled. A cloned/imported provider cannot
        # borrow another account's key by supplying its opaque reference.
        previous = {p['id']: p for p in current['providers']}
        for row in clean['providers']:
            old = previous.get(row['id'], {})
            if old.get('credential_ref'): row['credential_ref'] = old['credential_ref']
        if secret_updates:
            if not isinstance(secret_updates, dict): raise ValueError('凭据更新格式无效')
            vault = _vault()  # Fail closed; never discard an unreadable vault.
            ids = {p['id'] for p in clean['providers']}
            for pid, key in secret_updates.items():
                if pid not in ids or not isinstance(key, str) or len(key) > 4096 or any(ord(c) < 33 for c in key): raise ValueError('凭据编号或内容无效')
                row = next(p for p in clean['providers'] if p['id'] == pid)
                row['legacy_credential'] = False
                if key:
                    # Append only: a crash before document publication leaves
                    # the previous document AND its original credential intact.
                    reference = 'cred_' + uuid.uuid4().hex
                    vault[reference] = key
                    row['credential_ref'] = reference
                else: row.pop('credential_ref', None)
            _atomic(_vault_path(), profile_store._dpapi(json.dumps(vault).encode('utf-8')))
        if _path().exists(): _atomic(_path().with_suffix('.json.bak'), _path().read_bytes())
        _atomic(_path(), json.dumps(clean, ensure_ascii=False, indent=2).encode('utf-8'))
        return snapshot()

def import_document(document, expected_revision):
    # Import is deliberately incapable of restoring/exporting plaintext keys.
    if not isinstance(document, dict) or not isinstance(document.get('providers'), list): raise ValueError('导入配置格式无效')
    for provider in document.get('providers', []):
        if not isinstance(provider, dict): raise ValueError('导入服务商格式无效')
        if any(k in provider for k in ('api_key', 'token', 'secret')): raise ValueError('导入文件含明文凭据，已拒绝')
    return update_document(document, expected_revision, _recover=True)

def reauthorize_credentials(vault_kind, new_values, confirmed, expected_revision):
    """Explicitly recover only an unreadable vault, preserving its exact bytes.

    Other keys in the damaged vault cannot be recovered automatically and must
    be entered again. A healthy/missing vault must use normal settings updates.
    """
    if confirmed is not True: raise ValueError('必须明确确认保留损坏凭据备份并重新授权')
    if vault_kind not in ('provider', 'legacy'): raise ValueError('凭据类型无效')
    if not isinstance(new_values, dict) or not new_values: raise ValueError('请至少提供一个新的有效 API Key')
    from . import db
    with _LOCK, _file_lock(), db.tx() as connection:
        connection.execute('BEGIN IMMEDIATE')
        assert_no_active_requests(connection)
        current = _read()
        if type(expected_revision) is not int or current['revision'] != expected_revision:
            raise ConfigurationConflict('配置已被其他窗口修改，请刷新后重试')
        target = _vault_path() if vault_kind == 'provider' else profile_store.SECRETS_PATH
        if not target.exists(): raise ValueError('凭据文件不存在，请直接在正常设置中填写密钥')
        unreadable = False
        try:
            data = _vault() if vault_kind == 'provider' else json.loads(profile_store._dpapi(target.read_bytes(), decrypt=True).decode('utf-8'))
            if not isinstance(data, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in data.items()):
                unreadable = True
        except (OSError, ValueError, UnicodeError): unreadable = True
        if not unreadable: raise ValueError('当前凭据可以正常读取，禁止使用损坏恢复操作清空正常凭据')
        allowed = {p['id'] for p in current['providers']} if vault_kind == 'provider' else set(profile_store.SECRET_FIELDS)
        for name, value in new_values.items():
            if name not in allowed or not isinstance(value, str) or not value or len(value) > 4096 or any(ord(c) < 33 for c in value):
                raise ValueError('新凭据的编号或内容无效')
        original = target.read_bytes()
        digest = __import__('hashlib').sha256(original).hexdigest()
        backup = target.with_name(target.name + '.damaged-' + uuid.uuid4().hex + '-' + digest[:12] + '.bak')
        _atomic(backup, original)
        if backup.read_bytes() != original: raise OSError('损坏凭据备份校验失败，未替换原文件')
        document = copy.deepcopy(current)
        if vault_kind == 'provider':
            vault = {}
            for pid, key in new_values.items():
                row = next(p for p in document['providers'] if p['id'] == pid)
                reference = 'cred_' + uuid.uuid4().hex
                vault[reference] = key; row['credential_ref'] = reference; row['legacy_credential'] = False
            _atomic(target, profile_store._dpapi(json.dumps(vault).encode('utf-8')))
        else:
            profile_store._write_secrets(new_values)
        document['revision'] = current['revision'] + 1
        if _path().exists(): _atomic(_path().with_suffix('.json.bak'), _path().read_bytes())
        _atomic(_path(), json.dumps(document, ensure_ascii=False, indent=2).encode('utf-8'))
        result = snapshot()
        result['recovery'] = {'vault_kind': vault_kind, 'backup_file': backup.name, 'sha256': digest,
                              'other_credentials_require_reentry': True}
        return result

@dataclass(frozen=True)
class ProviderConfig:
    profile_id: str
    adapter: str
    base_url: str
    api_key: str = field(repr=False)
    model: str
    paths: object
    parameters: object
    timeout: int

def resolve_provider(kind):
    if kind not in KINDS: raise ValueError('不支持的模型类型')
    document = _read()
    row = next(x for x in document['providers'] if x['id'] == document['defaults'][kind])
    if not row['enabled']: raise ValueError('默认服务商已停用')
    key = _vault().get(row.get('credential_ref', row['id']), '')
    if row.get('legacy_credential'):
        from . import config, secrets_store
        key = key or secrets_store.get('openlux_api_key') or config.OPENLUX_API_KEY
    return ProviderConfig(row['id'], row['adapter'], row['base_url'], key, row['models'][kind],
        MappingProxyType(dict(row['paths'])), MappingProxyType(dict(row['parameters'][kind])), row['timeout'])

def resolve_official_provider():
    """Official Hypit/billing identity, separate from generic model defaults.

    An API key stored for a custom proxy must never be sent to OpenLux merely
    because the profile happens to retain the legacy profile identifier.
    """
    from . import config, secrets_store
    document = _read()
    row = next((p for p in document['providers'] if p['id'] == 'legacy-openlux'), None)
    fixed = 'https://api.openlux.ai'
    if row and row['base_url'].rstrip('/') in (fixed, fixed + '/v1'):
        if not row['enabled']: raise ValueError('官方 OpenLux 账户已停用；Hypit 和余额功能不可用')
        key = _vault().get(row.get('credential_ref', row['id']), '')
        if row.get('legacy_credential'): key = key or secrets_store.get('openlux_api_key') or config.OPENLUX_API_KEY
        return ProviderConfig(row['id'], 'openlux', fixed, key, row['models']['video'],
            MappingProxyType(dict(PATH_DEFAULTS)), MappingProxyType({}), row['timeout'])
    # The established official-account pane remains independent of a custom
    # chat/video profile. Only its original vault may supply a fallback key.
    if config.OPENLUX_BASE_URL.rstrip('/') not in (fixed, fixed + '/v1'):
        raise ValueError('官方账户接口固定为 https://api.openlux.ai，不能向自定义地址发送账户凭据')
    key = secrets_store.get('openlux_api_key') or config.OPENLUX_API_KEY
    return ProviderConfig('official-account', 'openlux', fixed, key, config.VIDEO_MODEL,
        MappingProxyType(dict(PATH_DEFAULTS)), MappingProxyType({}), config.HTTP_TIMEOUT)

def resolve_official_credentials():
    """Backend-only compatibility credentials for the verified Hypit bridge.

    The returned mapping is never an API response. Existing account/OSS fields
    retain their DPAPI source; only the official model key may use its new slot.
    """
    credentials = dict(profile_store.read_secrets())
    credentials['openlux_api_key'] = resolve_official_provider().api_key
    return credentials

def runtime_overrides():
    try: return dict(_read().get('runtime', {}))
    except ValueError: return {}

def merge_legacy_settings(values):
    """Keep established settings controls connected after provider migration."""
    if not _path().exists(): return
    document = _read()
    document['runtime'].update(values)
    row = next(p for p in document['providers'] if p['id'] == document['defaults']['video'])
    if 'video_base_model' in values: row['models']['video'] = values['video_base_model']
    if 'http_timeout' in values: row['timeout'] = values['http_timeout']
    strategy = document['runtime'].get('video_endpoint_strategy', 'recommended')
    if any(k in values for k in ('video_endpoint_strategy', 'custom_video_create_path', 'custom_video_query_path')):
        if strategy == 'custom':
            row['paths']['video'] = document['runtime'].get('custom_video_create_path') or PATH_DEFAULTS['video']
            row['paths']['video_query'] = document['runtime'].get('custom_video_query_path') or PATH_DEFAULTS['video_query']
        else:
            row['paths']['video'] = PATH_DEFAULTS['video']; row['paths']['video_query'] = PATH_DEFAULTS['video_query']
    update_document(document, document['revision'])

def startup_options():
    """Bootstrap-safe local paths; never imports settings/config or reads keys."""
    path = _path()
    try:
        document = json.loads(path.read_text(encoding='utf-8'))
        if document.get('schema_version') != SCHEMA_VERSION: return {}
        runtime = document.get('runtime', {})
        from pathlib import PureWindowsPath
        result = {}
        for key in ('output_directory', 'ffmpeg_path', 'ffprobe_path', 'chromium_path'):
            value = runtime.get(key, '')
            if (not isinstance(value, str) or not value or value.startswith(('\\\\', '//'))
                or '\x00' in value or not (Path(value).is_absolute() or PureWindowsPath(value).is_absolute())): continue
            if key != 'output_directory' and PureWindowsPath(value).suffix.lower() != '.exe': continue
            result[key] = value
        return result
    except (OSError, ValueError, AttributeError, TypeError): return {}

def provider_preferences(kind):
    """Non-secret preferences, safe for startup and settings screens."""
    try: document = _read()
    except ValueError: return {}
    if not document['revision']: return {}
    row = next(x for x in document['providers'] if x['id'] == document['defaults'][kind])
    return {'model': row['models'][kind], 'paths': dict(row['paths']), 'timeout': row['timeout']}
