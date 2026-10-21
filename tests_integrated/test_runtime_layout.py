"""Packaged resources must never become the writable data directory."""
from pathlib import Path
import sys
import pytest
from core import runtime_layout


def test_frozen_resources_and_chinese_user_path_are_separate(monkeypatch, tmp_path):
    resources = tmp_path / 'Program Files' / 'Studio'
    home = tmp_path / '用户 数据' / 'Studio'
    monkeypatch.setattr(sys, '_MEIPASS', str(resources), raising=False)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setenv('STUDIO_DATA_HOME', str(home))
    assert runtime_layout.resource_root() == resources.resolve()
    assert runtime_layout.user_home() == home.resolve()
    assert not resources.exists()


def test_worker_is_explicit_allowlist(monkeypatch):
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    assert runtime_layout.module_command('yt_dlp', '--version') == [sys.executable, '--worker-module', 'yt_dlp', '--version']
    with pytest.raises(ValueError):
        runtime_layout.module_command('os', 'arbitrary-command')


def test_source_worker_uses_python_module(monkeypatch):
    monkeypatch.delattr(sys, 'frozen', raising=False)
    assert runtime_layout.module_command('yt_dlp', '--version') == [sys.executable, '-m', 'yt_dlp', '--version']


def test_early_boot_marker_uses_explicit_profile_without_app_import(monkeypatch,tmp_path):
    import ast,os,re,time
    application=Path(__file__).resolve().parents[1]/'app.py'
    node=next(n for n in ast.parse(application.read_text('utf8')) .body if isinstance(n,ast.FunctionDef) and n.name=='_boot_phase')
    monkeypatch.setenv('STUDIO_DATA_HOME',str(tmp_path/'用户 数据'))
    monkeypatch.setenv('STUDIO_WORKSPACE_ID','owner')
    scope={'Path':Path,'os':os,'re':re,'time':time,'__file__':str(tmp_path/'immutable/app.py')}
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(application),'exec'),scope)
    scope['_boot_phase']('test')
    assert (tmp_path/'用户 数据/data/startup_stage.txt').read_text('utf8').find('test')>=0
    assert not (tmp_path/'immutable').exists()
