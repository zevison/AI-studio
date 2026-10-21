"""Offline, unpaid regression for shared runtime installer. Safe on Linux and Windows."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import bootstrap_install as b
import shared_runtime as r


def test_reuse_previous():
    with tempfile.TemporaryDirectory() as d:
        base=Path(d)
        old=base/'SeedanceStudio_V3.9.8.3'; old.mkdir()
        current=base/'SeedanceStudio_V3.9.8.4'; current.mkdir()
        oldvenv=old/'.venv'; vpy=r.python_exe(oldvenv)
        vpy.parent.mkdir(parents=True)
        vpy.symlink_to(sys.executable)
        assert r.supported(oldvenv), 'test interpreter fixture invalid'
        saved=(r.ROOT, b.ROOT, b.VENV, b.VENV_PY, b.SHARED)
        try:
            r.ROOT=current
            b.ROOT=current
            b.VENV=current/'.venv'
            b.VENV_PY=r.python_exe(b.VENV)
            b.SHARED=base/'nonexisting_common_env'
            msg=b.prepare_venv()
            assert '复用旧版' in msg, msg
            assert b.VENV_PY.is_file(), 'missing junction alias'
            assert oldvenv.is_dir(), 'legacy environment should not be deleted'
            msg2=b.prepare_venv()
            assert '当前目录已有' in msg2, msg2
            print('[PASS] 同版本旧环境复用 + 重复安装保持不变')
        finally:
            r.ROOT, b.ROOT, b.VENV, b.VENV_PY, b.SHARED=saved


def test_cache_reuse():
    with tempfile.TemporaryDirectory() as d:
        base=Path(d)
        old=base/'SeedanceStudio_V3.9.8.3'
        new=base/'SeedanceStudio_V3.9.8.4'
        old.mkdir();new.mkdir()
        file=old/'tools/ffmpeg/bin/ffprobe.exe'
        file.parent.mkdir(parents=True)
        file.write_bytes(b'Z'*140_000)
        prior=r.ROOT; prior_asset=r.ASSETS
        try:
            r.ROOT=new
            r.ASSETS=base/'shared_assets'
            target=new/'tools/ffmpeg/bin/ffprobe.exe'
            assert r.copy_previous_asset('tools/ffmpeg/bin/ffprobe.exe',target,min_bytes=100_000)
            assert target.stat().st_size==140_000
            r.cache_asset('tools/ffmpeg/bin/ffprobe.exe',target,min_bytes=100_000)
            target.unlink(); file.unlink()
            assert r.copy_previous_asset('tools/ffmpeg/bin/ffprobe.exe',target,min_bytes=100_000)
            print('[PASS] FFprobe 可从旧版或公共缓存恢复，均无需下载')
        finally:
            r.ROOT=prior;r.ASSETS=prior_asset


def test_price_firewall_unaffected():
    from importlib.util import spec_from_file_location, module_from_spec
    # This release never invokes OpenLux or remote video tasks.
    text=(Path(__file__).parent/'bootstrap_install.py').read_text(encoding='utf-8')
    assert 'openlux' not in text.lower()
    assert 'pip' in text.lower()
    print('[PASS] 安装脚本不触发 OpenLux 或视频生成')


if __name__=='__main__':
    test_reuse_previous()
    test_cache_reuse()
    test_price_firewall_unaffected()
    print('ALL TESTS PASSED')

