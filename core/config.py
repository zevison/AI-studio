from __future__ import annotations
import os
from pathlib import Path

from .runtime_layout import resource_root, user_home
from .configuration_service import startup_options
STARTUP_OPTIONS = startup_options()
ROOT = resource_root()
USER_HOME = user_home()
DATA_ROOT = USER_HOME / 'data'
# An authenticated workspace is a separate process and database, not a UI filter.
WORKSPACE_ID = os.getenv('STUDIO_WORKSPACE_ID','owner')
if WORKSPACE_ID!='owner' and not __import__('re').fullmatch('[a-f0-9]{24}',WORKSPACE_ID):
    raise RuntimeError('Invalid workspace identity')
DATA_DIR = DATA_ROOT if WORKSPACE_ID=='owner' else DATA_ROOT / 'workspaces' / WORKSPACE_ID
WORKSPACE_TOKEN = os.getenv('STUDIO_WORKSPACE_TOKEN','')
if WORKSPACE_ID!='owner' and len(WORKSPACE_TOKEN)<32:raise RuntimeError('Isolated workspace requires a gateway token')
ASSET_DIR = DATA_DIR / 'assets'
OUTPUT_DIR = Path(STARTUP_OPTIONS['output_directory']) if WORKSPACE_ID=='owner' and STARTUP_OPTIONS.get('output_directory') else DATA_DIR / 'outputs'
CACHE_DIR = DATA_DIR / 'cache'
STATIC_DIR = ROOT / 'static'
DB_PATH = DATA_DIR / 'studio.db'
LOG_PATH = (USER_HOME if WORKSPACE_ID=='owner' else DATA_DIR) / 'seedance_studio.log'
PID_PATH = (USER_HOME if WORKSPACE_ID=='owner' else DATA_DIR) / 'seedance_studio.pid'

for p in (DATA_DIR, ASSET_DIR, CACHE_DIR):
    p.mkdir(parents=True, exist_ok=True)
OUTPUT_DIRECTORY_ERROR = ''
try:
    OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
except OSError:
    OUTPUT_DIRECTORY_ERROR = '指定输出目录不可用，已使用本机默认目录。请在设置中检查目录权限后重启。'
    OUTPUT_DIR = DATA_DIR / 'outputs'
    OUTPUT_DIR.mkdir(parents=True,exist_ok=True)
from . import output_registry
try:
    output_registry.register(USER_HOME if WORKSPACE_ID=='owner' else DATA_DIR,OUTPUT_DIR,DATA_DIR/'outputs')
except (OSError,ValueError,TypeError):
    OUTPUT_DIRECTORY_ERROR = '输出目录历史无法更新，原记录和成片已保留；请检查目录权限或恢复历史文件。'


def load_env(path: Path | None = None) -> None:
    path = path or USER_HOME / '.env'
    if not path.exists():
        return
    for raw in path.read_text(encoding='utf-8-sig').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip(); value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_external_openlux_key = os.environ.get('OPENLUX_API_KEY', '').strip()
load_env()

# Once stored, credentials live outside the version folder, protected by the
# current Windows user account (DPAPI). A new installation auto-imports
# previous sibling versions' .env and OSS settings without showing secrets.
from . import profile_store
try:
    profile_store.ensure_bootstrap(ROOT)
    _vault = profile_store.read_secrets()
    CREDENTIAL_ERROR = ''
except (OSError, ValueError, UnicodeError):
    # The settings UI must remain available to repair a damaged/cross-user vault.
    # Keep the original bytes; never silently replace unreadable credentials.
    _vault = {}
    CREDENTIAL_ERROR = '本机凭据无法解密或已损坏；原文件已保留，请在设置中重新授权。'
if not _external_openlux_key and _vault.get('openlux_api_key'):
    # A key changed in Settings must win over an old .env left in the folder.
    os.environ['OPENLUX_API_KEY'] = _vault['openlux_api_key']

APP_VERSION = '5.2.0'
APP_BUILD = 'R3.20'
APP_EDITION = '人工爆款复查与真实视觉脚本版'
HOST = os.getenv('HOST', '127.0.0.1')
PORT = int(os.getenv('PORT', '8765'))
ASSET_HOST = os.getenv('ASSET_HOST', '127.0.0.1')
ASSET_PORT = int(os.getenv('ASSET_PORT', '8766'))
OPEN_BROWSER = os.getenv('OPEN_BROWSER', '1') == '1'

