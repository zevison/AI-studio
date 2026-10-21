"""Desktop boundary regressions; no backend, account or production profile used."""
import json
import os
from pathlib import Path
import sys
import hashlib
import zipfile
from types import SimpleNamespace
import pytest
from desktop import launcher


@pytest.mark.parametrize('channel',['stdout','stderr'])
def test_frozen_redirected_streams_emit_utf8_chinese(monkeypatch,channel):
    import io
    raw=io.BytesIO();stream=io.TextIOWrapper(raw,encoding='gbk')
    with monkeypatch.context() as patch:
        patch.setattr(sys,'frozen',True,raising=False)
        patch.setattr(sys,channel,stream)
        # Keep the other test runner stream untouched.
        other='stderr' if channel=='stdout' else 'stdout'
        patch.setattr(sys,other,io.StringIO())
        launcher.restore_standard_streams()
        print('中文 路径：安装失败',file=getattr(sys,channel),flush=True)
        assert raw.getvalue().decode('utf8').splitlines()==['中文 路径：安装失败']
        assert stream.line_buffering
    stream.close()


def test_imported_launcher_preserves_development_stream_encoding(monkeypatch):
    import io
    stream=io.TextIOWrapper(io.BytesIO(),encoding='gbk')
    with monkeypatch.context() as patch:
        patch.setattr(sys,'frozen',False,raising=False)
        patch.setattr(sys,'stdout',stream)
        launcher.restore_standard_streams()
        assert stream.encoding=='gbk'
    stream.close()


def test_explicit_home_wins_and_inherited_secrets_are_not_imported(tmp_path,monkeypatch):
    for key in ('STUDIO_DATA_HOME','STUDIO_PROFILE_HOME','STUDIO_CONTROL_DB','HOST','PORT','OPEN_BROWSER','STUDIO_WORKSPACE_ID','STUDIO_WORKSPACE_TOKEN','STUDIO_DESKTOP'):
        monkeypatch.setenv(key,'untrusted-inherited-value')
    monkeypatch.setenv('OPENLUX_API_KEY','do-not-inherit-test-value')
    target=tmp_path/'中文 user profile'
    assert launcher.prepare_environment(target)==target.resolve()
    assert os.environ['STUDIO_PROFILE_HOME']==str(target.resolve())
    assert os.environ['PORT']=='0'
    assert os.environ['HOST']=='127.0.0.1'
    assert os.environ['STUDIO_WORKSPACE_ID']=='owner'
    assert 'OPENLUX_API_KEY' not in os.environ
    assert os.environ['STUDIO_CONTROL_DB']==str(target.resolve()/'data/team-control.sqlite')


def test_frozen_environment_cannot_select_another_python_or_node_runtime(tmp_path,monkeypatch):
    import os
    keys=('STUDIO_DATA_HOME','STUDIO_PROFILE_HOME','STUDIO_CONTROL_DB','HOST','PORT','OPEN_BROWSER',
          'STUDIO_WORKSPACE_ID','STUDIO_WORKSPACE_TOKEN','STUDIO_DESKTOP','PATH','PYTHONHOME','PYTHONPATH',
          'PYTHONNET_RUNTIME','NODE_PATH','NODE_OPTIONS')
    for key in keys:monkeypatch.setenv(key,'untrusted-inherited-runtime')
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path/'resources')
    launcher.prepare_environment(tmp_path/'private-home')
    assert 'untrusted-inherited-runtime' not in os.environ['PATH']
    for key in ('PYTHONHOME','PYTHONPATH','PYTHONNET_RUNTIME','NODE_PATH','NODE_OPTIONS'):assert key not in os.environ


def test_changed_bundled_native_tool_refuses_startup(tmp_path,monkeypatch):
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path)
    for folder,format_name in [('tools/node','seedance.node-managed@1'),('tools/ffmpeg','seedance.ffmpeg-managed@1')]:
        path=tmp_path/folder;path.mkdir(parents=True)
        (path/'tool.exe').write_bytes(b'original')
        (path/'runtime-manifest.json').write_text(json.dumps({'format':format_name,
            'files':{'tool.exe':hashlib.sha256(b'original').hexdigest()}}),encoding='utf8')
    launcher.verify_managed_analysis_components()
    (tmp_path/'tools/ffmpeg/tool.exe').write_bytes(b'modified')
    with pytest.raises(RuntimeError,match='incomplete or changed'):launcher.verify_managed_analysis_components()


