import json
from core import config
from core.hypit_installation import browser_configuration


def test_custom_chromium_has_no_conflicting_managed_selector(monkeypatch,tmp_path):
    custom=str(tmp_path/'浏览器 路径'/'chrome.exe')
    monkeypatch.setattr(config,'STARTUP_OPTIONS',{'chromium_path':custom})
    assert browser_configuration(tmp_path/'cache')=={'chromePath':custom}


def test_managed_browser_uses_current_pinned_lock_not_another_hardcoded_version(monkeypatch,tmp_path):
    (tmp_path/'tools').mkdir()
    (tmp_path/'tools/windows-runtime.lock.json').write_text(json.dumps({'browser':{'version':'155.0.8059.84','cache_root':'chrome-headless-shell/win64-155.0.8059.84'}}),encoding='utf8')
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(config,'STARTUP_OPTIONS',{})
    assert browser_configuration(tmp_path/'cache')['browserVersion']=='155.0.8059.84'
