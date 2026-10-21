from __future__ import annotations
from . import db, config, profile_store

# OpenLux exposes three route control methods:
# 1) model suffix, 2) provider.sort, 3) token-level default routing_priority.
ROUTE_CONTROL_METHODS = {
    'model_suffix': {
        'label': '模型名后缀（推荐，优先级最高）',
        'meaning': '直接在模型名后追加路由后缀；最直观，且优先级最高。',
    },
    'provider_sort': {
        'label': 'provider.sort 字段',
        'meaning': '模型名保持不变，通过 provider.sort 指定路由目标。',
    },
    'token_default': {
        'label': 'Token 级默认',
        'meaning': '请求里不指定路由，由 OpenLux API Token 设置页的 routing_priority 接管。',
    },
}

ROUTE_PREFERENCES = {
    'success_rate': {
        'label': '成功率最高（默认 / 推荐）',
        'suffix': ':stable',
        'provider_sort': 'success_rate',
        'meaning': '优先选择成功率最高的渠道。',
    },
    'speed': {
        'label': '速度最快',
        'suffix': ':nitro',
        'provider_sort': 'speed',
        'meaning': '优先选择速度最快的渠道。',
    },
    'price': {
        'label': '价格最低',
        'suffix': ':floor',
        'provider_sort': 'price',
        'meaning': '优先选择价格最低的渠道。',
    },
}

DEFAULTS = {
    'output_directory': '',
    'ffmpeg_path': '',
    'ffprobe_path': '',
    'chromium_path': '',
    'video_base_model': config.VIDEO_MODEL,
    # Default uses OpenLux's recommended highest-priority mechanism and prioritizes success rate.
    'video_route_control': 'model_suffix',
    'video_route_preference': 'success_rate',
    'video_endpoint_strategy': 'recommended',
    'custom_video_create_path': config.VIDEO_CREATE_PATH,
    'custom_video_query_path': config.VIDEO_QUERY_PATH,

    'default_video_mode': 'auto',
    'default_video_ratio': config.DEFAULT_RATIO,
    'default_video_resolution': config.DEFAULT_RESOLUTION,
    'default_video_duration': config.DEFAULT_DURATION,
    'default_generate_audio': True,
    'default_audio_mode': 'original',
    'default_image_ratio': '9:16',
    'image_quality': 'auto',

    'dedupe_seconds': config.DEDUPE_SECONDS,
    'max_concurrent_video_tasks': config.MAX_CONCURRENT_VIDEO_TASKS,
    'http_timeout': config.HTTP_TIMEOUT,
    'query_retries': config.QUERY_RETRIES,
    'poll_interval': config.POLL_INTERVAL,

    'replica_inherit_duration': True,
    'replica_inherit_ratio': True,
    'replica_scene_threshold': 0.30,
    'replica_max_scenes': 24,
    'replica_transcript_mode': 'if_installed',
    'replica_ocr_mode': 'if_installed',
    'replica_require_analysis_gate': True,
    'replica_require_word_anchor_for_paid': False,
    'replica_spatial_analysis': True,
    'auto_quality_check': True,

    'tunnel_auto_start': False,
    'tunnel_selftest_before_paid': True,
    'asset_link_ttl_minutes': 720,
    'public_asset_strategy': 'oss_auto',
    'fixed_public_asset_base_url': '',
    'oss_endpoint': config.OSS_ENDPOINT,
    'oss_bucket': config.OSS_BUCKET,
    'oss_object_prefix': config.OSS_OBJECT_PREFIX,
    'oss_auto_cleanup': True,
    'oss_delete_after_hours': 48,
    'cache_retention_days': 30,

    # 全局成本系统。默认 720p 区间来自近期本机 OpenLux Seedance 账单样本；
    # 只是预算提示，不替代 OpenLux 最终账单。
    'cost_auto_balance': True,
    'cost_manual_balance': 0.0,
    'cost_quota_per_usd': 500000.0,
    'cost_video_720_low_per_second': 0.21,
    'cost_video_720_high_per_second': 0.32,
    'cost_video_480_multiplier': 0.75,
    'cost_video_1080_multiplier': 1.50,
    # 当 OpenLux /api/pricing 暂时不可用时，启用备用公式：
    # 输出视频秒数 × 人民币/秒 × CNY→USD 最新可用汇率。
    'cost_fallback_cny_per_second': 1.0,
    'cost_fx_refresh_minutes': 30,

    # 本地付费保护：预算上限与异常消费熔断（0 表示不启用该项）。
    # 只统计本地 usage_events 中已记录的费用，不等于 OpenLux 最终账单。
    'paid_budget_usd': 5.0,
    'paid_budget_window_hours': 24,
    'paid_budget_max_tasks': 20,
}

