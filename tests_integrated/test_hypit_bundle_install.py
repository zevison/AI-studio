import hashlib
import importlib.util
from pathlib import Path
import zipfile
import pytest

spec=importlib.util.spec_from_file_location('hypit_bundle_installer',Path(__file__).parents[1]/'tools/install_hypit_bundle.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def archive(tmp_path,name='runtime.txt'):
    file=tmp_path/'runtime.zip'
    with zipfile.ZipFile(file,'w') as z:z.writestr(name,b'Pinned runtime fixture')
    if '\\' in name:
        # zipfile normalizes Windows separators while creating archives. Patch
        # both filename headers to reproduce a genuinely hostile external ZIP.
        file.write_bytes(file.read_bytes().replace(name.replace('\\','/').encode(),name.encode()))
    return file,hashlib.sha256(file.read_bytes()).hexdigest()


def test_install_reuse_and_corruption_detection(tmp_path):
    file,sha=archive(tmp_path)
    target=tmp_path/'managed/runtime'
    assert module.extract_verified(file,target,sha)=='installed'
    assert module.extract_verified(file,target,sha)=='reused'
    (target/'runtime.txt').write_text('corrupted')
    with pytest.raises(RuntimeError):module.extract_verified(file,target,sha)
    assert (target/'runtime.txt').read_text()=='corrupted'


@pytest.mark.parametrize('name',['../escape','C:/escape','dir/../../escape','dir\\escape','CON.txt','dir/file. '])
def test_redteam_archive_escape_and_windows_ambiguous_names(tmp_path,name):
    file,sha=archive(tmp_path,name)
    target=tmp_path/'managed/runtime'
    with pytest.raises(RuntimeError):module.extract_verified(file,target,sha)
    assert not target.exists()
    assert list(target.parent.iterdir())==[]


def test_bad_checksum_does_not_create_install_directory(tmp_path):
    file,sha=archive(tmp_path)
    target=tmp_path/'managed/runtime'
    with pytest.raises(RuntimeError):module.extract_verified(file,target,'0'*64)
    assert not target.parent.exists()


def test_payload_excludes_generated_studio_cache_but_keeps_code_integrity(tmp_path):
    spec=importlib.util.spec_from_file_location('build_hypit_payload',Path(__file__).parents[1]/'tools/build_hypit_payload.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    root=tmp_path/'sdk';code=root/'node_modules/@hypit/studio/dist/start.js'
    code.parent.mkdir(parents=True);code.write_text('export const version = 1;')
    cache=root/'node_modules/@hypit/studio/node_modules/.vite/deps/recipe.js'
    cache.parent.mkdir(parents=True);cache.write_text('generated optimizer cache')
    (root/'.env').write_text('test-only credential placeholder')
    bundle=tmp_path/'payload.zip';identity=builder.build_payload(root,bundle)
    with zipfile.ZipFile(bundle) as zipped:
        assert zipped.namelist()==['node_modules/@hypit/studio/dist/start.js']
    target=tmp_path/'installed/sdk'
    assert module.extract_verified(bundle,target,identity['sha256'])=='installed'
    generated=target/'node_modules/@hypit/studio/node_modules/.vite/deps/recipe.js'
    generated.parent.mkdir(parents=True);generated.write_text('fresh optimizer output')
    assert module.extract_verified(bundle,target,identity['sha256'])=='reused'
    (target/'node_modules/@hypit/studio/dist/start.js').write_text('tampered SDK code')
    with pytest.raises(RuntimeError):module.extract_verified(bundle,target,identity['sha256'])


def test_identical_sdk_bytes_ignore_host_mtimes_and_file_modes(tmp_path):
    import os
    spec=importlib.util.spec_from_file_location('build_hypit_payload',Path(__file__).parents[1]/'tools/build_hypit_payload.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    root=tmp_path/'sdk';root.mkdir();code=root/'code.js';code.write_bytes(b'export const stable = true;')
    first=builder.build_payload(root,tmp_path/'first.zip')
    os.utime(code,(1800000000,1800000000));code.chmod(0o600)
    second=builder.build_payload(root,tmp_path/'second.zip')
    assert first['sha256']==second['sha256']
    assert (tmp_path/'first.zip').read_bytes()==(tmp_path/'second.zip').read_bytes()
    with zipfile.ZipFile(tmp_path/'second.zip') as archive:
        assert archive.getinfo('code.js').date_time==(1980,1,1,0,0,0)


def test_sdk_archive_cannot_recursively_package_its_own_output(tmp_path):
    spec=importlib.util.spec_from_file_location('build_hypit_payload',Path(__file__).parents[1]/'tools/build_hypit_payload.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    root=tmp_path/'sdk';root.mkdir();(root/'keep.js').write_bytes(b'original')
    with pytest.raises(ValueError,match='outside'):builder.build_payload(root,root/'payload.zip')
    if __import__('os').name=='nt':
        with pytest.raises(ValueError,match='outside'):builder.build_payload(module.native_path(root),root/'payload.zip')
    assert [p.name for p in root.iterdir()]==['keep.js']


def test_deep_unicode_paths_install_reuse_and_cleanup(tmp_path):
    import os
    target=tmp_path/('中文安装目录'+'a'*65)/'runtime'
    name=('nested'+'b'*65)+'/'+('leaf'+'c'*65)+'/runtime.dat'
    file,sha=archive(tmp_path,name)
    assert len(str(target/name))>260
    assert module.extract_verified(file,target,sha)=='installed'
    assert module.native_path(target/name).read_bytes()==b'Pinned runtime fixture'
    assert module.extract_verified(file,target,sha)=='reused'
    # Force failure after a deep file was created; cleanup must handle the same long paths.
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(bad,'w') as z:
        z.writestr(name,b'deep file')
        z.writestr('../escape',b'unsafe')
    failed=target.parent/'failed'
    with pytest.raises(RuntimeError,match='Unsafe'):
        module.extract_verified(bad,failed,hashlib.sha256(bad.read_bytes()).hexdigest())
    assert not module.native_path(failed).exists()
    assert not list(module.native_path(target.parent).glob('.install-*'))
    # Own deep fixtures must also be removed through the native path API.
    assert target.parent.resolve().is_relative_to(tmp_path.resolve())
    module.shutil.rmtree(module.native_path(target.parent))


def test_cleanup_error_preserves_primary_installation_failure(tmp_path,monkeypatch):
    file,sha=archive(tmp_path,'../escape')
    def fail_cleanup(*args,**kwargs):raise PermissionError('test cleanup denied')
    monkeypatch.setattr(module.shutil,'rmtree',fail_cleanup)
    with pytest.raises(RuntimeError,match='Unsafe') as caught:
        module.extract_verified(file,tmp_path/'managed/runtime',sha)
    assert any('cleanup' in note.lower() and 'denied' in note for note in caught.value.__notes__)
