"""Offline bridge tests. All network calls stubbed. NO paid operations."""
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.hypit_openlux_gateway import (
    ContractError, DurableVideoGateway, request_sha256,
)
from core.hypit_provider_bridge import (
    ContractNotVerified, MediaFailure, OpenLuxHttpTransport, ProviderLifecycle,
    SafeMp4Collector, SpendEnvelope, VerifiedRoute, _bound_json,
)
from tests.test_hypit_openlux_gateway import FakeTransport, make_edit


class Response(io.BytesIO):
    def __init__(self, data, *, status=200, headers=None):
        super().__init__(data)
        self.status=status
        self.headers=headers or {}
    def __enter__(self):return self
    def __exit__(self,*args):self.close()


class Opener:
    def __init__(self,response=b'{}',*,status=200,headers=None):
        self.response=response
        self.status=status
        self.headers=headers
        self.calls=[]
    def open(self,req,*,timeout):
        self.calls.append((req,timeout))
        return Response(self.response,status=self.status,headers=self.headers)


def mp4_bytes():
    return b'\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42'+b'\x00'*64


class ProtocolTests(unittest.TestCase):
    def test_default_route_not_verified(self):
        route=VerifiedRoute()
        self.assertFalse(route.account_protocol_verified)
        with self.assertRaises(ContractNotVerified):
            OpenLuxHttpTransport('dummy',route,opener=Opener()).post(make_edit(),timeout=20)

    def test_rejects_foreign_absolute_path(self):
        for path in ('https://evil.example/api','//evil.example/path','/api/../x','/api?a=1','/api/#x'):
            with self.subTest(path=path),self.assertRaises(ValueError):
                VerifiedRoute(submit_path=path)

    def test_query_placeholder_required(self):
        with self.assertRaises(ValueError):VerifiedRoute(query_path='/query')

    def test_method_and_auth_fixed(self):
        opener=Opener(b'{"id":"remote-001"}')
        transport=OpenLuxHttpTransport('dummy-secret',VerifiedRoute(account_protocol_verified=True),opener=opener)
        result=transport.post(make_edit(),timeout=10)
        self.assertEqual(result['id'],'remote-001')
        request=opener.calls[0][0]
        self.assertEqual(request.get_method(),'POST')
        self.assertEqual(request.full_url,'https://api.openlux.ai/api/v3/contents/generations/tasks')
        self.assertEqual(request.get_header('Authorization'),'Bearer dummy-secret')
        self.assertNotIn('dummy-secret',repr(result))

    def test_query_id_injected_only_if_valid(self):
        opener=Opener(b'{"status":"running"}')
        transport=OpenLuxHttpTransport('dummy',VerifiedRoute(account_protocol_verified=True),opener=opener)
        with self.assertRaises(ContractError):transport.get('../other',timeout=10)
        self.assertEqual(len(opener.calls),0)
        transport.get('task_123',timeout=10)
        self.assertTrue(opener.calls[0][0].full_url.endswith('/task_123'))

    def test_metadata_wrapper_does_not_drop_media(self):
        opener=Opener(b'{"id":"remote-001"}')
        transport=OpenLuxHttpTransport('dummy',VerifiedRoute(payload_shape='metadata',account_protocol_verified=True),opener=opener)
        transport.post(make_edit(),timeout=10)
        wire=json.loads(opener.calls[0][0].data)
        self.assertEqual(wire['metadata']['content'][1]['role'],'reference_video')
        self.assertEqual(wire['metadata']['omni_reference_task_type'],'edit')

    def test_oversized_json_blocked(self):
        with self.assertRaises(ContractError):_bound_json(Response(b'{' + b' '*300000+b'}'))

    def test_non_object_json_blocked(self):
        with self.assertRaises(ContractError):_bound_json(Response(b'[]'))


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.payload=make_edit()
        self.fake=FakeTransport()
        self.gateway=DurableVideoGateway(Path(self.temp.name)/'ledger.sqlite',self.fake,expected_account='account_one',verified_transport=True)
        self.collector=SafeMp4Collector({'cdn.example.com'},opener=Opener(mp4_bytes()),
                                          resolver=lambda *x,**y:[(0,0,0,'',('8.8.8.8',443))], video_probe=lambda path: True)
        self.bridge=ProviderLifecycle(self.gateway,self.collector,backend_account_id='account_one')

    def approval(self,**overrides):
        now=time.time()
        kwargs=dict(account_id='account_one',request_hash=request_sha256(self.payload),
                quoted_usd='0.27',limit_usd='0.50',issued_at_epoch=now-10,
                expires_at_epoch=now+100,contract_verified=True,approved_by_user=True)
        kwargs.update(overrides)
        return SpendEnvelope(**kwargs)

    def test_budget_amount_is_required(self):
        for bad in ('', 'nan', 'NaN', 'Infinity', '-1', '1e9999'):
            with self.subTest(bad=bad):
                with self.assertRaises(PermissionError):self.bridge.start(self.payload,self.approval(quoted_usd=bad))
        self.assertEqual(self.fake.posts,0)

    def test_budget_over_limit_blocked(self):
        with self.assertRaises(PermissionError):self.bridge.start(self.payload,self.approval(quoted_usd='0.51'))
        self.assertEqual(self.fake.posts,0)

    def test_no_verified_price_or_consent_blocked(self):
        for params in ({'contract_verified':False},{'approved_by_user':False},
                       {'request_hash':'0'*64},{'account_id':'account_two'}):
            with self.subTest(params=params),self.assertRaises(PermissionError):
                self.bridge.start(self.payload,self.approval(**params))
        self.assertEqual(self.fake.posts,0)

    def test_expired_or_overlong_consent_blocked(self):
        for params in ({'expires_at_epoch':time.time()-2},{'expires_at_epoch':time.time()+10000}):
            with self.subTest(params=params),self.assertRaises(PermissionError):
                self.bridge.start(self.payload,self.approval(**params))
        self.assertEqual(self.fake.posts,0)

    def test_complete_start_poll_collect_mp4(self):
        self.bridge.start(self.payload,self.approval())
        progress=self.bridge.poll(self.payload)
        self.assertEqual(progress['phase'],'ready')
        with patch.object(self.bridge.collector,'_verify_host') as hostcheck:
            out=self.bridge.collect(self.payload,Path(self.temp.name)/'result.mp4')
        self.assertEqual(out['size'],len(mp4_bytes()))
        self.assertEqual(out['task_id'],'task_valid_123')
        self.assertEqual((Path(self.temp.name)/'result.mp4').read_bytes(),mp4_bytes())
        self.assertEqual(self.fake.posts,1)
        self.assertEqual(self.fake.polls,2)
        self.assertNotIn('SIGNED',str(self.gateway.info(self.payload)))

    def test_collect_without_ack_blocked(self):
        with self.assertRaises(PermissionError):self.bridge.collect(self.payload,Path(self.temp.name)/'x.mp4')

    def test_collect_no_overwrite(self):
        p=Path(self.temp.name)/'result.mp4'
        p.write_bytes(b'original')
        with self.assertRaises(MediaFailure):
            with patch.object(self.collector,'_verify_host'):
                self.collector.collect('https://cdn.example.com/vid.mp4',p)
        self.assertEqual(p.read_bytes(),b'original')

    def test_collect_truncated_media_cleanup(self):
        self.collector._opener=Opener(b'<html>error</html>')
        p=Path(self.temp.name)/'out.mp4'
        with patch.object(self.collector,'_verify_host'):
            with self.assertRaises(MediaFailure):self.collector.collect('https://cdn.example.com/v.mp4',p)
        self.assertFalse(p.exists())
        self.assertEqual(list(Path(self.temp.name).glob('.hypit_download*')),[])

    def test_collect_bounded_byte_size(self):
        self.collector._opener=Opener(mp4_bytes()*40)
        self.collector.max_bytes=100
        p=Path(self.temp.name)/'out.mp4'
        with patch.object(self.collector,'_verify_host'):
            with self.assertRaises(MediaFailure):self.collector.collect('https://cdn.example.com/v.mp4',p)
        self.assertFalse(p.exists())

    def test_unplayable_mp4_is_rejected(self):
        self.collector._probe=lambda p: False
        p=Path(self.temp.name)/'unplayable.mp4'
        with patch.object(self.collector,'_verify_host'):
            with self.assertRaises(MediaFailure):self.collector.collect('https://cdn.example.com/v.mp4',p)
        self.assertFalse(p.exists())

    def test_no_bearer_on_signed_cdn_request(self):
        op=Opener(mp4_bytes())
        self.collector._opener=op
        with patch.object(self.collector,'_verify_host'):
            self.collector.collect('https://cdn.example.com/v.mp4?token=private',Path(self.temp.name)/'x.mp4')
        self.assertIsNone(op.calls[0][0].get_header('Authorization'))

    def test_cdn_hostname_must_be_allowlisted(self):
        with self.assertRaises(MediaFailure):self.collector.collect('https://foreign.example.com/v.mp4',Path(self.temp.name)/'x.mp4')

    def test_private_dns_rejected(self):
        self.collector._resolver=lambda *a,**k:[(0,0,0,'',('127.0.0.1',443))]
        with self.assertRaises(MediaFailure):self.collector.collect('https://cdn.example.com/v.mp4',Path(self.temp.name)/'x.mp4')

    def test_account_binding_must_match(self):
        with self.assertRaises(ValueError):ProviderLifecycle(self.gateway,self.collector,backend_account_id='different')


if __name__=='__main__':unittest.main()