ALLOWED = {
    'video_route_control': set(ROUTE_CONTROL_METHODS),
    'video_route_preference': set(ROUTE_PREFERENCES),
    'video_endpoint_strategy': {'recommended', 'custom'},
    'default_audio_mode': {'original','model','silent'},
    'default_video_mode': {'auto', 'text', 'image_reference', 'first_frame', 'first_last', 'reference', 'edit', 'extend'},
    'default_video_ratio': {'16:9', '9:16', '1:1', '4:3', '3:4', '21:9', 'adaptive'},
    'default_video_resolution': {'480p', '720p', '1080p'},
    'default_image_ratio': {'auto', '1:1', '4:5', '3:4', '9:16', '16:9'},
    'image_quality': {'auto', 'low', 'medium', 'high'},
    'replica_transcript_mode': {'off', 'if_installed'},
    'replica_ocr_mode': {'off', 'if_installed'},
    'public_asset_strategy': {'oss_auto', 'oss_only', 'fixed_only', 'quick_only'},
}

BOOL_KEYS = {
    'default_generate_audio', 'replica_inherit_duration', 'replica_inherit_ratio',
    'replica_require_analysis_gate', 'replica_require_word_anchor_for_paid', 'replica_spatial_analysis', 'auto_quality_check',
    'tunnel_auto_start', 'tunnel_selftest_before_paid', 'oss_auto_cleanup', 'cost_auto_balance'
}

INT_RANGES = {
    'default_video_duration': (4, 30),
    'replica_max_scenes': (5, 60),
    'asset_link_ttl_minutes': (5, 10080),
    'oss_delete_after_hours': (1, 720),
    'cache_retention_days': (1, 3650),
    'dedupe_seconds': (1, 120),
    'max_concurrent_video_tasks': (1, 10),
    'http_timeout': (30, 300),
    'query_retries': (0, 5),
    'poll_interval': (2, 120),
    'cost_fx_refresh_minutes': (5, 1440),
    'paid_budget_window_hours': (1, 720),
    'paid_budget_max_tasks': (0, 200),
}

FLOAT_RANGES = {
    'replica_scene_threshold': (0.10, 0.80),
    'cost_manual_balance': (0.0, 100000000.0),
    'cost_quota_per_usd': (1.0, 1000000000000.0),
    'cost_video_720_low_per_second': (0.0, 10000.0),
    'cost_video_720_high_per_second': (0.0, 10000.0),
    'cost_video_480_multiplier': (0.01, 10.0),
    'cost_video_1080_multiplier': (0.01, 10.0),
    'cost_fallback_cny_per_second': (0.01, 100.0),
    'paid_budget_usd': (0.0, 100000.0),
}


def _strip_route_suffix(model: str) -> str:
    model = str(model or '').strip()
    for suffix in (':stable', ':nitro', ':floor'):
        if model.endswith(suffix):
            return model[:-len(suffix)]
    return model


def _legacy_route_migration(out: dict, rows: list[dict]) -> dict:
    raw = {r['key']: db.loads(r['value_json'], None) for r in rows}
    # V2.5.1 stored stable/nitro/floor as if they were route-control methods.
    # Migrate them to the correct "model suffix + preference" representation.
    if 'video_route_control' not in raw and 'video_route_preference' not in raw:
        legacy = str(raw.get('video_route_mode') or '')
        mp = {'stable': 'success_rate', 'nitro': 'speed', 'floor': 'price'}
        if legacy in mp:
            out['video_route_control'] = 'model_suffix'
            out['video_route_preference'] = mp[legacy]
    return out