OPENLUX_API_KEY = os.getenv('OPENLUX_API_KEY', '').strip()
OPENLUX_BASE_URL = os.getenv('OPENLUX_BASE_URL', 'https://api.openlux.ai').rstrip('/')
IMAGE_CREATE_PATH = os.getenv('IMAGE_CREATE_PATH', '/v1/images/generations')
IMAGE_EDIT_PATH = os.getenv('IMAGE_EDIT_PATH', '/v1/images/edits')
VIDEO_CREATE_PATH = os.getenv('VIDEO_CREATE_PATH', '/api/v3/contents/generations/tasks')
VIDEO_QUERY_PATH = os.getenv('VIDEO_QUERY_PATH', '/api/v3/contents/generations/tasks/{task_id}')

# 当前唯一默认模型。后续如 OpenLux 更换模型 ID，只改 .env，不需要改前端。
IMAGE_MODEL = os.getenv('IMAGE_MODEL', 'gpt-image-2.5-sunburst-c')
VIDEO_MODEL = os.getenv('VIDEO_MODEL', 'doubao-seedance-2-5-260628')

DEFAULT_RESOLUTION = os.getenv('DEFAULT_RESOLUTION', '720p')
DEFAULT_RATIO = os.getenv('DEFAULT_RATIO', '9:16')
DEFAULT_DURATION = int(os.getenv('DEFAULT_DURATION', '10'))
MAX_UPLOAD_MB = int(os.getenv('MAX_UPLOAD_MB', '220'))
DEDUPE_SECONDS = int(os.getenv('DEDUPE_SECONDS', '30'))
PREVIEW_TTL_SECONDS = int(os.getenv('PREVIEW_TTL_SECONDS', '600'))
MAX_CONCURRENT_VIDEO_TASKS = int(os.getenv('MAX_CONCURRENT_VIDEO_TASKS', '10'))

# 费用估算仅用于界面预览；0/空白代表未知，不代表免费。
IMAGE_ESTIMATED_COST_USD = float(os.getenv('IMAGE_ESTIMATED_COST_USD', '0') or 0)
VIDEO_ESTIMATED_COST_PER_SECOND_USD = float(os.getenv('VIDEO_ESTIMATED_COST_PER_SECOND_USD', '0') or 0)

# 为避免重复扣费，所有付费创建请求都不自动重试。
PAID_CREATE_RETRIES = 0
QUERY_RETRIES = int(os.getenv('QUERY_RETRIES', '1'))
HTTP_TIMEOUT = int(os.getenv('HTTP_TIMEOUT', '90'))
POLL_INTERVAL = int(os.getenv('POLL_INTERVAL', '6'))

CLOUDFLARED_PATH = os.getenv('CLOUDFLARED_PATH', '').strip()
TUNNEL_ENABLED = os.getenv('TUNNEL_ENABLED', '1') == '1'

# Aliyun OSS optional manual configuration. Web settings override non-secret fields;
# credentials in data/secrets.json override env values. ALIYUN_OSS_* aliases are also accepted.
OSS_ENDPOINT = (os.getenv('OSS_ENDPOINT') or os.getenv('ALIYUN_OSS_ENDPOINT') or '').strip().rstrip('/')
OSS_BUCKET = (os.getenv('OSS_BUCKET') or os.getenv('ALIYUN_OSS_BUCKET') or '').strip()
OSS_ACCESS_KEY_ID = (os.getenv('OSS_ACCESS_KEY_ID') or os.getenv('ALIYUN_OSS_ACCESS_KEY_ID') or '').strip()
OSS_ACCESS_KEY_SECRET = (os.getenv('OSS_ACCESS_KEY_SECRET') or os.getenv('ALIYUN_OSS_ACCESS_KEY_SECRET') or '').strip()
OSS_OBJECT_PREFIX = (os.getenv('OSS_OBJECT_PREFIX') or os.getenv('ALIYUN_OSS_OBJECT_PREFIX') or 'seedance-studio').strip().strip('/') or 'seedance-studio'

# Seedance 媒体 content 字段默认采用火山方舟官方嵌套格式：
# {"type":"image_url","image_url":{"url":"..."},"role":"reference_image"}
# 如 OpenLux 某个渠道仍使用旧扁平格式，可在 .env 设置 VIDEO_MEDIA_SHAPE=legacy_flat。
VIDEO_MEDIA_SHAPE = os.getenv('VIDEO_MEDIA_SHAPE', 'official_nested').strip().lower()
