"""Regression checks for SceneDetect self-heal during Windows first install.

No network access; the actual Python install must still be verified on Windows.
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import bootstrap_install as boot


def test_healthy_scenedetect_is_not_reinstalled(monkeypatch):
    monkeypatch.setattr(boot,'probe_scenedetect',lambda: (True,''))
    monkeypatch.setattr(boot,'run',lambda cmd: (_ for _ in ()).throw(AssertionError('healthy module reinstalled')))
    assert boot.repair_scenedetect() is True


def test_missing_module_fixes_with_single_binary_install(monkeypatch):
    calls=[]
    answers=iter([(False,'No module named scenedetect'),(True,'')])
    monkeypatch.setattr(boot,'probe_scenedetect',lambda:next(answers))
    monkeypatch.setattr(boot,'run',lambda cmd: calls.append(cmd) or 0)
    assert boot.repair_scenedetect() is True
    assert len(calls)==1
    assert '--no-deps' in calls[0]
    assert '--force-reinstall' in calls[0]
    assert '--only-binary=:all:' in calls[0]
    assert 'scenedetect>=0.6.5,<0.7' in calls[0]


def test_retry_once_if_wheel_without_dependencies_is_not_enough(monkeypatch):
    calls=[]
    answers=iter([(False,'No module named scenedetect'),(False,'No module named platformdirs'),(True,'')])
    monkeypatch.setattr(boot,'probe_scenedetect',lambda:next(answers))
    monkeypatch.setattr(boot,'run',lambda cmd: calls.append(cmd) or 0)
    assert boot.repair_scenedetect() is True
    assert len(calls)==2
    assert '--no-deps' not in calls[1]
    assert calls[1][-1]=='scenedetect>=0.6.5,<0.7'


def test_both_repairs_fail_is_reported_as_failure(monkeypatch):
    calls=[]
    monkeypatch.setattr(boot,'probe_scenedetect',lambda:(False,'DLL load failed'))
    monkeypatch.setattr(boot,'run',lambda cmd:calls.append(cmd) or 1)
    assert boot.repair_scenedetect() is False
    assert len(calls)==2


def test_installer_keeps_two_cmd_entry_points_and_real_error_reporting():
    assert sorted(p.name for p in ROOT.glob('*.cmd')) == ['00_START.cmd','01_INSTALL.cmd']
    script=(ROOT/'tools/bootstrap_install.py').read_text('utf-8')
    assert 'required_import_errors' in script
    assert 'repair_scenedetect()' in script
    assert script.index('repair_scenedetect()\n        show_install_stage') < script.index("'Preparing final capability report and dependency validation'")
    assert 'from scenedetect import open_video, SceneManager' in script
    assert 'APP_VERSION = \'5.2.0\'' in script
    group_script=(ROOT/'tools/install_analysis_stack.py').read_text('utf-8')
    assert "'scenedetect>=0.6.5,<0.7'" in group_script
