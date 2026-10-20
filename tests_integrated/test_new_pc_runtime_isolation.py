"""Offline regression of first-install isolation and preservation boundaries."""
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'tools'))
import bootstrap_install as boot
import install_analysis_stack as analysis


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    root = tmp_path/'中文 & 预算% 本版本'
    root.mkdir()
    monkeypatch.setattr(boot, 'ROOT', root)
    monkeypatch.setattr(boot, 'VENV', root/'.venv')
    return root


def test_new_environment_is_local_without_shell_or_old_release(monkeypatch, isolated):
    calls=[]
    monkeypatch.setattr(boot, 'supported', lambda p: len(calls)>0)
    monkeypatch.setattr(boot, 'run', lambda cmd: calls.append(cmd) or 0)
    markers=[]
    monkeypatch.setattr(boot, 'mark_family', markers.append)
    boot.prepare_venv()
    assert calls == [[sys.executable, '-m', 'venv', str(isolated/'.venv')]]
    assert markers == [isolated/'.venv']


def test_healthy_local_environment_never_reinstalled(monkeypatch, isolated):
    monkeypatch.setattr(boot, 'supported', lambda p: True)
    monkeypatch.setattr(boot, 'same_family', lambda p: True)
    monkeypatch.setattr(boot, 'run', lambda cmd: pytest.fail('unexpected reinstall'))
    boot.prepare_venv()


def test_old_environment_preserved_if_replacement_fails(monkeypatch, isolated):
    old=isolated/'.venv';old.mkdir();(old/'keep.txt').write_text('user marker')
    monkeypatch.setattr(boot, 'supported', lambda p: False)
    monkeypatch.setattr(boot, 'run', lambda cmd: 1)
    with pytest.raises(RuntimeError,match='独立 Python'):
        boot.prepare_venv()
    assert len(list(isolated.glob('.venv_saved_*/keep.txt'))) == 1
    assert next(isolated.glob('.venv_saved_*/keep.txt')).read_text()=='user marker'


def test_backup_names_never_collide_with_repeated_attempts(monkeypatch, isolated):
    for i in range(2):
        (isolated/'.venv').mkdir()
        boot._backup_broken_link()
    assert len(list(isolated.glob('.venv_saved_*'))) == 2


def test_running_workspace_blocks_before_venv_mutation(monkeypatch, isolated):
    monkeypatch.setattr(analysis,'studio_is_running',lambda:True)
    monkeypatch.setattr(boot,'prepare_venv',lambda:pytest.fail('modified live environment'))
    assert boot.main()==3


def test_direct_bootstrap_rejects_unqualified_python(monkeypatch, isolated):
    monkeypatch.setattr(boot.sys,'version_info',(3,13,0))
    monkeypatch.setattr(boot,'prepare_venv',lambda:pytest.fail('changed environment'))
    assert boot.main()==2


def test_healthy_but_linked_environment_is_detached(monkeypatch, isolated):
    old=isolated/'.venv';old.mkdir();(old/'keep.txt').write_text('old runtime')
    monkeypatch.setattr(Path,'is_junction',lambda p:p==old)
    monkeypatch.setattr(boot,'supported',lambda p:True)
    monkeypatch.setattr(boot,'same_family',lambda p:True)
    calls=[]
    monkeypatch.setattr(boot,'run',lambda cmd:calls.append(cmd) or 0)
    monkeypatch.setattr(boot,'mark_family',lambda p:None)
    boot.prepare_venv()
    assert calls and calls[0][-1]==str(old)
    assert next(isolated.glob('.venv_saved_*/keep.txt')).read_text()=='old runtime'


def test_hidden_launch_has_fallback_and_interpreter_check():
    cmd=(ROOT/'00_START.cmd').read_text('ascii')
    assert 'sys.version_info[:2]==(3,12)' in cmd
    assert 'if not errorlevel 1 exit /b 0' in cmd
    assert 'goto fallback' in cmd and 'launch_hidden.ps1' in cmd
    script=(ROOT/'tools/launch_hidden.ps1').read_text('utf8')
    assert '-WindowStyle Hidden' in script
    assert 'start_silent.py' in script


def test_install_child_with_no_output_is_time_bounded(isolated):
    import time
    before=time.monotonic()
    assert boot.run([sys.executable,'-c','import time;time.sleep(30)'],timeout=.3)==124
    assert time.monotonic()-before<10


def test_install_stream_preserves_output_and_exit_status(isolated,capsys):
    assert boot.run([sys.executable,'-c','print("real child output");raise SystemExit(7)'],timeout=5)==7
    assert 'real child output' in capsys.readouterr().out


def test_moved_environment_requires_recreation(tmp_path,monkeypatch):
    import shared_runtime
    old=tmp_path/'old';new=tmp_path/'new';environment=tmp_path/'environment';environment.mkdir()
    monkeypatch.setattr(shared_runtime,'ROOT',old);shared_runtime.mark_family(environment)
    assert shared_runtime.same_family(environment)
    monkeypatch.setattr(shared_runtime,'ROOT',new)
    assert not shared_runtime.same_family(environment)


def test_legacy_marker_without_owner_is_not_trusted(tmp_path):
    import shared_runtime,json
    (tmp_path/'seedance-runtime-family.json').write_text(json.dumps({'family':shared_runtime.RUNTIME_FAMILY}))
    assert not shared_runtime.same_family(tmp_path)


def test_installer_lock_covers_extraction_and_dependencies():
    script=(ROOT/'tools/install_new_pc.ps1').read_text(encoding='utf-8-sig')
    assert script.index("'setup.lock'") < script.index('install_hypit_bundle.py')
    assert script.index('check_install_idle.py') < script.index('install_hypit_bundle.py')
    assert '[System.IO.FileShare]::None' in script
    assert 'finally {\n    if($installLock)' in script
    assert '$readTask.Wait([int]$remaining)' in script
    assert '$inputStream.Read($buffer' not in script
