import hashlib,importlib.util,json,sys,zipfile
from pathlib import Path
import pytest

TOOLS=Path(__file__).resolve().parents[1]/'tools'
sys.path.insert(0,str(TOOLS))
import build_windows_release as release
import repository_safety as safety

@pytest.mark.parametrize('name',['../escape','/absolute','C:/ads','x\\y','CON.txt','x/a','x/A'])
def test_archive_validation_precedes_writes(tmp_path,name):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:
        if name in {'x/a','x/A'}:z.writestr('x/a','first');z.writestr('x/A','second')
        else:
            info=zipfile.ZipInfo('placeholder');info.filename=name
            z.writestr(info,'unsafe')
    target=tmp_path/'target'
    with pytest.raises(ValueError):release.extract(archive,target)
    assert not target.exists()

def test_pinned_cache_rejects_corruption_without_replacing_file(tmp_path):
    cached=tmp_path/'runtime.zip';cached.write_bytes(b'original')
    item={'url':'https://example.invalid/runtime.zip','sha256':'0'*64}
    with pytest.raises(ValueError,match='preserved'):release.fetch(item,tmp_path)
    assert cached.read_bytes()==b'original'

def test_valid_pinned_cache_needs_no_network(tmp_path):
    cached=tmp_path/'runtime.zip';cached.write_bytes(b'fixture')
    assert release.fetch({'url':'https://example.invalid/runtime.zip','sha256':hashlib.sha256(b'fixture').hexdigest()},tmp_path)==cached


@pytest.mark.skipif(sys.platform!='win32',reason='Windows extended-path behavior')
def test_windows_archive_extraction_supports_long_paths(tmp_path):
    archive=tmp_path/'long.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('deep/file.txt','long-path fixture')
    target=tmp_path.joinpath(*['long-runtime-component-'+str(i)*20 for i in range(7)])
    assert len(str(target))>260
    release.extract(archive,target)
    assert (release.native_path(target)/'deep/file.txt').read_text()=='long-path fixture'

def test_safety_catches_realistic_token_inside_runtime_zip(tmp_path):
    archive=tmp_path/'runtime.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('fixture.txt','sk-'+'A1b2'*12)
    result=safety.scan_tree(tmp_path)
    assert result['findings']==[{'path':'runtime.zip!/fixture.txt','kind':'openai_key'}]
    assert 'A1b2' not in json.dumps(result)


def test_binary_algorithm_and_pem_header_are_not_secret_material(tmp_path):
    value=b'sk-'+b'ecdsa-sha2-nistp256-cert-v01'+b'\0-----BEGIN '+b'PRIVATE KEY-----\0'
    (tmp_path/'public-binary.bin').write_bytes(value)
    assert not safety.scan_tree(tmp_path)['findings']


def test_complete_pem_body_still_blocks_distribution(tmp_path):
    (tmp_path/'fixture.bin').write_bytes(b'-----BEGIN '+b'PRIVATE KEY-----\n'+b'A'*80+b'\n-----END '+b'PRIVATE KEY-----')
    assert safety.scan_tree(tmp_path)['findings']==[{'path':'fixture.bin','kind':'private_key'}]


def test_algorithm_shaped_token_with_suffix_is_still_blocked(tmp_path):
    (tmp_path/'fixture.bin').write_bytes(b'sk-'+b'ecdsa-sha2-nistp256-cert-v01' + b'TokenSuffix123')
    assert safety.scan_tree(tmp_path)['findings']==[{'path':'fixture.bin','kind':'openai_key'}]

@pytest.mark.parametrize('name',['.env','.env.local','data/studio.db','secrets.dpapi','profile.json','build/result.zip'])
def test_private_source_paths_are_denied(name):
    assert safety.unsafe_source_path(name)

@pytest.mark.parametrize('name',['.env.example','core/secrets_store.py','tools/hypit_provider/dist/src/activation.js','static/fonts/NotoSansSC.ttf','tools/acceptance/fixtures/projects/hp_free/assets/material-1.mp4'])
def test_required_sources_are_not_excluded(name):
    assert not safety.unsafe_source_path(name)

def test_runtime_lock_has_fixed_hashes_and_public_https():
    lock=json.loads((TOOLS/'windows-runtime.lock.json').read_text('utf8'))
    for name in ('node','browser','ffmpeg'):
        assert lock[name]['url'].startswith('https://')
        assert len(lock[name]['sha256'])==64

def test_free_smoke_never_defaults_to_developer_directory():
    text=(TOOLS/'acceptance/free_smoke.py').read_text('utf8')
    assert "add_argument('--root',required=True)" in text
    assert 'C:/Users/Administrator' not in text


def test_windows_format_checks_keep_owned_scripts_and_exclude_generated_env(tmp_path):
    import ast
    tree=ast.parse((Path(__file__).parent/'test_windows_release_42814.py').read_text('utf8'))
    function=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='owned')
    namespace={'ROOT':tmp_path}
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<owned-script-scope>','exec'),namespace)
    for name,expected in [('tools/install.ps1',True),('.venv/Scripts/Activate.ps1',False),('node_modules/pkg/test.ps1',False),('build/fixture.ps1',False)]:
        path=tmp_path/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text('fixture')
        assert namespace['owned'](path) is expected
