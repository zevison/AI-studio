"""Installer regression tests - safe: no network, no video-model requests."""
from __future__ import annotations
import io
import os
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from download_progress import download_file, DownloadTimeout

class FakeResponse(io.BytesIO):
    def __init__(self,data,declared=True):
        super().__init__(data)
        self.headers={'Content-Length':str(len(data))} if declared else {}


def test_ffmpeg_progress_uses_actual_megabytes_and_percent(tmp_path):
    expected=b'Z'*(6*1024*1024)
    out=[]
    size=download_file('https://example.com/archive.zip',tmp_path/'ffmpeg.zip',label='FFmpeg',
        open_url=lambda *a,**kw: FakeResponse(expected),
        printer=lambda *a,**kw:out.append(' '.join(map(str,a))))
    assert size==len(expected)
    assert (tmp_path/'ffmpeg.zip').read_bytes()==expected
    assert any('100%' in m and '6.0/6.0 MB' in m for m in out)
    assert any('Transfer complete' in m for m in out)


def test_unknown_total_never_invents_percentage(tmp_path):
    out=[]
    expected=b'Z'*(6*1024*1024)
    download_file('https://example.com/archive.zip',tmp_path/'unknown.zip',label='FFmpeg',
        open_url=lambda *a,**kw: FakeResponse(expected,False),
        printer=lambda *a,**kw:out.append(' '.join(map(str,a))))
    assert any('Total size unavailable' in m for m in out)
    assert any('Received ' in m and 'MB' in m for m in out)
    assert not any('% |' in m for m in out)


def test_partial_download_is_removed_not_cached(tmp_path):
    out=tmp_path/'partial.zip'
    class TruncatedResponse(FakeResponse):
        @property
        def headers(self):return {'Content-Length':str(2000000)}
        @headers.setter
        def headers(self,v):pass
    with pytest.raises(OSError,match='incomplete download'):
        download_file('https://example.com/archive.zip',out,label='FFmpeg',
            open_url=lambda *a,**kw: TruncatedResponse(b'Z'*500000),printer=lambda *a,**kw:None)
    assert not out.exists()


def test_ffmpeg_download_has_time_limit_and_timeout(tmp_path):
    out=tmp_path/'stuck.zip'
    t=[0.0]
    def advance():
        t[0]+=3.0
        return t[0]
    with pytest.raises(DownloadTimeout):
        download_file('https://example.com/archive.zip',out,label='FFmpeg',
            max_seconds=2,now=advance,
            open_url=lambda *a,**kw:FakeResponse(b'Z'*1000000),
            printer=lambda *a,**kw:None)
    assert not out.exists()


def test_pshell_native_utf8_fix_and_real_downloader_integration():
    ps=(ROOT/'tools/install_new_pc.ps1').read_text('utf-8-sig')
    assert '[Console]::OutputEncoding' in ps
    assert '$OutputEncoding = $utf8NoBom' in ps
    assert "$env:PYTHONIOENCODING = 'utf-8'" in ps
    assert "$env:PYTHONUNBUFFERED = '1'" in ps
    source=(ROOT/'tools/install_analysis_stack.py').read_text('utf8')
    assert 'download_file(url,archive,label=\'FFmpeg\'' in source
    assert 'FF_BACKUP_URL' in source
    assert 'try:' in source and 'except' in source


def test_one_install_one_launcher_and_all_features_remain():
    assert sorted(p.name for p in ROOT.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd']
    for component in ['core/voice_bindings.py','core/audio_output.py','core/openlux.py','core/bright_tiktok.py',
                      'core/oss.py','static/audio_reference.css','static/audio_reference.js','static/app.js',
                      'tools/bootstrap_install.py','tools/install_new_pc.ps1','tools/download_progress.py']:
        assert (ROOT/component).is_file(),component
    assert "APP_VERSION = '5.0.0'" in (ROOT/'core/config.py').read_text('utf-8')
