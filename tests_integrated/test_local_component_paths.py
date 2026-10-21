import os
from pathlib import Path
import sys
from core import local_component_paths as paths,media,smart_analysis


def test_frozen_whisperx_never_uses_host_path_or_python_directory(tmp_path):
    host=tmp_path/'host';host.mkdir();(host/('whisperx.exe' if os.name=='nt' else 'whisperx')).write_bytes(b'host')
    def borrowed(_):raise AssertionError('Frozen app borrowed uncontrolled PATH')
    assert paths.whisperx_path(tmp_path/'app',frozen=True,executable=host/'python.exe',which=borrowed) is None


def test_managed_whisperx_resolver_is_shared_and_development_remains_supported(tmp_path):
    name='whisperx.exe' if os.name=='nt' else 'whisperx'
    file=tmp_path/'tools/runtime/whisperx_py313'/('Scripts' if os.name=='nt' else 'bin')/name
    file.parent.mkdir(parents=True);file.write_bytes(b'managed')
    assert paths.whisperx_path(tmp_path,frozen=True)==str(file)
    assert paths.whisperx_path(tmp_path,frozen=False,executable=tmp_path/'python.exe')==str(file)


def test_missing_frozen_ffmpeg_is_not_hidden_by_another_install(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(media,'_LOCAL_PATHS',{})
    monkeypatch.setattr(media,'BUNDLED_BIN',tmp_path/'missing')
    shared=tmp_path/'old-install';shared.mkdir()
    for name in ('ffmpeg','ffprobe'):(shared/(name+'.exe' if os.name=='nt' else name)).write_bytes(b'old')
    monkeypatch.setattr(media,'SHARED_BIN',shared)
    assert media.ffmpeg_path() is None and media.ffprobe_path() is None


def test_smart_analysis_respects_managed_ocr_before_system_path(monkeypatch):
    monkeypatch.setattr(smart_analysis,'find_tesseract',lambda:'managed-tesseract')
    def host(_):raise AssertionError('Unexpected host fallback')
    monkeypatch.setattr(smart_analysis.shutil,'which',host)
    assert smart_analysis._find_exe('tesseract')=='managed-tesseract'
