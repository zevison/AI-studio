import json,tempfile,time,unittest
from pathlib import Path
from core.hypit_llm_transport import create_bounded_adapter,OpenLuxChatSender
from core.hypit_token_budget import TokenGrant
from core.hypit_agent_runtime import openlux_chat_adapter
from tests.test_hypit_provider_bridge import Opener

class LlmProtocolTests(unittest.TestCase):
    def test_unbounded_legacy_adapter_refused(self):
        with self.assertRaises(PermissionError):openlux_chat_adapter({'model':'gpt-6-luna'})

    def test_verified_protocol_applied_before_reservation_and_wire(self):
        with tempfile.TemporaryDirectory() as tmp:
            opener=Opener(json.dumps({'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':2}}).encode())
            grant=TokenGrant('test','gpt-6-luna','.09','.45','.50',4,2048,time.time()+500,True,True)
            adapter,bank=create_bounded_adapter(Path(tmp)/'budget.db',grant,'private-test-key',opener=opener)
            adapter({'model':'gpt-6-luna','messages':[]})
            request=opener.calls[0][0];payload=json.loads(request.data)
            self.assertEqual(request.full_url,'https://api.openlux.ai/v1/chat/completions')
            self.assertEqual(payload['reasoning_effort'],'none');self.assertEqual(payload['max_completion_tokens'],2048)
            self.assertNotIn('private-test-key',str(bank.evidence()))
            from hashlib import sha256
            self.assertEqual(bank.evidence()[0]['request_hash'],sha256(request.data).hexdigest())

    def test_unverified_protocol_refused_before_network(self):
        opener=Opener()
        sender=OpenLuxChatSender('private-test-key',opener=opener)
        with self.assertRaises(PermissionError):sender({'model':'gpt-6-luna'})
        self.assertEqual(opener.calls,[])
