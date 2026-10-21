"""V4.2.7 no-network/no-paid-api regression for verified OCR installer + Studio integration."""
from __future__ import annotations
import contextlib
import io
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from core import tesseract_support as ts
import tesseract_installer as inst


def test_paths():
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp)
        exe=base/'Installed Programs'/'Tesseract-OCR'/'tesseract.exe'
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b'FAKE TEST EXECUTABLE')
        env={'ProgramFiles':str(base/'Installed Programs')}
        found=ts.find_tesseract(root=base/'studio',env=env,which='',registry_paths=[])
        assert found==str(exe),found
        exe.unlink()
        local=base/'LocalAppData'/'Programs'/'Tesseract-OCR'/'tesseract.exe'
        local.parent.mkdir(parents=True)
        local.write_bytes(b'TEST')
        found=ts.find_tesseract(root=base/'studio',env={'LOCALAPPDATA':str(base/'LocalAppData')},which='',registry_paths=[])
        assert found==str(local)
    print('PASS: looks in Program Files and current-user installation even without PATH')


def test_verify():
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/inst.FILENAME
        p.write_bytes(b'MZ'+b'0'*(inst.MIN_BYTES))
        assert not inst.verify_installer(p),'Hash should be rejected'
        p.write_bytes(b'<html>fake</html>')
        assert not inst.verify_installer(p)
    assert len(inst.SHA256)==64 and inst.SOURCE.startswith('https://github.com/tesseract-ocr/tesseract/releases/download/5.5.3/')
    print('PASS: rejects incorrect checksum, HTML and arbitrary executable payload')


def test_download_no_network():
    class Dummy:
        def __init__(self,host):self.host=host
        def __enter__(self):return self
        def __exit__(self,*args):return None
        def geturl(self):return self.host
        def read(self,n):return b'UNTRUSTED'
    with tempfile.TemporaryDirectory() as temp:
        p=Path(temp)/inst.FILENAME
        with patch.object(inst,'verify_installer',return_value=False):
            try:inst.download_installer(p,opener=lambda req,timeout:Dummy('https://malicious.example.invalid/file.exe'))
            except RuntimeError as exc:assert '未经允许' in str(exc)
            else:raise AssertionError('Invalid redirect was accepted')
            assert not p.exists() and not p.with_suffix('.download').exists()
    print('PASS: rejects unsafe redirects and cleans partial downloads')


def test_safe_install():
    with tempfile.TemporaryDirectory() as temp:
        location=Path(temp)/inst.FILENAME
        location.write_bytes(b'fake download used only with patched verifier')
        state=[{'available':False,'path':None}]
        calls=[]
        def runner(command,**kwargs):
            calls.append(command)
            state[0]={'available':True,'path':'C:/Program Files/Tesseract-OCR/tesseract.exe'}
            return SimpleNamespace(returncode=0)
        with patch.object(inst,'verify_installer',return_value=True),patch.object(inst,'time') as tm:
            ok=inst.install_tesseract(platform='nt',downloader=lambda:location,run_installer=runner,verifier=lambda:state[0])
        assert ok and len(calls)==1 and calls[0]==[str(location)]
        with patch.object(inst,'verify_installer',return_value=False):
            called=[]
            ok=inst.install_tesseract(platform='nt',downloader=lambda:location,run_installer=lambda *a,**k: called.append(1),verifier=lambda:{'available':False})
            assert ok is False and not called
        ok=inst.install_tesseract(platform='nt',consent=False,run_installer=lambda *a,**k:1,verifier=lambda:{'available':False})
        assert ok is False
        called=[]
        ok=inst.install_tesseract(platform='nt',run_installer=lambda *a,**k: called.append(1),verifier=lambda:{'available':True,'path':'C:/existing/tesseract.exe'})
        assert ok is True and not called
    print('PASS: consent, reuse existing install, execute only verified setup, post-install recheck')


def test_integration():
    from core import system_health, smart_analysis, hypit_engine, production_system
    from unittest.mock import patch
    assert smart_analysis.find_tesseract is ts.find_tesseract
    assert hypit_engine.find_tesseract is ts.find_tesseract
    assert production_system.find_tesseract is ts.find_tesseract
    assert system_health.check_tesseract is ts.check_tesseract
    source=(ROOT/'tools/install_analysis_stack.py').read_text(encoding='utf-8')
    other=(ROOT/'tools/install_production_tools.py').read_text(encoding='utf-8')
    assert 'UB-Mannheim.TesseractOCR' not in source
    assert 'UB-Mannheim.TesseractOCR' not in other
    assert 'from tesseract_installer import install_tesseract' in source
    assert 'from tesseract_installer import install_tesseract' in other
    assert 'install_complete_analysis()' in (ROOT/'tools/bootstrap_install.py').read_text(encoding='utf-8-sig')
    assert 'install_tesseract()' in (ROOT/'tools/install_analysis_stack.py').read_text(encoding='utf-8-sig')
    print('PASS: all analysis/health modules use shared executable discovery, no winget OCR route')


if __name__=='__main__':
    test_paths();test_verify();test_download_no_network();test_safe_install();test_integration();print('ALL OCR V4.2.7 SIMULATION TESTS PASSED')
