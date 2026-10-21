"""Guard the executable harness from inheriting production identities."""
import importlib.util
from pathlib import Path
import urllib.error

import pytest

SPEC = importlib.util.spec_from_file_location('desktop_acceptance', Path(__file__).resolve().parents[1] / 'tools/accept_desktop_exe.py')
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def test_harness_does_not_inherit_real_keys_profiles_or_python_hooks(monkeypatch):
    for name in ('OPENLUX_API_KEY','OSS_ACCESS_KEY_SECRET','ALIYUN_OSS_ACCESS_KEY_SECRET',
                 'STUDIO_PROFILE_HOME','STUDIO_DATA_HOME','PYTHONPATH','BRIGHTDATA_API_KEY',
                 'R2_ACCESS_KEY_ID','CUSTOM_AUTH_TOKEN'):
        monkeypatch.setenv(name,'production-must-not-be-used')
    environment=harness.clean_environment()
    assert 'production-must-not-be-used' not in environment.values()
    assert environment['OPEN_BROWSER']=='0'


def test_expected_denial_cannot_be_counted_when_request_succeeds():
    with pytest.raises(AssertionError,match='accepted'):
        harness.expect_http_error(lambda: {'ok':True},403)


def test_different_http_error_does_not_pass_authorization_check():
    def server_error():
        raise urllib.error.HTTPError('http://127.0.0.1/',500,'failure',None,None)
    with pytest.raises(AssertionError):
        harness.expect_http_error(server_error,403)


@pytest.mark.parametrize('route',['https://api.example/paid','//api.example/paid','relative'])
def test_http_client_rejects_nonlocal_route(route,tmp_path):
    app=harness.Application(tmp_path/'program.exe',tmp_path/'home',tmp_path,'case')
    with pytest.raises(ValueError,match='Local'):
        app.request(route)
