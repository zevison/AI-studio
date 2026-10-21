from core import tesseract_support as ocr
import pytest


def test_frozen_ocr_never_borrows_host_binary(tmp_path, monkeypatch):
    host=tmp_path/'host.exe';host.write_bytes(b'host')
    monkeypatch.setattr(ocr.sys,'frozen',True,raising=False)
    assert ocr.find_tesseract(root=tmp_path/'app',which=str(host),registry_paths=[]) is None


def test_packaged_ocr_preferred_and_language_data_bound(tmp_path):
    app=tmp_path/'app';binary=app/'tools/tesseract/tesseract.exe'
    binary.parent.mkdir(parents=True);binary.write_bytes(b'package')
    host=tmp_path/'host.exe';host.write_bytes(b'host')
    assert ocr.find_tesseract(root=app,which=str(host),registry_paths=[])==str(binary)
    data=binary.parent/'tessdata';data.mkdir()
    for lang in ('eng','chi_sim'): (data/(lang+'.traineddata')).write_bytes(b'test')
    command=ocr.ocr_command(binary,'input.png')
    assert command[-4:]==['--tessdata-dir',str(data),'-l','eng+chi_sim']


def test_frozen_missing_languages_refuses_inherited_data(tmp_path, monkeypatch):
    monkeypatch.setattr(ocr.sys,'frozen',True,raising=False)
    with pytest.raises(FileNotFoundError,match='语言数据'):
        ocr.ocr_command(tmp_path/'tesseract.exe','input.png')
