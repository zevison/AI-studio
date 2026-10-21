from __future__ import annotations
import os
from . import config, profile_store

PATH = profile_store.SECRETS_PATH

def _read() -> dict:
    try:
        profile_store.ensure_bootstrap(config.ROOT)
        return profile_store.read_secrets()
    except (OSError, ValueError, UnicodeError):
        raise ValueError("本机凭据无法读取，原文件已保留；请检查 Windows 用户或恢复凭据备份") from None

def _write(data: dict) -> None:
    profile_store.update_secrets(data)

def get(key: str, default: str = '') -> str:
    val = _read().get(key)
    if val is None or val == '':
        env_map = {
            'openlux_api_key': ('OPENLUX_API_KEY',),
            'oss_access_key_id': ('OSS_ACCESS_KEY_ID','ALIYUN_OSS_ACCESS_KEY_ID'),
            'oss_access_key_secret': ('OSS_ACCESS_KEY_SECRET','ALIYUN_OSS_ACCESS_KEY_SECRET'),
            'r2_access_key_id': ('R2_ACCESS_KEY_ID',),
            'r2_secret_access_key': ('R2_SECRET_ACCESS_KEY',),
            'openlux_account_access_token': ('OPENLUX_ACCOUNT_ACCESS_TOKEN',),
            'openlux_account_user_id': ('OPENLUX_ACCOUNT_USER_ID',),
        }
        for env_key in env_map.get(key, ()):
            v = os.getenv(env_key, '')
            if v:
                return v
        return default
    return str(val)

def update(values: dict) -> dict:
    from . import db, configuration_service
    with db.tx() as connection:
        connection.execute('BEGIN IMMEDIATE')
        configuration_service.assert_no_active_requests(connection)
        return _update_unlocked(values)

def _update_unlocked(values: dict) -> dict:
    data = _read()
    for k in profile_store.SECRET_FIELDS:
        if k not in values:
            continue
        v = values.get(k)
        if v is None:
            continue
        v = str(v).strip()
        if v:
            data[k] = v
    profile_store.update_secrets(data)
    return status()

def _guarded_remove(*keys):
    from . import db, configuration_service
    with db.tx() as connection:
        connection.execute('BEGIN IMMEDIATE')
        configuration_service.assert_no_active_requests(connection)
        profile_store.remove_secrets(*keys)
    return status()

def clear_oss() -> dict:
    return _guarded_remove('oss_access_key_id','oss_access_key_secret')

def clear_openlux_account() -> dict:
    return _guarded_remove('openlux_account_access_token','openlux_account_user_id')

def _mask(v: str) -> str:
    if not v:
        return ''
    if len(v) <= 8:
        return '*' * len(v)
    return v[:4] + '*' * (len(v)-8) + v[-4:]

def status() -> dict:
    try: _read()
    except ValueError as exc:
        return {'credential_error':str(exc),'openlux_api_key_configured':False, 'oss_access_key_configured':False,'oss_access_key_secret_configured':False, 'openlux_account_token_configured':False,'openlux_account_user_id_configured':False}
    api_key = get('openlux_api_key','')
    ak = get('oss_access_key_id','')
    sk = get('oss_access_key_secret','')
    oat = get('openlux_account_access_token','')
    oui = get('openlux_account_user_id','')
    return {
        'openlux_api_key_configured': bool(api_key),
        'openlux_api_key_masked': _mask(api_key),
        'storage': 'Windows DPAPI（当前用户加密）' if os.name == 'nt' else '本机受限权限文件',
        'oss_access_key_configured': bool(ak),
        'oss_access_key_secret_configured': bool(sk),
        'oss_access_key_masked': _mask(ak),
        'openlux_account_token_configured': bool(oat),
        'openlux_account_token_masked': _mask(oat),
        'openlux_account_user_id_configured': bool(oui),
        'openlux_account_user_id_masked': _mask(oui),
        'persistent_store': str(PATH),
    }
