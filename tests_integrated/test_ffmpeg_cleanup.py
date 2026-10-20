import importlib.util,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
spec=importlib.util.spec_from_file_location('ffmpeg_cleanup_test',ROOT/'tools/install_analysis_stack.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

def test_transient_file_lock_retries_without_invalidating_install(tmp_path,monkeypatch):
    monkeypatch.setattr(m.tempfile,'gettempdir',lambda:str(tmp_path))
    original=m.shutil.rmtree;calls=[]
    def locked(path):
        calls.append(path)
        if len(calls)<3:raise PermissionError('Windows scanner lock')
        original(path)
    monkeypatch.setattr(m.shutil,'rmtree',locked);monkeypatch.setattr(m.time,'sleep',lambda _:None)
    with m.ffmpeg_scratch() as folder:Path(folder,'probe.exe').write_bytes(b'fixture')
    assert len(calls)==3 and not Path(folder).exists()

def test_cleanup_error_does_not_replace_original_extraction_failure(tmp_path,monkeypatch):
    import pytest
    monkeypatch.setattr(m.tempfile,'gettempdir',lambda:str(tmp_path))
    def locked(path):raise PermissionError('test scanner lock')
    monkeypatch.setattr(m.shutil,'rmtree',locked);monkeypatch.setattr(m.time,'sleep',lambda _:None)
    with pytest.raises(ValueError,match='original checksum error'):
        with m.ffmpeg_scratch():raise ValueError('original checksum error')
