"""Layout and Windows one-installer/one-launcher release contract (offline)."""
from pathlib import Path
import re
ROOT=Path(__file__).resolve().parents[1]

def test_video_and_replica_options_have_readable_text_wrappers():
    html=(ROOT/'static/index.html').read_text(encoding='utf8')
    for scope in ('video','replica'):
        for mode in ('timbre','content'):
            pattern=rf'<label class="audio-use-option"><input type="radio" name="{scope}AudioUse" value="{mode}"[^>]*><span>.+?</span></label>'
            assert re.search(pattern,html),f'{scope}/{mode}: broken markup'
    assert html.count('class="audio-use-option"')==4

def test_audio_radio_overrides_global_full_width_inputs():
    css=(ROOT/'static/audio_reference.css').read_text(encoding='utf8')
    assert 'width:16px !important' in css
    assert 'padding:0 !important' in css
    assert 'grid-template-columns:18px minmax(0,1fr)' in css
    assert 'word-break:normal' in css
    assert '.audio-use-mode[hidden]{display:none!important}' in css

def test_windows_root_only_two_cmd_entrypoints_and_launch_is_hidden():
    assert sorted(p.name for p in ROOT.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd']
    assert not list(ROOT.glob('*.vbs'))
    boot=(ROOT/'00_START.cmd').read_bytes()
    assert b'\r\n' in boot and boot.count(b'\n')==boot.count(b'\r\n')
    assert b'tools\\launch_silent.vbs' in boot
    assert b'.venv\\Scripts\\python.exe' in boot
    assert b'wscript.exe' in boot
    assert (ROOT/'tools/launch_silent.vbs').exists()
    assert (ROOT/'tools/start_silent.py').exists()
    install=(ROOT/'01_INSTALL.cmd').read_bytes()
    assert install.count(b'\n')==install.count(b'\r\n')
    assert b'install_new_pc.ps1' in install

def test_consistent_release_identity():
    assert "APP_VERSION = '5.0.0'" in (ROOT/'core/config.py').read_text(encoding='utf8')
    assert "APP_VERSION = '5.0.0'" in (ROOT/'tools/bootstrap_install.py').read_text(encoding='utf8')
    assert 'audio_reference.css?v=5.0.0' in (ROOT/'static/index.html').read_text(encoding='utf8')
