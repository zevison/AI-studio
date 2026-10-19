"""Authenticated read-only account prices. No guessed currency or group defaults."""
from decimal import Decimal, InvalidOperation
import hashlib
import json
import time
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise PermissionError('Credential redirect refused')


def read_json(path, token=None, user_id=None, *, opener=None):
    if path not in {'/api/status', '/api/pricing', '/api/user/self/groups', '/v1/models'}:
        raise PermissionError('Read-only account route not approved')
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    if user_id:
        headers['New-Api-User'] = str(user_id)
    client = opener or urllib.request.build_opener(NoRedirect(), urllib.request.ProxyHandler({}))
    with client.open(urllib.request.Request('https://api.openlux.ai' + path, headers=headers), timeout=25) as response:
        if response.status != 200:
            raise ValueError('Account read failed')
        raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('Account response too large')
        value = json.loads(raw)
        if not isinstance(value, dict) or value.get('success') is False:
            raise ValueError('Account returned an error')
        return value


def token_quote(vault, model='gpt-6-luna', *, reader=read_json):
    if model != 'gpt-6-luna':
        raise PermissionError('Only the tested Chat tools model is enabled')
    key, account_token = vault.get('openlux_api_key'), vault.get('openlux_account_access_token')
    if not key or not account_token:
        raise PermissionError('Save API key and account read token in Settings first')
    status = reader('/api/status')['data']
    if not isinstance(status, dict) or status.get('currency_type') != 'USD':
        raise PermissionError('Account currency is not verified USD')
    pricing = reader('/api/pricing', key)
    groups = reader('/api/user/self/groups', account_token, vault.get('openlux_account_user_id'))['data']
    models = reader('/v1/models', key)['data']
    if not any(isinstance(row, dict) and row.get('id') == model for row in models):
        raise PermissionError('Model is not available to this API key')
    try:
        unit = Decimal(str(status['quota_per_unit']))
        matches = [r for r in pricing['data'] if r.get('model_name') == model]
        candidates = []
        for row in matches:
            enabled = row.get('enable_groups')
            ratios = groups.get('ratios')
            if row.get('quota_type') != 0 or 'openai' not in row.get('supported_endpoint_types', []):
                continue
            if not isinstance(enabled, list) or not enabled or not isinstance(ratios, dict):
                raise ValueError('Account group prices missing')
            if any(g not in ratios for g in enabled):
                raise ValueError('Account group price incomplete')
            factor = max(Decimal(str(ratios[g])) for g in enabled)
            inp = Decimal(str(row['model_ratio'])) * factor * Decimal(1_000_000) / unit
            out = inp * Decimal(str(row['completion_ratio']))
            if not all(x.is_finite() and x > 0 for x in (unit, inp, out, factor)):
                raise ValueError('Invalid account price')
            candidates.append((inp, out))
        if not candidates:
            raise ValueError('Exact account token quote missing')
    except (KeyError, TypeError, InvalidOperation, ZeroDivisionError) as exc:
        raise PermissionError('Account pricing format not verified') from exc
    return {'model': model, 'input_usd_per_million': str(max(x[0] for x in candidates)),
            'output_usd_per_million': str(max(x[1] for x in candidates)), 'checked_at': time.time(),
            'account': hashlib.sha256(key.encode()).hexdigest()[:24], 'currency': 'USD',
            'basis': 'Current account enabled groups, highest token rate; actual bill is separate'}
