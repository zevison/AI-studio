"""V5.0.0 installer behavior tests; no live network, Windows or paid APIs."""
from __future__ import annotations
import sys, io, os, hashlib, subprocess, zipfile
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from download_progress import DownloadTooSlow, download_file
import install_analysis_stack as mod

class Fake(io.BytesIO):
    def __init__(self,data):
        super().__init__(data);self.headers={'Content-Length':str(len(data))}

def test_smaller_verified_7z_precedes_zip():
    assert len(mod.FF_SOURCES)==3
    assert [v[2] for v in mod.FF_SOURCES]==['7z','7z','zip']
    assert mod.FF_URL.startswith('https://www.gyan.dev/')
    assert 'github.com/GyanD/' in mod.FF_BACKUP_URL
    assert len(mod.FF_SEVEN_SHA256)==64
    assert mod.FF_SOURCES[0][3]==mod.FF_SOURCES[1][3]
    assert 'tar' in Path(mod.__file__).read_text('utf-8')

def test_slow_primary_aborts_and_cleans_partial(tmp_path):
    seconds=[0]
    def fake_clock():seconds[0]+=10;return seconds[0]
    payload=b'Z'*400_000
    target=tmp_path/'slow.7z'
    with pytest.raises(DownloadTooSlow,match='switching to alternate'):
        download_file('https://example.com/ffmpeg.7z',target,label='FFmpeg',
            open_url=lambda *a,**k:Fake(payload), now=fake_clock,
            min_speed_mbps=0.08,slow_grace_seconds=15, printer=lambda *a,**kw:None)
    assert not target.exists()

def test_verified_sha256_rejects_corrupt_archive(tmp_path):
    p=tmp_path/'download.7z';p.write_bytes(b'invalid download')
    with pytest.raises(RuntimeError,match='SHA-256 mismatch'):
        mod._validate_sha256(p,mod.FF_SEVEN_SHA256)
    mod._validate_sha256(p,hashlib.sha256(p.read_bytes()).hexdigest())

def test_zip_fallback_extracts_only_two_windows_tools(tmp_path):
    z=tmp_path/'ffmpeg.zip'
    with zipfile.ZipFile(z,'w') as archive:
        archive.writestr('ffmpeg-test/bin/ffmpeg.exe',b'MZ'+b'F'*200_000)
        archive.writestr('ffmpeg-test/bin/ffprobe.exe',b'MZ'+b'P'*200_000)
        archive.writestr('ffmpeg-test/bin/suspicious.exe',b'MZ'+b'X'*200_000)
    selected=mod._extract_windows_ffmpeg(z,tmp_path/'scratch',archive_type='zip')
    assert set(selected)=={'ffmpeg.exe','ffprobe.exe'}
    assert not list((tmp_path/'scratch').rglob('suspicious.exe'))

@pytest.mark.parametrize('filename',('ffmpeg.exe','ffprobe.exe'))
def test_invalid_windows_binary_rejected(tmp_path,filename):
    p=tmp_path/filename;p.write_bytes(b'ZZ'+b'F'*200_000)
    with pytest.raises(RuntimeError,match='Invalid Windows executable'):
        mod._validate_ffmpeg_binary(p)

def test_preserve_single_cmd_and_existing_audio_features():
    assert sorted(x.name for x in ROOT.glob('*.cmd'))==['00_START.cmd','01_INSTALL.cmd']
    assert (ROOT/'core/voice_bindings.py').exists()
    assert (ROOT/'static/audio_reference.js').exists()
    assert "APP_VERSION = '5.0.0'" in (ROOT/'core/config.py').read_text('utf-8')
