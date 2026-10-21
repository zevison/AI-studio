import hashlib
import json
import shutil
import uuid
import os
import threading
import pytest
from core import component_manager as manager
from core.component_download import DownloadCancelled


def setup(tmp_path):
    root=tmp_path/'root';(root/'tools').mkdir(parents=True)
    data=b'reviewed fixture'
    row={'path':'config.json','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'url':'https://example.invalid/config.json'}
    item={'title':'fixture','version':'1','files':[row],'installed_files':{'config.json':row['sha256']}}
    (root/'tools/components-profile.json').write_text(json.dumps({'format':'seedance.components@1','components':{'asr':item}}),'utf8')
    return root,item,data


def test_crash_between_activation_renames_restores_original(tmp_path):
    root,item,data=setup(tmp_path);home=tmp_path/'home'
    destination=manager.location(home,'asr',item);destination.mkdir(parents=True)
    (destination/'config.json').write_bytes(b'original damaged bytes')
    backup=destination.with_name(destination.name+'.preserved-'+uuid.uuid4().hex)
    journal=destination.with_name(destination.name+'.activation.json')
    journal.write_text(json.dumps({'identity':manager.identity(item),'backup':backup.name,'had_destination':True}),'utf8')
    destination.rename(backup)  # Process dies before staging -> destination.
    manager.ComponentManager(root,home)
    assert (destination/'config.json').read_bytes()==b'original damaged bytes'
    assert not journal.exists()


def test_activation_validation_failure_rolls_back_bytes(tmp_path):
    from core.component_activation import activate
    _,item,data=setup(tmp_path)
    destination=tmp_path/'installed';destination.mkdir();(destination/'config.json').write_bytes(data)
    staging=tmp_path/'stage';staging.mkdir();(staging/'config.json').write_bytes(b'tampered')
    with pytest.raises(PermissionError):activate(staging,destination,item)
    assert (destination/'config.json').read_bytes()==data
    assert list(tmp_path.glob('installed.rejected-*'))


def test_interrupted_upload_never_becomes_network_download(tmp_path):
    root,item,_=setup(tmp_path);home=tmp_path/'home';base=home/'components';base.mkdir(parents=True)
    (base/'state.json').write_text(json.dumps({'asr':{'identity':manager.identity(item),'state':'receiving'}}),'utf8')
    service=manager.ComponentManager(root,home)
    assert service.states['asr']['state']=='failed' and not service.states['asr']['interrupted']


def test_paused_managed_offline_import_resumes_without_network(tmp_path,monkeypatch):
    from core.component_packages import export_pack
    root,item,data=setup(tmp_path);home=tmp_path/'home'
    cache=tmp_path/'cache';cache.mkdir();(cache/item['files'][0]['sha256']).write_bytes(data)
    folder=home/'components/imports';folder.mkdir(parents=True)
    package=export_pack(folder/(uuid.uuid4().hex+'.zip'),'asr',item,cache)
    def interrupted(*_,**__):raise DownloadCancelled()
    with monkeypatch.context() as patch:
        patch.setattr('core.component_packages.import_pack',interrupted)
        service=manager.ComponentManager(root,home)
        service.begin('asr',confirmed=True,offline_pack=package);service.thread.join(5)
    assert package.is_file() and service.states['asr']['state']=='paused'
    def forbidden(*_,**__):pytest.fail('Offline resume attempted network')
    resumed=manager.ComponentManager(root,home,downloader=forbidden,health_checker=lambda *_:{'available':True})
    resumed.begin('asr');resumed.thread.join(5)
    assert resumed.status()['components'][0]['state']=='ready'
    assert not package.exists()


def test_cache_cleanup_keeps_user_files_and_installed_bytes(tmp_path):
    root,item,data=setup(tmp_path);service=manager.ComponentManager(root,tmp_path/'home')
    cache=service.base/'cache';cache.mkdir(parents=True)
    (cache/item['files'][0]['sha256']).write_bytes(data)
    (cache/'user-file.txt').write_bytes(b'user')
    installed=manager.location(service.home,'asr',item);installed.mkdir(parents=True);(installed/'config.json').write_bytes(data)
    with pytest.raises(PermissionError):service.clear_cache()
    result=service.clear_cache(confirmed=True)
    assert result['removed_files']==1 and result['freed_bytes']==len(data)
    assert (cache/'user-file.txt').read_bytes()==b'user' and (installed/'config.json').read_bytes()==data


def test_disk_reserve_prevents_any_network_request(tmp_path,monkeypatch):
    from core import component_download as transfer
    _,item,_=setup(tmp_path)
    monkeypatch.setattr(shutil,'disk_usage',lambda _:shutil._ntuple_diskusage(100,99,1))
    def forbidden(*_,**__):pytest.fail('Network attempted with insufficient disk')
    with pytest.raises(OSError):transfer.fetch(item['files'][0],tmp_path/'cache',opener=forbidden)


def test_native_health_process_crash_is_controlled(tmp_path,monkeypatch):
    from core import component_health as health
    from types import SimpleNamespace
    root,item,_=setup(tmp_path)
    monkeypatch.setattr(health.subprocess,'run',lambda *_args,**_kwargs:SimpleNamespace(returncode=-1073741819))
    with pytest.raises(RuntimeError,match='主程序保持运行'):
        health.check_in_subprocess(root,tmp_path/'home','asr',tmp_path/'stage',item)
    assert True  # The test interpreter survives the native child failure path.


def test_incompatible_plugin_rejected_before_execution():
    from core.component_compatibility import validate
    item={'id':'browser','os':'windows','architecture':'x64','rollback':'versioned-retained',
          'app_minimum':'5.3.0','app_maximum_exclusive':'6.0.0','install_type':'browser-zip',
          'health_check':'chrome-version','dependencies':['core.node'],'installed_bytes':123}
    with pytest.raises(PermissionError,match='版本不兼容'):validate('browser',item,'5.2.0')
    item['app_minimum']='5.2.0';validate('browser',item,'5.2.0')
    item['architecture']='arm64'
    with pytest.raises(PermissionError):validate('browser',item,'5.2.0')


def test_uninstall_rejects_in_use_component_without_touching_bytes(tmp_path):
    from core.component_locks import file_lease,ComponentBusy
    root,item,data=setup(tmp_path);service=manager.ComponentManager(root,tmp_path/'home')
    installed=manager.location(service.home,'asr',item);installed.mkdir(parents=True);(installed/'config.json').write_bytes(data)
    with file_lease(service.base/'leases/asr.lock'):
        with pytest.raises(ComponentBusy):service.uninstall('asr',confirmed=True)
    assert (installed/'config.json').read_bytes()==data


@pytest.mark.skipif(os.name!='nt',reason='Actual Windows sharing semantics')
def test_windows_image_style_handle_is_released_before_activation(tmp_path):
    import ctypes
    from ctypes import wintypes
    from core.component_activation import activate
    _,item,data=setup(tmp_path)
    staging=tmp_path/'staging';staging.mkdir();(staging/'config.json').write_bytes(data)
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    kernel.CreateFileW.restype=wintypes.HANDLE
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    # A directory handle without FILE_SHARE_DELETE prevents renaming the stage.
    handle=kernel.CreateFileW(str(staging),0x80000000,3,None,3,0x02000000,None)
    assert handle!=wintypes.HANDLE(-1).value
    timer=threading.Timer(.4,lambda:kernel.CloseHandle(handle));timer.start()
    destination=tmp_path/'active'
    try:result=activate(staging,destination,item)
    finally:timer.join(3)
    assert result['rename_retries']>0 and (destination/'config.json').read_bytes()==data


def test_cleanup_file_lock_does_not_escape_worker_or_erase_failure(tmp_path,monkeypatch):
    root,item,data=setup(tmp_path)
    def downloader(row,cache,**_):
        cache.mkdir(parents=True,exist_ok=True);p=cache/row['sha256'];p.write_bytes(data);return p
    service=manager.ComponentManager(root,tmp_path/'home',downloader=downloader,health_checker=lambda *_:{'available':False})
    def occupied(*_,**__):raise PermissionError('controlled in-use stage')
    monkeypatch.setattr(manager.shutil,'rmtree',occupied)
    service.begin('asr',confirmed=True);service.thread.join(5)
    assert service.active is None and service.states['asr']['state']=='install_failed'
    assert service.states['asr']['cleanup_warning']


def test_optional_log_error_cannot_change_committed_install_state(tmp_path):
    root,item,_=setup(tmp_path);service=manager.ComponentManager(root,tmp_path/'home')
    (service.base/'components.log').mkdir(parents=True)
    service.states={'asr':{'identity':manager.identity(item),'state':'ready'}}
    service._save()
    assert json.loads((service.base/'state.json').read_text('utf8'))['asr']['state']=='ready'


def test_cleanup_only_removes_owned_interrupted_stage_names(tmp_path):
    root,item,data=setup(tmp_path);service=manager.ComponentManager(root,tmp_path/'home')
    staging=service.base/'asr'/('.install-'+uuid.uuid4().hex);staging.mkdir(parents=True);(staging/'partial').write_bytes(data)
    retained=service.base/'asr'/'user-folder';retained.mkdir();(retained/'keep').write_bytes(data)
    installed=manager.location(service.home,'asr',item);installed.mkdir();(installed/'config.json').write_bytes(data)
    result=service.clear_cache(confirmed=True)
    assert result['removed_interrupted_stages']==1 and not staging.exists()
    assert (retained/'keep').read_bytes()==data and installed.exists()


def test_missing_asr_model_explains_plugin_install_without_claiming_missing_python_package(monkeypatch):
    from core import hypit_engine
    monkeypatch.setattr(hypit_engine,'_asr_cpu_supported',lambda:True)
    def missing():raise FileNotFoundError('controlled missing model')
    monkeypatch.setattr(hypit_engine,'_managed_asr_model',missing)
    result=hypit_engine.faster_whisper_status()
    assert result['available'] is False and result['missing_components']==['asr']
    assert '首次使用' in result['reason'] and 'pip' not in result['reason']


def test_actual_audio_request_not_capability_poll_checks_authorized_on_demand_download(monkeypatch,tmp_path):
    from core import hypit_engine
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(hypit_engine.media,'probe',lambda _: {'ok':True,'has_audio':True})
    monkeypatch.setattr(hypit_engine,'_whisperx_exe',lambda:None)
    monkeypatch.setattr(hypit_engine,'faster_whisper_status',lambda: {'available':False,'missing_components':['asr'],'reason':'needs component'})
    monkeypatch.setattr(manager,'catalog',lambda *_,**__: {'asr':{}})
    monkeypatch.setattr(manager,'service',lambda:SimpleNamespace(need=lambda key:calls.append(key)))
    result=hypit_engine.transcribe_if_available(tmp_path/'audio-video.mp4',tmp_path/'cache')
    assert calls==['asr'] and result['used'] is False and result['missing_components']==['asr']