def test_managed_model_corruption_refuses_startup(tmp_path,monkeypatch):
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path)
    model=tmp_path/'tools/runtime/models/faster-whisper-small';model.mkdir(parents=True)
    (model/'model.bin').write_bytes(b'approved')
    (model/'model-manifest.json').write_text(json.dumps({'format':'seedance.faster-whisper-model@1',
        'files':[{'path':'model.bin','sha256':hashlib.sha256(b'approved').hexdigest()}]}),encoding='utf8')
    launcher.verify_managed_analysis_components()
    (model/'model.bin').write_bytes(b'modified')
    with pytest.raises(RuntimeError,match='incomplete or changed'):launcher.verify_managed_analysis_components()


def test_frozen_resources_are_independent_of_working_directory(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path),raising=False)
    assert launcher.resource_root()==tmp_path


def test_atomic_ready_file_no_partial_or_orphan(tmp_path):
    target=tmp_path/'中文 文件.json'
    launcher.atomic_json(target,{'status':'ready','url':'http://127.0.0.1:12345'})
    launcher.atomic_json(target,{'status':'stopped'})
    assert json.loads(target.read_text('utf8'))=={'status':'stopped'}
    assert list(tmp_path.iterdir())==[target]


def test_existing_data_never_silently_overwritten(tmp_path):
    (tmp_path/'data').mkdir()
    (tmp_path/'data/keep.txt').write_text('preserve',encoding='utf8')
    with pytest.raises(FileExistsError):launcher.maybe_migrate(tmp_path,tmp_path/'other')
    assert (tmp_path/'data/keep.txt').read_text('utf8')=='preserve'


def test_no_implicit_migration_when_headless(tmp_path):
    assert launcher.maybe_migrate(tmp_path,prompt=False) is None
    assert not (tmp_path/'data').exists()


def test_worker_dispatch_rejects_arbitrary_modules():
    with pytest.raises(SystemExit) as error:
        launcher.main(['--worker-module','subprocess'])
    assert error.value.code==2


@pytest.mark.skipif(os.name!='nt',reason='Windows kernel mutex requires Windows')
def test_duplicate_mutex_rejected_and_released(tmp_path):
    with launcher.InstanceLock(tmp_path):
        with pytest.raises(RuntimeError,match='已在'):
            with launcher.InstanceLock(tmp_path):pass
    with launcher.InstanceLock(tmp_path):pass


def test_runtime_manifest_cannot_redirect_payload(tmp_path,monkeypatch):
    (tmp_path/'tools').mkdir()
    (tmp_path/'tools/hypit_bundle.json').write_text(json.dumps({'format':'seedance.hypit-bundle@1',
        'sdk':{'file':'../secrets.dpapi','sha256':'0'*64}}),encoding='utf8')
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    with pytest.raises(RuntimeError,match='Invalid managed runtime file'):
        launcher.install_local_runtimes(tmp_path/'profile')


@pytest.mark.parametrize('damage',['changed','missing'])
def test_corrupted_managed_cache_preserved_and_restored_once(tmp_path,monkeypatch,damage):
    resource=tmp_path/'resources';(resource/'tools').mkdir(parents=True)
    document={'format':'seedance.hypit-bundle@1'}
    for name in ('sdk','browser'):
        archive=resource/'tools'/(name+'-payload.zip')
        with zipfile.ZipFile(archive,'w') as package:package.writestr('keep.txt','original')
        document[name]={'file':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    (resource/'tools/hypit_bundle.json').write_text(json.dumps(document),encoding='utf8')
    monkeypatch.setattr(launcher,'resource_root',lambda:resource)
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]/'tools'))
    home=tmp_path/'profile';launcher.install_local_runtimes(home)
    folder=home/'assets/hypit-host'/('sdk-'+document['sdk']['sha256'][:12])
    if damage=='missing':(folder/'keep.txt').unlink()
    else:(folder/'keep.txt').write_text('damaged',encoding='utf8')
    launcher.install_local_runtimes(home)
    assert (folder/'keep.txt').read_text('utf8')=='original'
    quarantines=list(folder.parent.glob(folder.name+'.damaged-*'))
    assert len(quarantines)==1
    if damage=='missing':assert not (quarantines[0]/'keep.txt').exists()
    else:assert (quarantines[0]/'keep.txt').read_text('utf8')=='damaged'


def test_local_health_ignores_malicious_proxy_and_checks_identity(tmp_path,monkeypatch):
    for key in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY'):
        monkeypatch.setenv(key,'http://untrusted.invalid:8123')
    monkeypatch.setenv('NO_PROXY','')
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path/'program')
    report={'url':'http://127.0.0.1:23456','pid':123,'home':str(tmp_path),'version':'5.2.0','build':'candidate'}
    data={'pid':123,'port':23456,'version':'5.2.0','build':'candidate','workspace':'owner',
          'data_dir':str(tmp_path/'data'),'root_dir':str(tmp_path/'program')}
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def read(self,limit):return json.dumps({'ok':True,'data':data}).encode()
    class Opener:
        def open(self,url,timeout):
            assert url=='http://127.0.0.1:23456/api/bootstrap-health'
            assert timeout==5
            return Response()
    def build_opener(handler):
        assert handler.proxies=={}
        return Opener()
    monkeypatch.setattr(launcher.urllib.request,'build_opener',build_opener)
    assert launcher.validate_backend_health(report,tmp_path,123)==data
    data['pid']=456
    with pytest.raises(RuntimeError,match='different service'):
        launcher.validate_backend_health(report,tmp_path,123)