def get_all() -> dict:
    try: profile_store.ensure_bootstrap(config.ROOT)
    except (OSError, ValueError, UnicodeError): pass
    out = dict(DEFAULTS)
    rows = db.all_('SELECT key,value_json FROM user_settings')
    raw_keys={r['key'] for r in rows}
    shared=profile_store.read_profile()
    for k in profile_store.OSS_PROFILE_KEYS:
        if k not in raw_keys and k in shared and shared[k] not in (None,''):
            out[k]=shared[k]
    for r in rows:
        if r['key'] in out:
            out[r['key']] = db.loads(r['value_json'], out[r['key']])
    out = _legacy_route_migration(out, rows)
    from . import configuration_service
    out.update(configuration_service.runtime_overrides())
    try:
        selected=configuration_service.provider_preferences('video')
        if selected: out['video_base_model']=selected['model']
    except ValueError: pass
    if int(out.get('dedupe_seconds') or 0)<30: out['dedupe_seconds']=30  # V4.2.8 minimum retry guard
    legacy_public = str(out.get('public_asset_strategy') or '')
    if legacy_public in ('auto','r2_auto','r2_only'):
        out['public_asset_strategy'] = 'oss_auto' if legacy_public != 'r2_only' else 'oss_only'
    out['video_base_model'] = _strip_route_suffix(out.get('video_base_model') or config.VIDEO_MODEL) or _strip_route_suffix(config.VIDEO_MODEL)
    return out


def get(key, default=None):
    if key not in DEFAULTS:
        return default
    return get_all().get(key, default if default is not None else DEFAULTS[key])


def _validate(key, value):
    if key not in DEFAULTS:
        raise ValueError(f'不支持的设置项：{key}')
    if key in {'output_directory', 'ffmpeg_path', 'ffprobe_path', 'chromium_path'}:
        from pathlib import Path, PureWindowsPath
        value = str(value or '').strip()
        if not value: return ''
        if (not (Path(value).is_absolute() or PureWindowsPath(value).is_absolute())
            or value.startswith(('\\\\', '//')) or '\x00' in value):
            raise ValueError('运行组件和输出目录必须使用绝对本地路径，不允许网络共享或相对路径')
        if key != 'output_directory' and PureWindowsPath(value).suffix.lower() != '.exe':
            raise ValueError('Windows 本地组件路径必须指向 .exe 文件')
        return value
    if key in BOOL_KEYS:
        if isinstance(value, str):
            value = value.lower() in ('1', 'true', 'yes', 'on')
        return bool(value)
    if key in ALLOWED:
        value = str(value)
        if value not in ALLOWED[key]:
            raise ValueError(f'{key} 的值不受支持')
        return value
    if key in INT_RANGES:
        lo, hi = INT_RANGES[key]
        value = int(value)
        if not lo <= value <= hi:
            raise ValueError(f'{key} 必须在 {lo}–{hi} 之间')
        return value
    if key in FLOAT_RANGES:
        lo, hi = FLOAT_RANGES[key]
        value = float(value)
        if not lo <= value <= hi:
            raise ValueError(f'{key} 必须在 {lo}–{hi} 之间')
        return round(value, 3)
    if key == 'video_base_model':
        value = _strip_route_suffix(str(value or '').strip())
        if not value:
            raise ValueError('基础视频模型不能为空')
        if any(ch.isspace() for ch in value):
            raise ValueError('基础视频模型 ID 不能包含空格')
        return value
    if key in {'custom_video_create_path', 'custom_video_query_path'}:
        value = str(value or '').strip()
        if not value:
            raise ValueError(f'{key} 不能为空')
        from .configuration_service import validate_path
        return validate_path(value)
    if key in {'oss_endpoint','oss_bucket','oss_object_prefix'}:
        value = str(value or '').strip()
        if key == 'oss_object_prefix':
            value = value.strip('/') or 'seedance-studio'
        if key == 'oss_endpoint':
            value = value.rstrip('/')
            if value and not value.startswith(('http://','https://')):
                value = 'https://' + value
            if value and '-internal.aliyuncs.com' in value:
                raise ValueError('OSS Endpoint 不能使用 internal 内网地址；OpenLux / Seedance 需要公网可读地址')
        return value
    if key == 'fixed_public_asset_base_url':
        value = str(value or '').strip().rstrip('/')
        if value and not value.startswith('https://'):
            raise ValueError('固定公网素材地址必须以 https:// 开头')
        return value
    return value


