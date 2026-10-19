"""Never let generic transport options replay a chargeable POST."""
import urllib.error
from unittest.mock import patch
import pytest
from core import openlux,network_trust


def test_paid_post_ignores_requested_retries(monkeypatch):
    monkeypatch.setattr(openlux.config,'OPENLUX_API_KEY','test-only')
    with patch.object(openlux.urllib.request,'urlopen',side_effect=TimeoutError()) as send:
        with pytest.raises(openlux.ApiError):
            openlux.request_json('POST','/v1/chat/completions',{'model':'fixture'},retries=3)
    assert send.call_count==1


def test_account_write_also_never_retries():
    with patch.object(openlux.urllib.request,'urlopen',side_effect=TimeoutError()) as send:
        with pytest.raises(openlux.ApiError):
            openlux.request_json_with_token('POST','/api/example','test-only',{},retries=3)
    assert send.call_count==1


def test_trust_is_not_disabled():
    import ssl
    context=network_trust.tls_context()
    assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    text=network_trust.network_error_message(urllib.error.URLError(ssl.SSLCertVerificationError()))
    assert '证书校验失败' in text
