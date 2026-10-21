import hashlib
import io
import json
import zipfile
import pytest
from core.component_packages import export_pack,import_pack,pack_identity
from core.component_manager import ComponentManager,location


def fixture(tmp_path):
    data=b'reviewed offline fixture'
    row={'path':'config.json','bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'url':'https://example.invalid/config.json'}
    item={'title':'fixture','version':'1','files':[row],'installed_files':{'config.json':row['sha256']}}
    cache=tmp_path/'cache';cache.mkdir();(cache/row['sha256']).write_bytes(data)
    return item,cache,data


def test_offline_export_import_roundtrip_does_not_need_network(tmp_path):
    item,cache,data=fixture(tmp_path);pack=export_pack(tmp_path/'component.zip','asr',item,cache)
    files=import_pack(pack,'asr',item,tmp_path/'new-cache')
    assert len(files)==1 and files[0].read_bytes()==data


def test_identical_artifact_bytes_can_bind_multiple_logical_files(tmp_path):
    item,cache,data=fixture(tmp_path)
    item['files'].append(dict(item['files'][0],path='second.json'))
    pack=export_pack(tmp_path/'deduplicated.zip','asr',item,cache)
    files=import_pack(pack,'asr',item,tmp_path/'new-cache')
    assert len(files)==2 and files[0]==files[1] and files[1].read_bytes()==data


@pytest.mark.parametrize('compression',[zipfile.ZIP_LZMA,zipfile.ZIP_BZIP2])
def test_unbounded_native_zip_decoders_are_rejected_before_reading(tmp_path,compression):
    item,_,data=fixture(tmp_path);pack=tmp_path/'unsupported.zip'
    with zipfile.ZipFile(pack,'w',compression=compression) as archive:
        archive.writestr('manifest.json',json.dumps({'format':'seedance.offline-component@1','id':'asr','content_identity':pack_identity(item)}))
        archive.writestr('artifacts/'+item['files'][0]['sha256'],data)
    with pytest.raises(PermissionError,match='压缩算法'):import_pack(pack,'asr',item,tmp_path/'new-cache')


@pytest.mark.parametrize('mutation',['hash','version','extra','traversal','duplicate'])
def test_untrusted_package_cannot_replace_catalog(tmp_path,mutation):
    item,cache,data=fixture(tmp_path)
    metadata={'format':'seedance.offline-component@1','id':'asr','content_identity':pack_identity(item)}
    if mutation=='version':metadata['content_identity']='a'*64
    path=tmp_path/'bad.zip'
    with zipfile.ZipFile(path,'x') as archive:
        archive.writestr('manifest.json',json.dumps(metadata))
        name='artifacts/'+item['files'][0]['sha256']
        archive.writestr(name,data[:-1]+b'!' if mutation=='hash' else data)
        if mutation in {'extra','traversal','duplicate'}:
            if mutation=='duplicate':
                with pytest.warns(UserWarning,match='Duplicate name'):archive.writestr(name,data)
            else:archive.writestr({'extra':'evil.exe','traversal':'../outside'}[mutation],data)
    with pytest.raises((PermissionError,ValueError)):
        import_pack(path,'asr',item,tmp_path/'new-cache')
    assert not (tmp_path/'outside').exists()


def test_actual_health_failure_does_not_activate_component(tmp_path):
    item,cache,data=fixture(tmp_path);root=tmp_path/'root';(root/'tools').mkdir(parents=True)
    (root/'tools/components-profile.json').write_text(json.dumps({'format':'seedance.components@1','components':{'asr':item}}),'utf8')
    pack=export_pack(tmp_path/'good.zip','asr',item,cache)
    manager=ComponentManager(root,tmp_path/'home',health_checker=lambda *_:{'available':False})
    manager.begin('asr',confirmed=True,offline_pack=pack);manager.thread.join(5)
    assert not manager.thread.is_alive()
    assert manager.status()['components'][0]['state']=='install_failed'
    assert not location(manager.home,'asr',item).exists()
    assert pack.is_file()  # Caller source outside managed imports is retained.


def test_upload_interruption_cleans_only_own_temporary_file(tmp_path):
    item,cache,data=fixture(tmp_path);root=tmp_path/'root';(root/'tools').mkdir(parents=True)
    (root/'tools/components-profile.json').write_text(json.dumps({'format':'seedance.components@1','components':{'asr':item}}),'utf8')
    manager=ComponentManager(root,tmp_path/'home')
    with pytest.raises(ValueError,match='中断'):
        manager.receive_import('asr',io.BytesIO(b'partial'),100,confirmed=True)
    assert manager.active is None and not manager.receiving
    assert not list((manager.base/'imports').glob('*.zip'))
