"""Offline configuration security and real request-construction contracts."""
import copy
import io
import json
import socket
import sqlite3
import urllib.error
import urllib.request
import pytest
from core import configuration_service as service, profile_store, config, db, openlux, provider_transport

@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(profile_store, 'BASE_DIR', tmp_path / 'profile')
    monkeypatch.setattr(profile_store, 'PROFILE_PATH', tmp_path / 'profile' / 'profile.json')
    monkeypatch.setattr(profile_store, 'SECRETS_PATH', tmp_path / 'profile' / ('secrets.dpapi' if __import__('os').name == 'nt' else 'secrets.json'))
    monkeypatch.setattr(profile_store, 'LEGACY_SECRETS_PATH', tmp_path / 'profile' / 'secrets.json')
    monkeypatch.setattr(profile_store, '_bootstrap_done', True)
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'studio.db')
    monkeypatch.setattr(config, 'OPENLUX_API_KEY', '')
    monkeypatch.delenv('OPENLUX_API_KEY', raising=False)
    db.init_db()
    return service.snapshot()

def configured(document):
    document = copy.deepcopy(document)
    row = document['providers'][0]
    row['base_url'] = 'https://provider.example.com/v1'
    row['models']['chat'] = 'my-chat-model'
    row['parameters']['chat'] = {'temperature': 0.4, 'max_tokens': 1024}
    return document

def test_provider_roundtrip_and_no_secret_export(isolated):
    document = configured(isolated)
    result = service.update_document(document, 0, {'legacy-openlux': 'fixture-key-only'})
    assert result['revision'] == 1
    selected = service.resolve_provider('chat')
    assert selected.base_url == 'https://provider.example.com/v1'
    assert selected.model == 'my-chat-model'
    assert selected.api_key == 'fixture-key-only'
    assert dict(selected.parameters) == {'temperature': 0.4, 'max_tokens': 1024}
    assert result['providers'][0]['key_configured'] is True
    assert 'fixture-key-only' not in json.dumps(result)
    assert 'fixture-key-only' not in service._path().read_text(encoding='utf-8')
    assert 'credential_ref' not in json.dumps(service.export_document())
    with pytest.raises(TypeError): selected.paths['chat'] = '/nope'

def test_revision_conflict_keeps_previous_config(isolated):
    service.update_document(configured(isolated), 0)
    before = service._path().read_bytes()
    with pytest.raises(service.ConfigurationConflict): service.update_document(isolated, 0)
    assert service._path().read_bytes() == before

@pytest.mark.parametrize('url', ['http://provider.example.com', 'https://127.0.0.1', 'https://[::1]',
    'https://user:secret@provider.example.com', 'https://provider.example.com:8080',
    'https://localhost', 'https://something.local', 'https://provider.example.com/?key=hidden',
    'https://169.254.169.254', 'file:///C:/secrets.txt', 'https://provider.example.com\\@127.0.0.1'])
def test_disallow_unsafe_origins(isolated, url):
    document = configured(isolated); document['providers'][0]['base_url'] = url
    with pytest.raises(ValueError): service.update_document(document, 0)
    assert not service._path().exists()

@pytest.mark.parametrize('path', ['https://evil.example/v1/chat', '//evil.example/x', '/v1/../secrets', '/x\r\nAuthorization:evil'])
def test_no_cross_origin_paths(isolated, path):
    document = configured(isolated); document['providers'][0]['paths']['chat'] = path
    with pytest.raises(ValueError): service.update_document(document, 0)

def test_custom_url_never_receives_original_account_token(isolated):
    service.update_document(configured(isolated), 0)
    assert openlux._account_url('/api/user/self').startswith(config.OPENLUX_BASE_URL.rstrip('/') + '/')
    with pytest.raises(ValueError): openlux._url('https://evil.example/v1/chat/completions')

@pytest.mark.parametrize('state', ['pending', 'running', 'result_pending', 'submission_unknown'])
def test_active_paid_task_blocks_provider_and_key_switch(isolated, state):
    db.execute('INSERT INTO tasks(id,kind,status) VALUES(?,?,?)', ('task-fixture', 'video', state))
    with pytest.raises(service.ConfigurationConflict): service.update_document(configured(isolated), 0, {'legacy-openlux': 'fixture-key'})
    assert not service._path().exists()
    assert not service._vault_path().exists()

