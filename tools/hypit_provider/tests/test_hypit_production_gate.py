"""Gate and lifecycle combined regression tests; fake Hypit CLI and model."""
import dataclasses
import tempfile
import time
import unittest
from pathlib import Path

from core.hypit_openlux_gateway import ContractError, DurableVideoGateway, request_sha256
from core.hypit_agent_runtime import ProjectTools
from core.hypit_provider_bridge import ProviderLifecycle,SafeMp4Collector,SpendEnvelope
from core.hypit_production_gate import HypitProductionGate
from tests.test_hypit_openlux_gateway import FakeTransport,make_edit


class ProductionGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.project=root/'project';self.project.mkdir()
        (self.project/'main.svml').write_text('hypit source',encoding='utf8')
        (self.project/'build.svrun').write_text('hypit run',encoding='utf8')
        self.skill=root/'skill';(self.skill/'references').mkdir(parents=True)
        (self.skill/'SKILL.md').write_text('---\nname: hypit\n---\n',encoding='utf8')
        self.cli=root/'fake_hypit.exe';self.cli.write_text('fixture',encoding='utf8')
        self.log=[]
        self.tools=ProjectTools(self.project,self.skill,self.cli,
                         cli_runner=lambda c,s:self.log.append((c,s)) or {'ok':True})
        self.payload=make_edit()
        self.remote=FakeTransport()
        ledger=DurableVideoGateway(root/'ledger.sqlite',self.remote,
                                   expected_account='account_one',verified_transport=True)
        self.provider=ProviderLifecycle(ledger,SafeMp4Collector({'cdn.example.com'},
             video_probe=lambda _:True),backend_account_id='account_one')
        self.gate=HypitProductionGate(self.project,self.tools,self.provider,secret_key=b'a'*32)
        self.spend=SpendEnvelope('account_one',request_sha256(self.payload),'0.20','0.35',
                 time.time()-10,time.time()+100,contract_verified=True,approved_by_user=True)

    def test_real_order_check_plan_then_submit(self):
        proof=self.gate.preflight(self.payload)
        self.assertEqual(self.log,[('check','main.svml'),('plan','build.svrun')])
        self.assertEqual(self.remote.posts,0)
        self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,1)

    def test_tampered_source_blocked_before_paid_api(self):
        proof=self.gate.preflight(self.payload)
        (self.project/'main.svml').write_text('tampered',encoding='utf8')
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_added_svs_invalidates_proof(self):
        proof=self.gate.preflight(self.payload)
        (self.project/'style.svs').write_text('new style',encoding='utf8')
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_local_asset_replacement_invalidates_spending_proof(self):
        assets = self.project / 'assets'
        assets.mkdir()
        video = assets / 'reference.mp4'
        video.write_bytes(b'original media')
        proof = self.gate.preflight(self.payload)
        video.write_bytes(b'replaced media')
        with self.assertRaises(PermissionError): self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_removed_source_invalidates_proof(self):
        proof=self.gate.preflight(self.payload)
        (self.project/'build.svrun').unlink()
        with self.assertRaises(ContractError):self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_runtime_profile_mutation_invalidates_proof(self):
        (self.project/'hypit.runtime.json').write_text('{"bindings":{}}')
        proof=self.gate.preflight(self.payload)
        (self.project/'hypit.runtime.json').write_text('{"bindings":{"other":1}}')
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_provider_package_mutation_invalidates_proof(self):
        package=self.project/'packages'/'provider-openlux'
        package.mkdir(parents=True)
        (package/'provider.js').write_text('export const a=1;')
        proof=self.gate.preflight(self.payload)
        (package/'provider.js').write_text('export const a=2;')
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_tampered_signature_blocked(self):
        proof=self.gate.preflight(self.payload)
        forged=dataclasses.replace(proof,signature='f'*64)
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,forged)
        self.assertEqual(self.remote.posts,0)

    def test_wrong_request_invalidates_proof(self):
        proof=self.gate.preflight(self.payload)
        payload=dict(self.payload,generate_audio=False)
        with self.assertRaises(PermissionError):self.gate.start(payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_hypit_check_fail_rejects(self):
        self.tools.runner=lambda c,s:{'ok':False,'error':'check error'}
        with self.assertRaises(ContractError):self.gate.preflight(self.payload)
        self.assertEqual(self.remote.posts,0)

    def test_hypit_plan_fail_rejects(self):
        self.tools.runner=lambda c,s:{'ok':c=='check'}
        with self.assertRaises(ContractError):self.gate.preflight(self.payload)
        self.assertEqual(self.remote.posts,0)

    def test_expired_proof_denies(self):
        proof=self.gate.preflight(self.payload,lifetime_seconds=30)
        self.gate.clock=lambda:proof.expires_at+1
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,proof)
        self.assertEqual(self.remote.posts,0)

    def test_missing_proof_denies(self):
        with self.assertRaises(PermissionError):self.gate.start(self.payload,self.spend,None)
        self.assertEqual(self.remote.posts,0)

    def test_failed_spend_after_successful_hypit_no_submit(self):
        proof=self.gate.preflight(self.payload)
        bad=dataclasses.replace(self.spend,quoted_usd='6.00')
        with self.assertRaises(PermissionError):self.gate.start(self.payload,bad,proof)
        self.assertEqual(self.remote.posts,0)

if __name__=='__main__':unittest.main()