def update(values: dict) -> dict:
    from . import configuration_service
    validated={k:_validate(k,v) for k,v in (values or {}).items() if k in DEFAULTS}
    configuration_service.merge_legacy_settings(validated)
    now = db.now()
    shared_updates={}
    for k, val in validated.items():
        db.execute(
            '''INSERT INTO user_settings(key,value_json,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at''',
            (k, db.dumps(val), now),
        )
        if k in profile_store.OSS_PROFILE_KEYS:
            shared_updates[k]=val
    if shared_updates:
        profile_store.update_profile(shared_updates)
    return get_all()


def reset_defaults() -> dict:
    from . import configuration_service
    configuration_service.merge_legacy_settings({k:v for k,v in DEFAULTS.items() if k not in profile_store.OSS_PROFILE_KEYS})
    # Restore model/UI defaults without deleting the user's persistent OSS profile.
    # OSS is infrastructure, not a disposable UI preference.
    for k in DEFAULTS:
        if k in profile_store.OSS_PROFILE_KEYS:
            continue
        db.execute('DELETE FROM user_settings WHERE key=?', (k,))
    db.execute("DELETE FROM user_settings WHERE key='video_route_mode'")
    return get_all()


def base_video_model(cfg: dict | None = None) -> str:
    cfg = cfg or get_all()
    return _strip_route_suffix(cfg.get('video_base_model') or config.VIDEO_MODEL) or 'doubao-seedance-2-5-260628'


def route_info(cfg: dict | None = None) -> dict:
    cfg = cfg or get_all()
    control = str(cfg.get('video_route_control') or 'model_suffix')
    if control not in ROUTE_CONTROL_METHODS:
        control = 'model_suffix'
    preference = str(cfg.get('video_route_preference') or 'success_rate')
    if preference not in ROUTE_PREFERENCES:
        preference = 'success_rate'

    base = base_video_model(cfg)
    pref = ROUTE_PREFERENCES[preference]
    request_model = base
    provider = None

    if control == 'model_suffix':
        request_model = base + pref['suffix']
    elif control == 'provider_sort':
        provider = {'sort': pref['provider_sort']}
    elif control == 'token_default':
        # No request-side route override: OpenLux token routing_priority controls the route.
        provider = None

    return {
        'control': control,
        'control_label': ROUTE_CONTROL_METHODS[control]['label'],
        'control_meaning': ROUTE_CONTROL_METHODS[control]['meaning'],
        'preference': preference if control != 'token_default' else None,
        'preference_label': pref['label'] if control != 'token_default' else '由 OpenLux Token 设置决定',
        'preference_meaning': pref['meaning'] if control != 'token_default' else '本地请求不覆盖 Token 的 routing_priority。',
        'base_model': base,
        'request_model': request_model,
        'provider': provider,
        'provider_sort': provider.get('sort') if provider else None,
        'suffix': pref['suffix'] if control == 'model_suffix' else '',
    }


def effective_video_model(cfg: dict | None = None) -> str:
    return route_info(cfg)['request_model']


def effective_video_provider(cfg: dict | None = None) -> dict | None:
    return route_info(cfg)['provider']


def effective_video_paths(cfg: dict | None = None) -> tuple[str, str]:
    cfg = cfg or get_all()
    from . import configuration_service
    selected=configuration_service.provider_preferences('video')
    if selected:
        return selected['paths']['video'],selected['paths']['video_query']
    if cfg.get('video_endpoint_strategy') == 'custom':
        return (
            str(cfg.get('custom_video_create_path') or config.VIDEO_CREATE_PATH),
            str(cfg.get('custom_video_query_path') or config.VIDEO_QUERY_PATH),
        )
    return config.VIDEO_CREATE_PATH, config.VIDEO_QUERY_PATH


def effective_limits(cfg: dict | None = None) -> dict:
    cfg = cfg or get_all()
    return {
        'dedupe_seconds': int(cfg.get('dedupe_seconds', config.DEDUPE_SECONDS)),
        'max_concurrent_video_tasks': int(cfg.get('max_concurrent_video_tasks', config.MAX_CONCURRENT_VIDEO_TASKS)),
        'http_timeout': int(cfg.get('http_timeout', config.HTTP_TIMEOUT)),
        'query_retries': int(cfg.get('query_retries', config.QUERY_RETRIES)),
    }
