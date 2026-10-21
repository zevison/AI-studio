"""Manual originals use embedded identities and never fall back to network."""
import hashlib
import io
import json
import pytest
from core.component_manager import ComponentManager,location
from core.component_packages import cache_artifact


def manager(tmp_path,*,health=True):
    bodies=[b'first reviewed file',b'second reviewed file']
    rows=[dict(path=f'file{i}.json',bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),
        url=f'https://example.invalid/file{i}.json') for i,data in enumerate(bodies)]
    item=dict(title='Manual test',version='1',files=rows,
              installed_files={r['path']:r['sha256'] for r in rows})
    root=tmp_path/'root';(root/'tools').mkdir(parents=True)
    (root/'tools/components-profile.json').write_text(json.dumps({'format':'seedance.components@1','components':{'asr':item}}),'utf8')
    def no_network(*args,**kwargs):raise AssertionError('Manual import must not use network')
    service=ComponentManager(root,tmp_path/'home',downloader=no_network,health_checker=lambda *_:{'available':health})
    return service,item,bodies


def test_manual_set_waits_then_installs_without_network(tmp_path):
    service,item,bodies=manager(tmp_path)
    service.receive_import('asr',io.BytesIO(bodies[0]),len(bodies[0]),confirmed=True,artifact=True)
    assert service.thread is None and service.active is None
    state=service.status()['components'][0]
    assert state['offline_required'] and sum(r['cached'] for r in state['manual_files'])==1
    assert state['manual_files'][0]['urls']==['https://example.invalid/file0.json']
    with pytest.raises(ValueError,match='不会自动转为联网'):
        service.begin('asr',confirmed=True)
    service.receive_import('asr',io.BytesIO(bodies[1]),len(bodies[1]),confirmed=True,artifact=True)
    service.thread.join(5)
    assert not service.thread.is_alive()
    assert service.status()['components'][0]['state']=='ready'
    folder=location(service.home,'asr',item)
    assert [(folder/r['path']).read_bytes() for r in item['files']]==bodies


@pytest.mark.parametrize('kind',['tampered','wrong_size','truncated','unconfirmed'])
def test_bad_original_preserves_user_data_and_never_installs(tmp_path,kind):
    service,item,bodies=manager(tmp_path)
    service.home.mkdir(parents=True,exist_ok=True)
    user=service.home/'user-data.txt';user.write_text('retain')
    data=bodies[0];length=len(data);confirmed=True
    if kind=='tampered':data=b'x'*length
    if kind=='wrong_size':length+=2
    if kind=='truncated':data=data[:-1]
    if kind=='unconfirmed':confirmed=False
    with pytest.raises((ValueError,PermissionError)):
        service.receive_import('asr',io.BytesIO(data),length,confirmed=confirmed,artifact=True)
    assert user.read_text()=='retain' and not location(service.home,'asr',item).exists()
    assert service.active is None and not service.receiving


def test_manual_health_failure_never_activates(tmp_path):
    service,item,bodies=manager(tmp_path,health=False)
    for data in bodies:service.receive_import('asr',io.BytesIO(data),len(data),confirmed=True,artifact=True)
    service.thread.join(5)
    assert service.status()['components'][0]['state']=='install_failed'
    assert not location(service.home,'asr',item).exists()


def test_untrusted_original_name_not_used_for_cache_path(tmp_path):
    service,item,bodies=manager(tmp_path)
    upload=tmp_path/'download.exe';upload.write_bytes(bodies[0])
    checksum=cache_artifact(upload,item,service.base/'cache')
    assert (service.base/'cache'/checksum).read_bytes()==bodies[0]
    assert not (service.base/'cache'/'download.exe').exists()