def test_provider_secret_publish_crash_preserves_old_binding(isolated, monkeypatch):
    service.update_document(configured(isolated), 0, {'legacy-openlux': 'old-fixture-key'})
    document = service.snapshot(); document['providers'][0]['base_url'] = 'https://new.example.com'
    atomic = service._atomic
    def crash(path, data):
        if path == service._path(): raise OSError('simulated power loss')
        atomic(path, data)
    monkeypatch.setattr(service, '_atomic', crash)
    with pytest.raises(OSError): service.update_document(document, 1, {'legacy-openlux': 'new-fixture-key'})
    assert service.resolve_provider('chat').api_key == 'old-fixture-key'
    assert service.resolve_provider('chat').base_url == 'https://provider.example.com/v1'
    assert service.snapshot()['revision'] == 1

def test_new_profile_cannot_reuse_opaque_credential_reference(isolated):
    service.update_document(configured(isolated), 0, {'legacy-openlux': 'fixture-only'})
    document = service.snapshot(); duplicate = copy.deepcopy(document['providers'][0]); duplicate['id'] = 'other-account'
    document['providers'].append(duplicate); document['defaults']['chat'] = duplicate['id']
    service.update_document(document, 1)
    assert service.resolve_provider('chat').api_key == ''

def test_corrupt_configuration_preserved_until_explicit_import(isolated):
    service._path().parent.mkdir(parents=True, exist_ok=True)
    service._path().write_bytes(b'{broken')
    assert service.snapshot()['configuration_error']
    assert service.provider_preferences('video') == {}
    with pytest.raises(ValueError): service.resolve_provider('chat')
    with pytest.raises(ValueError): service.update_document(configured(isolated), 0)
    assert service._path().read_bytes() == b'{broken'
    service.import_document(configured(isolated), 0)
    assert service._path().with_suffix('.json.corrupt-backup').read_bytes() == b'{broken'
    assert service.snapshot()['configuration_error'] is None

def test_future_schema_is_never_downgraded(isolated):
    service._path().parent.mkdir(parents=True, exist_ok=True)
    service._path().write_text('{"schema_version":999}', encoding='utf-8')
    with pytest.raises(service.FutureConfiguration): service.import_document(configured(isolated), 0)
    assert json.loads(service._path().read_text())['schema_version'] == 999

def test_corrupt_vault_does_not_block_settings_or_overwrite(isolated):
    service._vault_path().parent.mkdir(parents=True, exist_ok=True)
    service._vault_path().write_bytes(b'corrupt-vault')
    assert service.snapshot()['credential_error']
    with pytest.raises(service.CredentialUnavailable): service.update_document(configured(isolated), 0, {'legacy-openlux': 'replacement'})
    assert service._vault_path().read_bytes() == b'corrupt-vault'

def test_request_construction_uses_saved_image_configuration(isolated, monkeypatch):
    document = configured(isolated); row = document['providers'][0]
    row['models']['image'] = 'my-image-model'; row['paths']['image'] = '/v1/custom-images'
    service.update_document(document, 0, {'legacy-openlux': 'fixture-only'})
    captured = {}
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    def fake_send(request, **kwargs):
        captured.update(url=request.full_url, body=json.loads(request.data), headers=dict(request.headers), options=kwargs)
        return Response(b'{"data": []}')
    monkeypatch.setattr(provider_transport, 'urlopen', fake_send)
    openlux.image_generate('a cube')
    assert captured['url'] == 'https://provider.example.com/v1/custom-images'
    assert captured['body']['model'] == 'my-image-model'
    assert captured['headers']['Authorization'] == 'Bearer fixture-only'

def test_transport_blocks_dns_rebinding_before_connect(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *args, **kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError, match='非公网'):
        provider_transport._PinnedHTTPSConnection('provider.example.com').connect()

def test_transport_rejects_redirect_without_forwarding_secret():
    request = urllib.request.Request('https://provider.example.com/v1/chat', headers={'Authorization': 'Bearer fixture'})
    with pytest.raises(ValueError, match='重定向'):
        provider_transport._NoRedirect().redirect_request(request, None, 307, 'redirect', {}, 'https://evil.example/steal')

@pytest.mark.parametrize('value', [True, float('nan'), float('inf'), -1, 3])
def test_custom_parameters_have_strict_types_and_ranges(isolated, value):
    document = configured(isolated); document['providers'][0]['parameters']['chat']['temperature'] = value
    with pytest.raises(ValueError): service.update_document(document, 0)

def test_unsupported_adapters_and_video_parameters_are_not_claimed(isolated):
    document = configured(isolated); document['providers'][0]['parameters']['video'] = {'magic_quality': True}
    with pytest.raises(ValueError, match='未经适配'): service.update_document(document, 0)
    document = configured(isolated); document['providers'][0]['adapter'] = 'made-up'
    with pytest.raises(ValueError, match='尚未实现'): service.update_document(document, 0)

