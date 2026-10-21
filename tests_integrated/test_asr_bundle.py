import sys
from types import SimpleNamespace
from core import config,hypit_engine as engine


def test_frozen_asr_requires_packaged_weights(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace())
    state=engine.faster_whisper_status()
    assert state['runtime_installed'] and not state['model_ready'] and not state['available']


def test_unsupported_cpu_skips_native_inference_import(monkeypatch):
    import builtins
    monkeypatch.setattr(engine,'_asr_cpu_supported',lambda:False)
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        if name=='faster_whisper':raise AssertionError('Unsupported CPU must not import native inference')
        return original(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',guarded)
    assert engine.faster_whisper_status()['available'] is False


def test_simultaneous_asr_does_not_load_second_model(tmp_path,monkeypatch):
    monkeypatch.setattr(engine.media,'probe',lambda _: {'ok':True,'has_audio':True})
    monkeypatch.setattr(engine,'_whisperx_exe',lambda:None)
    monkeypatch.setattr(engine,'faster_whisper_status',lambda:{'available':True})
    assert engine._asr_gate.acquire(blocking=False)
    try:
        result=engine.transcribe_if_available(tmp_path/'audio.wav',tmp_path/'cache')
        assert not result['used'] and '同时加载' in result['reason']
    finally:engine._asr_gate.release()


def test_asr_uses_managed_model_without_network_download(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'ROOT',tmp_path)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    model=tmp_path/'tools/runtime/models/faster-whisper-small';model.mkdir(parents=True)
    for name in ('model.bin','config.json','tokenizer.json'):(model/name).write_bytes(b'test-fixture')
    calls=[]
    class Model:
        def __init__(self,name,**kwargs): calls.append((name,kwargs))
        def transcribe(self,*args,**kwargs):
            word=SimpleNamespace(start=0,end=1,word='fixture')
            return iter([SimpleNamespace(start=0,end=1,text='fixture',words=[word])]),None
    monkeypatch.setitem(sys.modules,'faster_whisper',SimpleNamespace(WhisperModel=Model))
    monkeypatch.setattr(engine.media,'probe',lambda _: {'ok':True,'has_audio':True})
    monkeypatch.setattr(engine,'_whisperx_exe',lambda:None)
    result=engine.transcribe_if_available(tmp_path/'audio.wav',tmp_path/'cache')
    assert result['used'] and result['words'][0]['word']=='fixture'
    assert calls==[(str(model),{'device':'cpu','compute_type':'int8','local_files_only':True})]
