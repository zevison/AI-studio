import dataclasses
import tempfile
import time
import unittest
from pathlib import Path
from core.hypit_token_budget import BoundedChat,TokenGrant

class TokenBudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'tokens.db'
        self.grant=TokenGrant('offline','fixed-model','0.09','0.45','0.50',4,2048,time.time()+600,True,True)
        self.calls=[]
        def sender(payload):
            self.calls.append(payload)
            return {'usage':{'prompt_tokens':1000,'completion_tokens':100},'choices':[]}
        self.sender=sender

    def test_output_cap_and_four_call_maximum_are_persisted(self):
        chat=BoundedChat(self.path,self.grant,self.sender)
        for _ in range(4): chat({'model':'fixed-model','messages':[]})
        self.assertEqual(self.calls[0]['max_completion_tokens'],2048)
        reopened=BoundedChat(self.path,self.grant,self.sender)
        with self.assertRaises(PermissionError): reopened({'model':'fixed-model'})
        self.assertEqual(len(self.calls),4)

    def test_ambiguous_call_blocks_all_retries_after_reopen(self):
        def sender(payload): self.calls.append(payload);raise TimeoutError('uncertain')
        chat=BoundedChat(self.path,self.grant,sender)
        with self.assertRaises(TimeoutError):chat({'model':'fixed-model'})
        reopened=BoundedChat(self.path,self.grant,sender)
        with self.assertRaises(PermissionError):reopened({'model':'fixed-model'})
        self.assertEqual(len(self.calls),1)

    def test_forged_model_and_output_cap_refused_before_sender(self):
        chat=BoundedChat(self.path,self.grant,self.sender)
        for payload in [{'model':'costlier'},{'model':'fixed-model','max_tokens':9999}]:
            with self.assertRaises(PermissionError):chat(payload)
        self.assertEqual(self.calls,[])

    def test_unknown_rate_and_expensive_forecast_refused(self):
        with self.assertRaises(PermissionError):BoundedChat(self.path,dataclasses.replace(self.grant,rates_verified=False),self.sender)
        chat=BoundedChat(self.path,dataclasses.replace(self.grant,input_usd_per_million='1000000'),self.sender)
        with self.assertRaises(PermissionError):chat({'model':'fixed-model'})
        self.assertEqual(self.calls,[])

    def test_missing_usage_quarantines_grant(self):
        chat=BoundedChat(self.path,self.grant,lambda payload:{})
        with self.assertRaises(PermissionError):chat({'model':'fixed-model'})
        with self.assertRaises(PermissionError):chat({'model':'fixed-model'})
        self.assertEqual(len(chat.evidence()),1)

    def test_reconciliation_requires_positive_zero_charge_error_evidence(self):
        def broken(payload):raise TimeoutError('unknown')
        chat=BoundedChat(self.path,self.grant,broken)
        with self.assertRaises(TimeoutError):chat({'model':'fixed-model'})
        call=chat.evidence()[0]
        with self.assertRaises(PermissionError):chat.reconcile_rejection(call['id'],{})
        evidence={'model_name':'fixed-model','type':5,'quota':0,'prompt_tokens':0,'completion_tokens':0,
            'created_at':call['created']+1,'billing_metadata':{'error_code':'invalid_request_error','request_id':'vendor-rejection-1'}}
        chat.reconcile_rejection(call['id'],evidence)
        self.assertEqual(chat.evidence()[0]['reserved_usd'],'0.125')
        chat.sender=self.sender;chat({'model':'fixed-model'})
        self.assertEqual(len(chat.evidence()),2)