def test_old_controls_remain_connected_after_provider_migration(isolated):
    from core import settings
    service.update_document(configured(isolated), 0)
    settings.update({'video_base_model': 'new-video-model', 'default_video_duration': 8})
    assert service.resolve_provider('video').model == 'new-video-model'
    assert settings.get_all()['video_base_model'] == 'new-video-model'
    assert service.snapshot()['runtime']['default_video_duration'] == 8

def test_legacy_preferences_are_preserved_on_first_provider_save(isolated):
    from core import settings
    settings.update({'video_base_model': 'old-saved-model', 'http_timeout': 75})
    document = service.snapshot()
    assert document['providers'][0]['models']['video'] == 'old-saved-model'
    assert document['providers'][0]['timeout'] == 75
    service.update_document(document, 0)
    assert service.resolve_provider('video').model == 'old-saved-model'

def test_startup_paths_are_nonsecret_and_do_not_require_settings_import(isolated):
    document = configured(isolated)
    document['runtime']['output_directory'] = 'C:\\Users\\User\\视频 输出'
    document['runtime']['ffmpeg_path'] = 'C:\\Tools\\ffmpeg.exe'
    service.update_document(document, 0)
    assert service.startup_options() == document['runtime']

@pytest.mark.parametrize('runtime', [{'http_timeout': True}, {'query_retries': 1.5}, {'default_generate_audio': 'yes'}, {'output_directory': '\\\\server\\share'}, {'ffmpeg_path': 'relative.exe'}])
def test_runtime_types_and_local_paths_are_validated(isolated, runtime):
    document = configured(isolated); document['runtime'] = runtime
    with pytest.raises(ValueError): service.update_document(document, 0)

def test_wellformed_but_invalid_stored_json_does_not_crash_snapshot(isolated):
    service._path().parent.mkdir(parents=True, exist_ok=True)
    service._path().write_text('{"schema_version":1,"revision":1,"providers":[]}', encoding='utf-8')
    assert service.snapshot()['configuration_error']
    with pytest.raises(ValueError): service.resolve_provider('video')

def test_official_pricing_cannot_be_retargeted_to_generic_video_account(isolated, monkeypatch):
    service.update_document(configured(isolated), 0, {'legacy-openlux': 'custom-endpoint-key'})
    monkeypatch.setattr(config, 'OPENLUX_API_KEY', 'official-fixture-key')
    selected = service.resolve_official_provider()
    assert selected.base_url == 'https://api.openlux.ai'
    assert selected.api_key == 'official-fixture-key'
    assert selected.api_key != service.resolve_provider('video').api_key

def test_official_profile_uses_its_encrypted_reference_and_no_key_repr(isolated):
    document = configured(isolated); document['providers'][0]['base_url'] = 'https://api.openlux.ai'
    service.update_document(document, 0, {'legacy-openlux': 'official-fixture-key'})
    selected = service.resolve_official_provider()
    assert selected.api_key == 'official-fixture-key'
    assert 'official-fixture-key' not in repr(selected)

def test_manually_added_plaintext_secrets_are_never_reflected_or_exported(isolated):
    service.update_document(configured(isolated), 0)
    document = json.loads(service._path().read_text(encoding='utf-8'))
    document['api_key'] = 'top-plaintext-secret-fixture'
    document['providers'][0]['api_key'] = 'provider-plaintext-secret-fixture'
    document['providers'][0]['token'] = 'provider-token-secret-fixture'
    document['providers'][0]['paths']['secret'] = 'path-plaintext-secret-fixture'
    document['providers'][0]['models']['secret'] = 'model-plaintext-secret-fixture'
    service._path().write_text(json.dumps(document), encoding='utf-8')
    assert service.snapshot()['configuration_error'] is None
    public = json.dumps(service.snapshot()) + json.dumps(service.export_document())
    for marker in ('top-plaintext', 'provider-plaintext', 'provider-token', 'path-plaintext', 'model-plaintext'):
        assert marker not in public
    assert 'provider-plaintext-secret-fixture' in service._path().read_text(encoding='utf-8')

def test_unknown_runtime_secret_is_rejected_without_echo(isolated):
    service.update_document(configured(isolated), 0)
    document = json.loads(service._path().read_text(encoding='utf-8'))
    document['runtime']['unknown_secret'] = 'runtime-plaintext-secret-fixture'
    service._path().write_text(json.dumps(document), encoding='utf-8')
    assert service.snapshot()['configuration_error']
    assert 'runtime-plaintext-secret-fixture' not in json.dumps(service.snapshot())
    with pytest.raises(ValueError) as error: service.export_document()
    assert 'runtime-plaintext-secret-fixture' not in str(error.value)

