import configparser,json,re,threading
from pathlib import Path
import pytest
from desktop import launcher

ROOT=Path(__file__).resolve().parents[1]


def test_chinese_installer_complete_message_contract():
    contract=json.loads((ROOT/'desktop/locale/inno643-message-contract.json').read_text('utf8'))
    translation=configparser.ConfigParser(interpolation=None);translation.optionxform=str
    translation.read(ROOT/'desktop/locale/ChineseSimplified.isl',encoding='utf-8-sig')
    for section,entries in contract['sections'].items():
        assert set(translation[section])==set(entries)
        for key,tokens in entries.items():
            assert sorted(re.findall(r'%\d+',translation[section][key]))==tokens,key
    script=(ROOT/'desktop/installer.iss').read_text('utf8')
    assert 'compiler:Default.isl' not in script and 'locale\\ChineseSimplified.isl' in script
    assert 'UsePreviousLanguage=no' in script and 'ShowLanguageDialog=no' in script
    assert 'AppUserModelID: "SeedanceStudio.Desktop"' in script


def test_original_icon_sizes_and_frontend_identity():
    from PIL import Image
    with Image.open(ROOT/'assets/branding/seedance-studio.ico') as icon:
        assert {(n,n) for n in (16,24,32,48,64,96,128,256)}<=icon.ico.sizes()
    page=(ROOT/'static/index.html').read_text('utf8')
    assert 'data-application="seedance-studio"' in page
    assert '/static/seedance-icon.svg' in page
    assert "icon=SNAPSHOT+'/assets/branding/seedance-studio.ico'" in (ROOT/'tools/build_desktop_exe.py').read_text('utf8')


def test_background_validation_never_skips_or_duplicates_check(monkeypatch,tmp_path):
    entered=threading.Event();release=threading.Event();calls=[]
    def check(**kwargs):
        calls.append(kwargs);entered.set();assert release.wait(3);return tmp_path
    monkeypatch.setattr(launcher,'ensure_webview',check)
    task=launcher.WebViewPreparation(launcher.StartupTrace(tmp_path/'timing.json'))
    assert entered.wait(3)
    with pytest.raises(TimeoutError):task.result(.01)
    release.set()
    assert task.result(3)==tmp_path
    assert calls==[{'allow_evergreen':False}]


def test_background_integrity_failure_blocks_gui(monkeypatch,tmp_path):
    def reject(**_):raise RuntimeError('tampered runtime')
    monkeypatch.setattr(launcher,'ensure_webview',reject)
    task=launcher.WebViewPreparation(launcher.StartupTrace(tmp_path/'timing.json'))
    with pytest.raises(RuntimeError,match='tampered runtime'):task.result(3)
    assert json.loads((tmp_path/'timing.json').read_text('utf8'))['events'][0]['status']=='FAIL'


@pytest.mark.parametrize('field,value',[('origin','http://attacker.invalid'),('application','other-app'),('title','404'),('ready','loading')])
def test_false_gui_readiness_rejected(field,value):
    page={'origin':'http://127.0.0.1:12345','application':'seedance-studio','title':'Seedance Studio','ready':'complete'}
    launcher.validate_gui_page(page,page['origin'])
    page[field]=value
    with pytest.raises(RuntimeError,match='身份检查'):launcher.validate_gui_page(page,'http://127.0.0.1:12345')


def test_gui_deadline_does_not_fire_when_ready_or_closed():
    ready=threading.Event();closing=threading.Event();calls=[]
    ready.set()
    assert launcher.guard_gui_readiness(ready,closing,.01,lambda:calls.append('close'),lambda:calls.append('exit'))=='ready'
    ready.clear();closing.set()
    assert launcher.guard_gui_readiness(ready,closing,.01,lambda:calls.append('close'),lambda:calls.append('exit'))=='closed'
    assert not calls


def test_gui_deadline_graceful_close_then_failure_fallback():
    ready=threading.Event();closing=threading.Event();calls=[]
    def close():calls.append('close');closing.set()
    assert launcher.guard_gui_readiness(ready,closing,.01,close,lambda:calls.append('exit'),grace=.01)=='timeout'
    assert calls==['close']
    calls.clear();closing.clear()
    assert launcher.guard_gui_readiness(ready,closing,.01,lambda:calls.append('close'),lambda:calls.append('exit'),grace=.01)=='timeout'
    assert calls==['close','exit']


def test_gui_ready_at_deadline_is_not_killed():
    ready=threading.Event();closing=threading.Event();calls=[]
    def race():ready.set();return False
    assert launcher.guard_gui_readiness(ready,closing,.01,race,lambda:calls.append('exit'),grace=.01)=='ready'
    assert not calls


@pytest.mark.parametrize('value',['0','601'])
def test_invalid_gui_deadline_refused_before_profile_io(value):
    with pytest.raises(SystemExit):launcher.main(['--gui-load-timeout',value])


@pytest.mark.parametrize('bad_marker',[b'{broken',b'[]',b'\xff'])
def test_invalid_cached_runtime_marker_recovers_without_deleting_original(tmp_path,monkeypatch,bad_marker):
    import hashlib,zipfile
    resources=tmp_path/'resources';(resources/'tools').mkdir(parents=True)
    manifest={'format':'seedance.hypit-bundle@1'}
    for kind in ('sdk','browser'):
        archive=resources/'tools'/(kind+'-payload.zip')
        with zipfile.ZipFile(archive,'w') as file:file.writestr('component.txt','original')
        manifest[kind]={'file':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest()}
    (resources/'tools/hypit_bundle.json').write_text(json.dumps(manifest),encoding='utf8')
    monkeypatch.setattr(launcher,'resource_root',lambda:resources)
    monkeypatch.syspath_prepend(str(ROOT/'tools'))
    home=tmp_path/'profile';launcher.install_local_runtimes(home)
    sdk=home/'assets/hypit-host'/('sdk-'+manifest['sdk']['sha256'][:12])
    (sdk/'.seedance-runtime.json').write_bytes(bad_marker)
    result=launcher.install_local_runtimes(home)
    assert result['sdk']=='recovered'
    preserved=list(sdk.parent.glob(sdk.name+'.damaged-*'))
    assert len(preserved)==1
    assert (preserved[0]/'.seedance-runtime.json').read_bytes()==bad_marker
    assert (sdk/'component.txt').read_text()=='original'
