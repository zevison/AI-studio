"""Offline faults are not evidence of real mirror connectivity (separate live audit)."""
import hashlib
import io
import json
from pathlib import Path
import threading
import urllib.error
import pytest
from core import component_download as transfer
from core import component_manager as manager


def row(data=b'fixture'):
    return dict(path='config.json', bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
        url='https://huggingface.co/Systran/faster-whisper-small/resolve/'+'a'*40+'/config.json')


class Response(io.BytesIO):
    def __init__(self, data, status=200, **headers):
        super().__init__(data); self.status=status; self.headers=headers


def test_mirror_fallback_resumes_only_exact_content_range(tmp_path):
    content=b'fixture'; item=row(content); calls=[]
    (tmp_path/(item['sha256']+'.part')).write_bytes(content[:2])
    def opener(request, initial):
        calls.append(request)
        assert request.headers['Range']=='bytes=2-'
        if len(calls)==1:raise urllib.error.URLError('offline')
        return Response(content[2:],206,**{'Content-Range':f'bytes 2-6/{len(content)}'})
    result=transfer.fetch(item,tmp_path,opener=opener,attempts_per_source=1)
    assert result.read_bytes()==content and len(calls)==2
    assert calls[0].host=='modelscope.cn' and calls[1].host=='huggingface.co'


def test_server_ignoring_range_restarts_partial(tmp_path):
    item=row();(tmp_path/(item['sha256']+'.part')).write_bytes(b'fi')
    assert transfer.fetch(item,tmp_path,opener=lambda *_:Response(b'fixture')).read_bytes()==b'fixture'


def test_hash_tampering_never_installs_and_retry_can_recover(tmp_path):
    item=row()
    with pytest.raises(PermissionError,match='SHA-256'):
        transfer.fetch(item,tmp_path,opener=lambda *_:Response(b'tamper!'))
    assert not (tmp_path/item['sha256']).exists()
    assert len(list(tmp_path.glob('*.part.rejected-*')))>=1
    assert transfer.fetch(item,tmp_path,opener=lambda *_:Response(b'fixture')).read_bytes()==b'fixture'


@pytest.mark.parametrize('content_range',['bytes 1-6/7','bytes 0-6/999','garbage'])
def test_false_range_rejected_before_partial_write(tmp_path,content_range):
    with pytest.raises(ValueError,match='断点'):
        transfer.fetch(row(),tmp_path,opener=lambda *_:Response(b'fixture',206,**{'Content-Range':content_range}))
    assert not list(tmp_path.glob('*.part'))


def test_cancel_preserves_partial_and_does_not_install(tmp_path):
    item=row();partial=tmp_path/(item['sha256']+'.part');partial.write_bytes(b'fi')
    with pytest.raises(transfer.DownloadCancelled):transfer.fetch(item,tmp_path,cancelled=lambda:True)
    assert partial.read_bytes()==b'fi' and not (tmp_path/item['sha256']).exists()


@pytest.mark.parametrize('url',['http://modelscope.cn/file','https://user:pass@modelscope.cn/file',
    'https://evil.invalid/file','https://127.0.0.1/file','https://modelscope.cn:8080/file'])
def test_untrusted_redirect_denied(url):
    with pytest.raises(PermissionError):transfer.validate_url(url,'https://modelscope.cn/file')


