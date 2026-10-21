from __future__ import annotations
import json, os, sqlite3, tempfile, base64
from pathlib import Path

APP_DIR_NAME = 'SeedanceStudio'
OSS_PROFILE_KEYS = (
    'oss_endpoint','oss_bucket','oss_object_prefix',
    'asset_link_ttl_minutes','oss_auto_cleanup','oss_delete_after_hours',
    'public_asset_strategy',
)

def _base_dir() -> Path:
    explicit = os.getenv('STUDIO_PROFILE_HOME', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == 'nt':
        base = os.getenv('LOCALAPPDATA')
        if base:
            return Path(base) / APP_DIR_NAME
    base = os.getenv('XDG_CONFIG_HOME')
    if base:
        return Path(base) / APP_DIR_NAME
    return Path.home() / '.config' / APP_DIR_NAME

BASE_DIR = _base_dir()
PROFILE_PATH = BASE_DIR / 'profile.json'
SECRETS_PATH = BASE_DIR / ('secrets.dpapi' if os.name == 'nt' else 'secrets.json')
LEGACY_SECRETS_PATH = BASE_DIR / 'secrets.json'
SECRET_FIELDS = ('openlux_api_key', 'openlux_account_access_token', 'openlux_account_user_id', 'oss_access_key_id', 'oss_access_key_secret', 'r2_access_key_id', 'r2_secret_access_key', 'brightdata_api_key')

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        v=json.loads(path.read_text(encoding='utf-8'))
        return v if isinstance(v,dict) else {}
    except Exception:
        return {}

def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=path.stem+'_',suffix='.json',dir=str(path.parent))
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
        if os.name!='nt':
            try: os.chmod(path,0o600)
            except Exception: pass
    finally:
        try:
            if os.path.exists(tmp): os.unlink(tmp)
        except Exception: pass

def read_profile() -> dict:
    return _read_json(PROFILE_PATH)

def update_profile(values: dict) -> dict:
    data=read_profile()
    for k in OSS_PROFILE_KEYS:
        if k in values and values[k] is not None:
            data[k]=values[k]
    _write_json(PROFILE_PATH,data)
    return data

# Windows DPAPI is bound to the signed-in Windows account. No extra master
# password, third-party package, or plaintext credential shipped in the ZIP.
def _dpapi(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != 'nt':
        return data
    import ctypes
    from ctypes import wintypes
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD), ('pbData', ctypes.POINTER(ctypes.c_byte))]
    source = ctypes.create_string_buffer(data)
    blob_in = DATA_BLOB(len(data), ctypes.cast(source, ctypes.POINTER(ctypes.c_byte)))
    blob_out = DATA_BLOB()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    crypt32.CryptProtectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_wchar_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.argtypes = [ctypes.POINTER(DATA_BLOB), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    flags = 0x01  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        success = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None, flags, ctypes.byref(blob_out))
    else:
        success = crypt32.CryptProtectData(ctypes.byref(blob_in), 'Seedance Studio local credentials', None, None, None, flags, ctypes.byref(blob_out))
    if not success:
        raise OSError(ctypes.get_last_error(), 'Windows DPAPI 无法读取或保存本机凭据')
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))


def _write_secrets(data: dict) -> None:
    clean = {k: str(v) for k, v in data.items() if k in SECRET_FIELDS and v not in ('', None)}
    if os.name != 'nt':
        _write_json(SECRETS_PATH, clean)
        return
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    protected = _dpapi(json.dumps(clean, ensure_ascii=False).encode('utf-8'))
    fd, tmp = tempfile.mkstemp(prefix='secrets_',suffix='.tmp',dir=str(BASE_DIR))
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(protected)
        os.replace(tmp, SECRETS_PATH)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def read_secrets() -> dict:
    if SECRETS_PATH.exists():
        if os.name == 'nt':
            # Never silently discard a corrupted vault: that could hide a failed
            # upgrade and cause repeated authentication requests.
            value=json.loads(_dpapi(SECRETS_PATH.read_bytes(),decrypt=True).decode('utf-8'))
            if not isinstance(value,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in value.items()):
                raise ValueError('本机凭据格式无效，原文件已保留')
            return value
        return _read_json(SECRETS_PATH)
    if os.name == 'nt' and LEGACY_SECRETS_PATH.exists():
        legacy = _read_json(LEGACY_SECRETS_PATH)
        _write_secrets(legacy)
        # Remove old plaintext only after the encrypted copy has been verified.
        if read_secrets() == {k: str(v) for k,v in legacy.items() if k in SECRET_FIELDS and v not in ('',None)}:
            LEGACY_SECRETS_PATH.unlink(missing_ok=True)
        return read_secrets()
    return {}


def update_secrets(values: dict) -> dict:
    data = read_secrets()
    for k in SECRET_FIELDS:
        if k in values and values[k] is not None:
            v = str(values[k]).strip()
            if v: data[k] = v
    _write_secrets(data)
    return data


def remove_secrets(*keys) -> dict:
    data=read_secrets()
    for key in keys:
        data.pop(key, None)
    _write_secrets(data)
    return data


def clear_oss_secrets() -> dict:
    return remove_secrets('oss_access_key_id','oss_access_key_secret')