def test_local_health_rejects_remote_host_before_io(tmp_path,monkeypatch):
    monkeypatch.setattr(launcher.urllib.request,'build_opener',lambda *_:pytest.fail('Unexpected IO'))
    with pytest.raises(RuntimeError,match='identity mismatch'):
        launcher.validate_backend_health({'url':'https://attacker.invalid/','pid':12,'home':str(tmp_path)},tmp_path,12)


def test_fixed_webview_is_required_and_tamper_blocks_host_fallback(tmp_path,monkeypatch):
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path)
    monkeypatch.setattr(launcher,'webview_runtime_available',lambda:True)
    with pytest.raises(RuntimeError,match='锁定'):
        launcher.ensure_webview()
    fixed=tmp_path/'desktop/webview2';fixed.mkdir(parents=True)
    (fixed/'msedgewebview2.exe').write_bytes(b'public-vendor-test-fixture')
    checksum=hashlib.sha256((fixed/'msedgewebview2.exe').read_bytes()).hexdigest()
    (fixed/'runtime-manifest.json').write_text(json.dumps({'format':'seedance.webview2-fixed@1','files':{'msedgewebview2.exe':checksum}}),encoding='utf8')
    monkeypatch.setattr(sys,'getwindowsversion',lambda:SimpleNamespace(build=22631),raising=False)
    assert launcher.ensure_webview()==fixed
    assert os.environ['WEBVIEW2_BROWSER_EXECUTABLE_FOLDER']==str(fixed)
    (fixed/'msedgewebview2.exe').write_bytes(b'changed')
    with pytest.raises(RuntimeError,match='changed'):
        launcher.ensure_webview()


@pytest.mark.skipif(os.name!='nt',reason='Windows ACL arguments require Windows')
def test_win10_acl_only_grants_fixed_runtime_root(tmp_path,monkeypatch):
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path)
    fixed=tmp_path/'desktop/webview2';fixed.mkdir(parents=True)
    (fixed/'msedgewebview2.exe').write_bytes(b'fixture')
    (fixed/'runtime-manifest.json').write_text(json.dumps({'format':'seedance.webview2-fixed@1','files':{'msedgewebview2.exe':hashlib.sha256(b'fixture').hexdigest()}}),encoding='utf8')
    calls=[]
    monkeypatch.setattr(sys,'getwindowsversion',lambda:SimpleNamespace(build=19045),raising=False)
    monkeypatch.setattr(launcher.subprocess,'run',lambda args,**kwargs:calls.append((args,kwargs)))
    launcher.ensure_webview()
    assert [r[0] for r in calls]==[
        ['icacls.exe',str(fixed),'/grant','*S-1-15-2-2:(OI)(CI)(RX)'],
        ['icacls.exe',str(fixed),'/grant','*S-1-15-2-1:(OI)(CI)(RX)']]
    assert all(row[1]['timeout']==30 for row in calls)


def test_gui_debug_port_requires_explicit_isolation():
    with pytest.raises(SystemExit) as error:
        launcher.main(['--gui-cdp-port','9222'])
    assert error.value.code==2


def test_optional_sdk_never_extracts_on_base_startup(tmp_path,monkeypatch):
    tools=tmp_path/'tools';tools.mkdir()
    item={'file':'sdk-payload.zip','sha256':'a'*64}
    browser={'sha256':'b'*64,'deferred':True}
    (tools/'hypit_bundle.json').write_text(json.dumps({'format':'seedance.hypit-bundle@1','sdk_lazy':True,'sdk':item,'browser':browser}),'utf8')
    monkeypatch.setattr(launcher,'resource_root',lambda:tmp_path)
    monkeypatch.setattr(launcher,'verify_managed_analysis_components',lambda:None)
    import core.component_manager as components
    monkeypatch.setattr(components,'catalog',lambda _: {'browser':{'files':[{'sha256':'b'*64}]}})
    import install_hypit_bundle
    monkeypatch.setattr(install_hypit_bundle,'extract_verified',lambda *_:pytest.fail('SDK extraction blocked base startup'))
    result=launcher.install_local_runtimes(tmp_path/'home')
    assert result['sdk']=='bundled-lazy-before-use-verification'
    assert not (tmp_path/'home/assets').exists()