def test_dns_rebinding_to_lan_never_connects(monkeypatch):
    monkeypatch.setattr(transfer.socket,'getaddrinfo',lambda *_args,**_kwargs:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(PermissionError,match='公网'):transfer._PublicConnection('modelscope.cn').connect()


def fixture_catalog(root, data=b'fixture'):
    item=dict(title='ASR fixture',version='reviewed-v1',files=[row(data)],installed_files={'config.json':row(data)['sha256']})
    (root/'tools').mkdir(parents=True,exist_ok=True)
    (root/'tools/components-profile.json').write_text(json.dumps({'format':'seedance.components@1','components':{'asr':item}}),'utf8')
    return item


def test_atomic_install_duplicate_submission_and_corruption(tmp_path):
    root=tmp_path/'resources';home=tmp_path/'中文 空格';home.mkdir();item=fixture_catalog(root)
    entered=threading.Event();release=threading.Event()
    def download(row,cache,**kwargs):
        entered.set();assert release.wait(4)
        source=tmp_path/'download';source.write_bytes(b'fixture');return source
    service=manager.ComponentManager(root,home,downloader=download,health_checker=lambda *_:{'available':True})
    service.begin('asr',confirmed=True);assert entered.wait(2)
    original=service.thread
    assert service.begin('asr')['active']=='asr' and service.thread is original
    assert not manager.validate_installed(manager.location(home,'asr',item),item)
    release.set();original.join(5);assert not original.is_alive()
    assert service.status()['components'][0]['state']=='ready'
    path=manager.resolve('asr',root=root,home=home)
    (path/'config.json').write_bytes(b'tampered')
    with pytest.raises(FileNotFoundError):manager.resolve('asr',root=root,home=home)
    assert service.status()['components'][0]['state']=='needs_repair'


def test_interrupted_state_is_paused_not_ready_or_autoretried(tmp_path):
    item=fixture_catalog(tmp_path/'root');home=tmp_path/'home';(home/'components').mkdir(parents=True)
    record={'asr':dict(identity=manager.identity(item),state='downloading',downloaded=2)}
    (home/'components/state.json').write_text(json.dumps(record),'utf8')
    service=manager.ComponentManager(tmp_path/'root',home)
    assert service.status()['components'][0]['state']=='paused'
    stop=threading.Event();service.start_initial(stop);stop.set()
    assert service.thread is None


def test_upgrade_uses_new_folder_retains_previous_bytes(tmp_path):
    root=tmp_path/'root';home=tmp_path/'home';old=fixture_catalog(root);oldpath=manager.location(home,'asr',old)
    oldpath.mkdir(parents=True);(oldpath/'config.json').write_bytes(b'fixture')
    new=fixture_catalog(root,b'new fixture')
    assert manager.location(home,'asr',new)!=oldpath
    with pytest.raises(FileNotFoundError):manager.resolve('asr',root=root,home=home)
    assert (oldpath/'config.json').read_bytes()==b'fixture'
    fixture_catalog(root)
    assert manager.resolve('asr',root=root,home=home)==oldpath


def test_deep_windows_component_path_is_really_usable(tmp_path):
    root=tmp_path/'root';item=fixture_catalog(root)
    home=tmp_path/('deep-'+'a'*70)/('space '+'b'*70)/('user-'+'c'*70)
    destination=manager.location(home,'asr',item)
    destination.mkdir(parents=True)
    (destination/'config.json').write_bytes(b'fixture')
    assert manager.resolve('asr',root=root,home=home)==destination


def test_browser_runtime_contract_keeps_local_drive_path(monkeypatch,tmp_path):
    from core.hypit_installation import browser_configuration
    from core import config
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(config,'STARTUP_OPTIONS',{})
    value=browser_configuration('\\\\?\\C:\\User Data\\components\\browser')
    assert value['browserCacheDirectory']=='C:\\User Data\\components\\browser'


def test_unicode_native_model_uses_bounded_file_handles(tmp_path,monkeypatch):
    import os
    if os.name!='nt':pytest.skip('Windows native path compatibility')
    from core.asr_model import load_local_model
    folder=manager.plain(tmp_path/'中文 模型');folder.mkdir()
    for name in ('config.json','model.bin','vocabulary.txt','tokenizer.json'):(folder/name).write_bytes(b'fixture')
    captured=[]
    def factory(name,**options):
        assert name=='seedance-reviewed-local-small' and options['local_files_only']
        assert options['files']['tokenizer.json']==b'fixture'
        captured.extend(v for v in options['files'].values() if hasattr(v,'read'))
        assert all(stream.read()==b'fixture' for stream in captured)
        return 'loaded'
    assert load_local_model(factory,folder)=='loaded'
    assert captured and all(stream.closed for stream in captured)


def test_fresh_launch_never_downloads_without_consent(tmp_path):
    root=tmp_path/'root';fixture_catalog(root)
    calls=[]
    service=manager.ComponentManager(root,tmp_path/'home',downloader=lambda *args,**kwargs:calls.append(args))
    stop=threading.Event();service.start_initial(stop)
    import time
    time.sleep(.03)
    assert not calls and service.thread is None
    with pytest.raises(PermissionError,match='确认'):service.begin('asr')
    service.configure({'automatic_on_demand':True})
    service.need('asr')
    assert not calls and service.thread is None


def test_corrupt_preferences_cannot_grant_automatic_download(tmp_path):
    root=tmp_path/'root';fixture_catalog(root);home=tmp_path/'home';(home/'components').mkdir(parents=True)
    file=home/'components/preferences.json'
    file.write_text(json.dumps({'approved':{},'automatic_on_demand':'true'}),'utf8')
    original=file.read_bytes()
    with pytest.raises(ValueError,match='类型'):manager.ComponentManager(root,home)
    assert file.read_bytes()==original


def test_browser_readers_can_share_but_prevent_replacement(tmp_path):
    from core.component_locks import file_lease
    path=tmp_path/'browser.lock'
    with file_lease(path,shared=True),file_lease(path,shared=True):
        with pytest.raises(RuntimeError):
            with file_lease(path):pytest.fail('In-use browser was replaceable')
    with file_lease(path):
        with pytest.raises(RuntimeError):
            with file_lease(path,shared=True):pytest.fail('Reader entered activation')


def test_changed_etag_never_appends_to_old_entity(tmp_path):
    item=row();(tmp_path/(item['sha256']+'.part')).write_bytes(b'fi')
    source=transfer.sources_for(item)[0]
    (tmp_path/(item['sha256']+'.part.json')).write_text(json.dumps({'sha256':item['sha256'],'bytes':item['bytes'],'source':source,'etag':'"v1"'}),'utf8')
    calls=[]
    def opener(request,_initial):
        calls.append(request)
        if len(calls)==1:return Response(b'xture',206,**{'Content-Range':'bytes 2-6/7','ETag':'"v2"'})
        assert 'Range' not in request.headers
        return Response(b'fixture',ETag='"v2"')
    path=transfer.fetch(item,tmp_path,opener=opener)
    assert path.read_bytes()==b'fixture' and len(calls)==2
    assert calls[0].headers['If-range']=='"v1"'
    assert list(tmp_path.glob('*.part.rejected-*'))


def test_official_first_is_a_real_transport_preference(tmp_path):
    calls=[]
    def opener(request,_):calls.append(request.host);return Response(b'fixture')
    transfer.fetch(row(),tmp_path,source_order='official_first',opener=opener)
    assert calls==['huggingface.co']


def test_cross_process_lease_rejects_concurrent_writer(tmp_path):
    with manager.process_lease(tmp_path/'lease'):
        with pytest.raises(RuntimeError):
            with manager.process_lease(tmp_path/'lease'):pytest.fail('duplicate writer')
    with manager.process_lease(tmp_path/'lease'):pass


def test_catalog_traversal_is_rejected(tmp_path):
    fixture_catalog(tmp_path)
    doc=json.loads((tmp_path/'tools/components-profile.json').read_text('utf8'))
    doc['components']['asr']['installed_files']={'../outside':'a'*64}
    (tmp_path/'tools/components-profile.json').write_text(json.dumps(doc),'utf8')
    with pytest.raises(ValueError):manager.catalog(tmp_path)


def test_missing_browser_prevents_new_paid_work_before_any_provider_call(tmp_path,monkeypatch):
    from core import config
    from core.hypit_studio import HypitStudio
    item=fixture_catalog(tmp_path)
    profile=tmp_path/'tools/components-profile.json'
    doc=json.loads(profile.read_text('utf8'));doc['components']={'browser':item}
    profile.write_text(json.dumps(doc),'utf8')
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(config,'USER_HOME',tmp_path/'home')
    studio=object.__new__(HypitStudio)
    with pytest.raises(FileNotFoundError,match='组件'):
        studio.build_paid('irrelevant',{'approve_video_charges':True,'limit_usd':'1'})


def test_linked_cache_never_written(tmp_path):
    item=row(); target=tmp_path/item['sha256']
    outside=tmp_path/'outside';outside.write_bytes(b'original')
    try:target.symlink_to(outside)
    except OSError:pytest.skip('Creating a Windows symlink requires developer mode')
    with pytest.raises(PermissionError):transfer.fetch(item,tmp_path)
    assert outside.read_bytes()==b'original'
