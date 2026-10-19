import concurrent.futures
import json
import time
import urllib.error
import urllib.request
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.hypit_openlux_gateway import build_video_request, request_sha256
from core.hypit_runtime_backend import RuntimeBackend, SubmissionBudget
from core.hypit_video_quote import quote_video, MODEL_NAME


def payload():
    return build_video_request(prompt='Product demonstration',references=[],task_type='text',
        duration=4,ratio='16:9',resolution='720p',generate_audio=False)


def reader(path,*args):
    return {'data': {'/api/status':{'currency_type':'USD'},
        '/api/pricing':[{'model_name':MODEL_NAME,'model_price':.01,'enable_groups':['Seedance-1','Seedance-2']}],
        '/api/user/self/groups':{'ratios':{'Seedance-1':.65,'Seedance-2':.9}},
        '/v1/models':[{'id':MODEL_NAME}]}[path]}


def test_quote_matches_logged_in_console_table():
    quote=quote_video({'openlux_api_key':'test-key','openlux_account_access_token':'test-token'},payload(),
                      width=1280,height=720,reader=reader)
    assert Decimal(quote['usd_per_million_tokens']) == Decimal('9.630')
    assert quote['estimated_tokens'] == 86400
    assert Decimal(quote['estimated_usd']) == Decimal('.832032')
    assert quote['provider_enforced_charge_cap'] is False


@pytest.mark.parametrize('width,height,seconds',[(320,240,0),(1280,720,4)])
def test_unmeasured_dimensions_and_reference_price_mismatch_refused(width,height,seconds):
    with pytest.raises(PermissionError):
        quote_video({'openlux_api_key':'x','openlux_account_access_token':'y'},payload(),
                    width=width,height=height,input_video_seconds=seconds,reader=reader)


def test_crash_unknown_budget_persists_and_cannot_expand(tmp_path):
    budget=SubmissionBudget(tmp_path/'budget.db','grant','account',limit_usd='3.00')
    budget.reserve(payload(),'3.00')
    reopened=SubmissionBudget(tmp_path/'budget.db','grant','account',limit_usd='3.00')
    with pytest.raises(PermissionError):reopened.reserve(payload(),'3.00')
    with pytest.raises(PermissionError):SubmissionBudget(tmp_path/'budget.db','grant','account',limit_usd='6.00')


def test_collected_cache_requires_unchanged_bytes_and_task_identity(tmp_path):
    doc=payload();account='account';calls=[]
    def collect(payload,destination):
        calls.append('collect');destination.write_bytes(b'verified fixture bytes')
    life=SimpleNamespace(account=account,collect=collect)
    quote={'account':account,'currency':'USD','request_hash':request_sha256(doc),
        'estimated_usd':'.832032','checked_at':time.time()}
    backend=RuntimeBackend(life,doc,quote,SubmissionBudget(tmp_path/'b.db','g',account,limit_usd='3'),
        tmp_path/'media',reserved_usd='3',approved_by_user=True)
    backend.assert_task=lambda remote:None
    assert backend.collect('remote1')==b'verified fixture bytes'
    assert backend.collect('remote1')==b'verified fixture bytes' and len(calls)==1
    with pytest.raises(PermissionError):backend.collect('remote2')
    (tmp_path/'media'/(request_sha256(doc)[:24]+'.mp4')).write_bytes(b'changed')
    with pytest.raises(PermissionError):backend.collect('remote1')
    assert len(calls)==1


def test_collection_receipt_in_deep_windows_installation_path(tmp_path):
    fill=205-len(str(tmp_path))-1
    if fill<1:pytest.skip('Test root already exceeds target deep-path length')
    directory=tmp_path/('x'*fill);directory.mkdir()
    doc=payload();account='account'
    def collect(payload,dest):dest.write_bytes(b'verified deep-path fixture')
    life=SimpleNamespace(account=account,collect=collect)
    quote={'account':account,'currency':'USD','request_hash':request_sha256(doc),'estimated_usd':'.8','checked_at':time.time()}
    backend=RuntimeBackend(life,doc,quote,SubmissionBudget(tmp_path/'b.db','g',account,limit_usd='3'),
        directory,reserved_usd='3',approved_by_user=True)
    backend.assert_task=lambda remote:None
    assert backend.collect('task')==b'verified deep-path fixture'
    assert backend.collect('task')==b'verified deep-path fixture'


def test_concurrent_different_payloads_share_one_paid_allowance(tmp_path):
    budget=SubmissionBudget(tmp_path/'budget.db','grant','account',limit_usd='3.00')
    def reserve(n):
        item=payload();item['content'][0]['text']+=str(n)
        try:budget.reserve(item,'1');return True
        except PermissionError:return False
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve,range(8))) == 1


def test_runtime_capability_http_rejects_forgery_and_changed_payload(tmp_path):
    doc=payload();account='account'
    calls=[]
    life=SimpleNamespace(account=account,start=lambda p,s:calls.append(p) or {'task_id':'remote1'})
    quote={'account':account,'currency':'USD','request_hash':request_sha256(doc),
           'estimated_usd':'.832032','checked_at':time.time()}
    budget=SubmissionBudget(tmp_path/'budget.db','grant',account,limit_usd='3')
    backend=RuntimeBackend(life,doc,quote,budget,tmp_path/'output',reserved_usd='3',approved_by_user=True)
    changed=payload();changed['duration']=30
    with pytest.raises(PermissionError):backend.submit(changed)
    assert not calls
    backend.authorize(doc)
    assert not calls
    assert backend.submit(doc) == 'remote1'
    with pytest.raises(PermissionError):backend.submit(doc)
    assert len(calls)==1