def test_poll_interval_is_typed_and_propagates_to_settings(isolated):
    from core import settings
    document = configured(isolated); document['runtime']['poll_interval'] = 3
    service.update_document(document, 0)
    assert settings.get('poll_interval') == 3
    document = service.snapshot(); document['runtime']['poll_interval'] = 0
    with pytest.raises(ValueError): service.update_document(document, 1)

@pytest.mark.parametrize('confirmed', [False, None, 'true'])
def test_damaged_vault_recovery_requires_explicit_confirmation(isolated, confirmed):
    service._vault_path().parent.mkdir(parents=True, exist_ok=True)
    service._vault_path().write_bytes(b'damaged-vault-evidence')
    with pytest.raises(ValueError, match='明确确认'):
        service.reauthorize_credentials('provider', {'legacy-openlux': 'new-fixture-key'}, confirmed, 0)
    assert service._vault_path().read_bytes() == b'damaged-vault-evidence'

def test_healthy_vault_is_not_cleared_by_recovery(isolated):
    service.update_document(configured(isolated), 0, {'legacy-openlux': 'healthy-fixture-key'})
    original = service._vault_path().read_bytes()
    with pytest.raises(ValueError, match='正常读取'):
        service.reauthorize_credentials('provider', {'legacy-openlux': 'new-fixture-key'}, True, 1)
    assert service._vault_path().read_bytes() == original
    assert service.resolve_provider('chat').api_key == 'healthy-fixture-key'

def test_damaged_provider_vault_recovery_retains_exact_backup_and_uses_new_dpapi_key(isolated):
    service._vault_path().parent.mkdir(parents=True, exist_ok=True)
    original = b'original-damaged-vault-evidence'; service._vault_path().write_bytes(original)
    result = service.reauthorize_credentials('provider', {'legacy-openlux': 'new-fixture-key'}, True, 0)
    backup = service._vault_path().parent / result['recovery']['backup_file']
    assert backup.read_bytes() == original
    assert __import__('hashlib').sha256(original).hexdigest() == result['recovery']['sha256']
    assert service.resolve_provider('chat').api_key == 'new-fixture-key'
    assert result['credential_error'] is None and result['revision'] == 1
    assert 'new-fixture-key' not in json.dumps(result)

def test_damaged_legacy_vault_recovery_retains_original_and_updates_only_new_values(isolated):
    target = profile_store.SECRETS_PATH; target.parent.mkdir(parents=True, exist_ok=True)
    original = b'legacy-damaged-vault-evidence'; target.write_bytes(original)
    result = service.reauthorize_credentials('legacy', {'openlux_api_key': 'new-legacy-fixture-key'}, True, 0)
    assert (target.parent / result['recovery']['backup_file']).read_bytes() == original
    assert profile_store.read_secrets() == {'openlux_api_key': 'new-legacy-fixture-key'}
    assert service.resolve_official_provider().api_key == 'new-legacy-fixture-key'

def test_damaged_vault_recovery_cannot_bypass_active_task_or_revision(isolated):
    service._vault_path().parent.mkdir(parents=True, exist_ok=True)
    service._vault_path().write_bytes(b'damaged-vault-evidence')
    with pytest.raises(service.ConfigurationConflict):
        service.reauthorize_credentials('provider', {'legacy-openlux': 'new-fixture-key'}, True, 9)
    db.execute('INSERT INTO tasks(id,kind,status) VALUES(?,?,?)', ('active-fixture', 'video', 'submission_unknown'))
    with pytest.raises(service.ConfigurationConflict):
        service.reauthorize_credentials('provider', {'legacy-openlux': 'new-fixture-key'}, True, 0)
    assert service._vault_path().read_bytes() == b'damaged-vault-evidence'

def test_video_submission_and_poll_use_selected_account_timeout(isolated, monkeypatch):
    document = configured(isolated); document['providers'][0]['timeout'] = 275
    service.update_document(document, 0, {'legacy-openlux': 'fixture-only'})
    captured = []
    class Response(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *args): self.close()
    def fake_send(request, **kwargs):
        captured.append((request.get_method(), kwargs['timeout']))
        return Response(b'{"id":"fixture-task","status":"running"}')
    monkeypatch.setattr(provider_transport, 'urlopen', fake_send)
    openlux.video_create('synthetic cube', [], '16:9', '720p', 4)
    openlux.video_query('fixture-task')
    assert captured == [('POST', 275), ('GET', 275)]
