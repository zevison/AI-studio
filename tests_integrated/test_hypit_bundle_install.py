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