def _sqlite_oss_settings(db_path: Path) -> dict:
    if not db_path.exists():
        return {}
    out={}
    try:
        c=sqlite3.connect(db_path)
        try:
            exists=c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='user_settings'").fetchone()
            if not exists:
                return {}
            qmarks=','.join('?' for _ in OSS_PROFILE_KEYS)
            rows=c.execute(f"SELECT key,value_json FROM user_settings WHERE key IN ({qmarks})",OSS_PROFILE_KEYS).fetchall()
            for k,v in rows:
                try: out[k]=json.loads(v)
                except Exception: out[k]=v
        finally:
            c.close()
    except Exception:
        return {}
    return out

def _env_oss_settings(env_path: Path) -> dict:
    if not env_path.exists():
        return {}
    mp={
        'OSS_ENDPOINT':'oss_endpoint',
        'ALIYUN_OSS_ENDPOINT':'oss_endpoint',
        'OSS_BUCKET':'oss_bucket',
        'ALIYUN_OSS_BUCKET':'oss_bucket',
        'OSS_OBJECT_PREFIX':'oss_object_prefix',
    }
    out={}
    try:
        for raw in env_path.read_text(encoding='utf-8-sig',errors='ignore').splitlines():
            s=raw.strip()
            if not s or s.startswith('#') or '=' not in s: continue
            k,v=s.split('=',1); k=k.strip(); v=v.strip().strip('"').strip("'")
            if k in mp and v and mp[k] not in out: out[mp[k]]=v
    except Exception:
        pass
    return out

def _candidate_roots(current_root: Path):
    roots=[current_root]
    parent=current_root.parent
    try:
        siblings=[p for p in parent.iterdir() if p.is_dir() and p.name.startswith('SeedanceStudio_V') and p.resolve()!=current_root.resolve()]
        siblings.sort(key=lambda p:p.stat().st_mtime,reverse=True)
        roots.extend(siblings)
    except Exception:
        pass
    return roots

def _env_secrets(env_path: Path) -> dict:
    if not env_path.exists():
        return {}
    mapping={
        'OPENLUX_API_KEY':'openlux_api_key',
        'OPENLUX_ACCOUNT_ACCESS_TOKEN':'openlux_account_access_token',
        'OPENLUX_ACCOUNT_USER_ID':'openlux_account_user_id',
        'OSS_ACCESS_KEY_ID':'oss_access_key_id',
        'ALIYUN_OSS_ACCESS_KEY_ID':'oss_access_key_id',
        'OSS_ACCESS_KEY_SECRET':'oss_access_key_secret',
        'ALIYUN_OSS_ACCESS_KEY_SECRET':'oss_access_key_secret',
        'R2_ACCESS_KEY_ID':'r2_access_key_id',
        'R2_SECRET_ACCESS_KEY':'r2_secret_access_key',
    }
    out={}
    try:
        for raw in env_path.read_text(encoding='utf-8-sig',errors='ignore').splitlines():
            line=raw.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            name,value=line.split('=',1)
            key=mapping.get(name.strip())
            if key and value.strip().strip('"').strip("'"):
                out[key]=value.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


_bootstrap_done = False

def ensure_bootstrap(current_root: Path) -> dict:
    """Import known credentials and OSS settings from this/sibling versions once.

    Never logs secrets. Windows stores them with per-user DPAPI encryption in
    %LOCALAPPDATA%/SeedanceStudio, so future versions automatically reuse them.
    """
    global _bootstrap_done
    current_root=Path(current_root)
    profile=read_profile()
    secrets=read_secrets()
    if _bootstrap_done:
        return {'profile':profile,'secrets':{k:bool(v) for k,v in secrets.items()},'imported':False}
    need_profile=not (profile.get('oss_endpoint') and profile.get('oss_bucket'))
    imported=False
    # A packaged installation must never silently import a neighbouring user's
    # project. Desktop migration is explicit and journalled, before startup.
    roots = [] if getattr(__import__('sys'), 'frozen', False) or os.getenv('STUDIO_PROFILE_HOME') else _candidate_roots(current_root)
    for root in roots:
        if need_profile:
            vals={}
            vals.update(_env_oss_settings(root/'.env'))
            vals.update(_sqlite_oss_settings(root/'data'/'studio.db'))
            if vals.get('oss_endpoint') or vals.get('oss_bucket'):
                profile.update({k:v for k,v in vals.items() if k in OSS_PROFILE_KEYS and v not in (None,'')})
                _write_json(PROFILE_PATH,profile); imported=True
                need_profile=not (profile.get('oss_endpoint') and profile.get('oss_bucket'))
        credentials=_env_secrets(root/'.env')
        legacy=_read_json(root/'data'/'secrets.json')
        for key in SECRET_FIELDS:
            if key not in secrets and credentials.get(key):
                secrets[key]=credentials[key]; imported=True
            if key not in secrets and legacy.get(key):
                secrets[key]=str(legacy[key]); imported=True
    if imported:
        _write_secrets(secrets)
    _bootstrap_done = True
    return {'profile':profile,'secrets':{k:bool(v) for k,v in secrets.items()},'imported':imported}
