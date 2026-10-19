"""Account-bound Seedance 2.5 estimates, using the verified console pricing table.

Estimates are NOT a provider-enforced maximum charge. Unknown dimensions,
automatic durations and missing group rates must never silently become zero.
"""
from decimal import Decimal, InvalidOperation, ROUND_CEILING
import hashlib
import time

from .hypit_account import read_json
from .hypit_openlux_gateway import MODEL_NAME, request_sha256

CONSOLE = 'https://www.openlux.ai/console/models/detail?tab=av&name=' + MODEL_NAME
FORMULA = 'https://docs.volcengine.com/docs/ark/model-pricing?lang=en&redirect=1'
# Console public client 0au1nsqxqj~e2.js, verified 2026-10-16.
TABLE_REVISION = 'openlux-seedance25-20261016'
FACTORS = {'720p': (1070, 640), '480p': (1070, 640), '1080p': (1170, 700)}


def quote_video(vault, payload, *, width, height, input_video_seconds=0, reader=read_json):
    key = vault.get('openlux_api_key')
    token = vault.get('openlux_account_access_token')
    if not key or not token:
        raise PermissionError('API key and account read token are required')
    if payload.get('model') != MODEL_NAME or payload.get('resolution') not in FACTORS:
        raise PermissionError('Exact Seedance 2.5 pricing required')
    duration = payload.get('duration')
    if type(duration) is not int or not (4 <= duration <= 30 or duration==-1):
        raise PermissionError('Output duration cannot be quoted safely')
    # Only verified, fixed output sizes are eligible for paid authorization.
    sizes = {'480p': {(854,480),(480,854)},
             '720p': {(1280,720),(720,1280),(960,960)},
             '1080p': {(1920,1080),(1080,1920)}}
    if (width, height) not in sizes[payload['resolution']]:
        raise PermissionError('Output dimensions have no verified account quote')
    try:
        seconds = Decimal(str(input_video_seconds))
        if not seconds.is_finite() or not 0 <= seconds <= 30:
            raise ValueError('Invalid input duration')
        has_video = any(x.get('type') == 'video_url' for x in payload.get('content', []))
        if has_video != (seconds > 0):
            raise ValueError('Video pricing must match measured references')
        if duration==-1:
            if payload.get('omni_reference_task_type')=='edit' and has_video and 4<=seconds<=30:
                # Official 2.5 editing preserves the single source duration
                # (it may shorten it by <=0.4 s); generic auto generation does not.
                output_seconds=seconds
            elif payload.get('omni_reference_task_type') in {'reference','extend'}:
                output_seconds=Decimal(30)
            else:raise PermissionError('Unbounded automatic duration is not eligible')
        else:output_seconds=Decimal(duration)
        status = reader('/api/status')['data']
        if status.get('currency_type') != 'USD':
            raise PermissionError('Verified USD account required')
        rows = reader('/api/pricing', key)['data']
        groups = reader('/api/user/self/groups', token, vault.get('openlux_account_user_id'))['data']
        models = reader('/v1/models', key)['data']
        if not any(x.get('id') == MODEL_NAME for x in models):
            raise PermissionError('Seedance 2.5 not available to this key')
        matches = [x for x in rows if x.get('model_name') == MODEL_NAME]
        if len(matches) != 1:
            raise PermissionError('Exact account price is ambiguous')
        row = matches[0]
        # Refuse changes to the base/table protocol pending re-verification.
        base = Decimal(str(row['model_price']))
        enabled = row['enable_groups']
        if base != Decimal('0.01') or not isinstance(enabled, list) or not enabled:
            raise PermissionError('Console pricing revision changed')
        ratios = [Decimal(str(groups['ratios'][g])) for g in enabled]
        if any(not x.is_finite() or x <= 0 for x in ratios):
            raise PermissionError('Account group price invalid')
        rate = base * max(ratios) * FACTORS[payload['resolution']][int(has_video)]
        tokens = ((seconds + output_seconds) * width * height * 24 / 1024).to_integral_value(rounding=ROUND_CEILING)
        estimate = tokens * rate / 1_000_000
    except (KeyError, TypeError, InvalidOperation, ValueError) as exc:
        raise PermissionError('Complete account video pricing could not be verified') from exc
    return {'account': hashlib.sha256(key.encode()).hexdigest()[:24],
            'request_hash': request_sha256(payload), 'currency': 'USD',
            'usd_per_million_tokens': str(rate), 'estimated_tokens': int(tokens),
            'estimated_usd': str(estimate), 'checked_at': time.time(),
            'output_seconds_bound':str(output_seconds),
            'width':width,'height':height,'fps':24,
            'minimum_output_seconds':str((Decimal(4) if duration==-1 and payload.get('omni_reference_task_type')!='edit' else output_seconds)-Decimal('.5')),
            'maximum_output_seconds':str(output_seconds+Decimal('.5')),
            'planning_reserve_usd': str((estimate*Decimal('1.25')).quantize(Decimal('.000001'),rounding=ROUND_CEILING)),
            'table_revision': TABLE_REVISION, 'sources': [CONSOLE, FORMULA],
            'basis': 'Highest enabled account group, 24 fps; actual provider bill is separate',
            'provider_enforced_charge_cap': False}
