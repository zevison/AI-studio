"""Reference upload/quote boundaries with fake OSS only; no paid/network calls."""
import hashlib
import json
from types import SimpleNamespace

import pytest
from core import hypit_reference_quote as quotes
from core.hypit_reference_upload import upload_reference,cleanup_owned_versions


class Request:
    def __init__(self, **kwargs): self.__dict__.update(kwargs)


SDK=SimpleNamespace(PutObjectRequest=Request, GetObjectRequest=Request)
CFG={'endpoint':'https://oss-cn-hangzhou.aliyuncs.com','bucket':'fixture','prefix':'tests'}


@pytest.mark.parametrize('version,changed,expected', [('v1',False,1),(None,False,0),('v1',True,0)])
def test_cleanup_never_deletes_unversioned_or_changed_objects(tmp_path,version,changed,expected):
    record={'format':'seedance.hypit-upload@1','phase':'ready',**{k:CFG[k] for k in ('endpoint','bucket')},
        'object_key':'tests/hypit-assets/'+'a'*64+'/'+'b'*32+'.mp4','sha256':'a'*64,
        'expected_etag':'c'*32,'version_id':version,'cleanup_after':1}
    journal=tmp_path/'owned.json';journal.write_text(json.dumps(record))
    class Client:
        deletes=0
        def head_object(self,req):
            assert req.version_id=='v1'
            return SimpleNamespace(etag='c'*32,metadata={'sha256':'wrong' if changed else 'a'*64})
        def delete_object(self,req):
            assert req.version_id=='v1' and req.key==record['object_key']
            self.deletes+=1
    client=Client();sdk=SimpleNamespace(HeadObjectRequest=Request,DeleteObjectRequest=Request)
    result=cleanup_owned_versions(tmp_path,client=client,sdk=sdk,cfg=CFG,now=2)
    assert client.deletes==result['deleted']==expected


def material(tmp_path):
    file=tmp_path/'reference.mp4';file.write_bytes(b'fixture media, probe mocked')
    return {'path':str(file),'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}


def test_upload_journals_before_io_and_binds_bytes(tmp_path):
    asset=material(tmp_path);journals=tmp_path/'journal'
    class Client:
        def put_object(self, req):
            records=list(journals.glob('*.json'))
            assert len(records)==1
            assert json.loads(records[0].read_text())['phase']=='upload_uncertain'
            assert req.forbid_overwrite is True
            assert req.metadata['sha256']==asset['sha256']
            return SimpleNamespace(etag=hashlib.md5(req.body.read()).hexdigest(),version_id='v1')
        def presign(self, req, **kwargs):
            assert req.version_id=='v1'
            return SimpleNamespace(url='https://fixture.oss-cn-hangzhou.aliyuncs.com/unique',signed_headers={})
    result=upload_reference(asset,client=Client(),sdk=SDK,cfg=CFG,journal_dir=journals)
    assert result['sha256']==asset['sha256']
    record=json.loads(next(journals.glob('*.json')).read_text())
    assert record['phase']=='ready' and record['version_id']=='v1'
    assert 'url' not in record


@pytest.mark.parametrize('failure',['timeout','etag','mutation'])
def test_uncertain_or_unverified_upload_never_returns_provider_url(tmp_path,failure):
    asset=material(tmp_path);journals=tmp_path/'journal'
    class Client:
        def put_object(self, req):
            if failure=='timeout':raise TimeoutError('ack lost')
            etag=hashlib.md5(req.body.read()).hexdigest()
            if failure=='mutation':
                from pathlib import Path
                Path(asset['path']).write_bytes(b'changed')
            return SimpleNamespace(etag='wrong' if failure=='etag' else etag)
        def presign(self,*args,**kwargs):raise AssertionError('Unverified object was signed')
    with pytest.raises((PermissionError,TimeoutError)):
        upload_reference(asset,client=Client(),sdk=SDK,cfg=CFG,journal_dir=journals)
    assert len(list(journals.glob('*.json')))==1
    assert json.loads(next(journals.glob('*.json')).read_text())['phase']!='ready'


@pytest.mark.parametrize('change', [{'video_codec':'av1'},{'fps':23},{'width':320,'height':240},
    {'has_audio':True,'audio_codec':'opus'}])
def test_unsupported_reference_rejected_before_upload(tmp_path,monkeypatch,change):
    asset=material(tmp_path)
    spec={'references':[dict(asset,role='reference_video',asset_id='a')],'prompt':'A cup',
        'task_type':'edit','duration':-1,'ratio':'adaptive','resolution':'720p','generate_audio':False}
    monkeypatch.setattr(quotes,'reference_source_spec',lambda *a,**k:spec)
    info={'ok':True,'duration':4,'width':720,'height':1280,'fps':24,'video_codec':'h264','has_audio':False}
    info.update(change)
    def forbidden(*a,**k):raise AssertionError('Invalid reference reached quote/upload')
    with pytest.raises(PermissionError):
        quotes.prepare_reference_quote(tmp_path,[],{},task_type='edit',limit_usd=3,
            upload=forbidden,normalize=lambda a:{'specs':a},probe=lambda p:info,quote_reader=forbidden)


def test_over_budget_quote_never_uploads(tmp_path,monkeypatch):
    asset=material(tmp_path)
    spec={'references':[dict(asset,role='reference_video',asset_id='a')],'prompt':'A cup',
        'task_type':'edit','duration':-1,'ratio':'adaptive','resolution':'720p','generate_audio':False}
    monkeypatch.setattr(quotes,'reference_source_spec',lambda *a,**k:spec)
    def forbidden(*a,**k):raise AssertionError('Over-budget media uploaded')
    with pytest.raises(PermissionError,match='budget'):
        quotes.prepare_reference_quote(tmp_path,[],{},task_type='edit',limit_usd=3,
            upload=forbidden,normalize=lambda a:{'specs':a},
            probe=lambda p:{'ok':True,'duration':4,'width':720,'height':1280,'fps':24,'video_codec':'h264'},
            quote_reader=lambda *a,**k:{'estimated_usd':'2.5','planning_reserve_usd':'3.125'})
