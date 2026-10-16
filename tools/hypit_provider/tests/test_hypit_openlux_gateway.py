"""Purely local contract tests; NEVER call live OpenLux or charge models."""
import concurrent.futures
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.hypit_openlux_gateway import (
    ContractError, DurableVideoGateway, ExplicitApproval, SubmissionUnknown,
    build_video_request, request_sha256, result_url_from, state_from, task_id_from,
)


def source_video():
    return {'role':'reference_video','url':'https://assets.example.com/video/001.mp4'}


def make_edit(**overrides):
    args = dict(prompt='Change the product, preserve scene structure', references=[source_video()],
                task_type='edit', duration=-1, ratio='adaptive', resolution='720p', generate_audio=True)
    args.update(overrides)
    return build_video_request(**args)


class FakeTransport:
    def __init__(self, *, mode='normal'):
        self.mode = mode
        self.posts = 0
        self.polls = 0
        self.lock = threading.Lock()
    def post(self, payload, *, timeout):
        with self.lock:
            self.posts += 1
        if self.mode == 'ambiguous':
            raise TimeoutError('lost response after service may have accepted')
        if self.mode == 'no_id':
            return {'status':'queued'}
        return {'id':'task_valid_123', 'status':'queued'}
    def get(self, task_id, *, timeout):
        with self.lock:
            self.polls += 1
        if self.mode == 'unknown_poll':
            return {'status':'unknown'}
        if self.mode == 'missing_output':
            return {'status':'succeeded'}
        if self.mode == 'wrong_identity':
            return {'id':'another_task','status':'succeeded','output_url':'https://assets.example.com/result.mp4'}
        return {'id':task_id,'status':'succeeded', 'output_url':'https://assets.example.com/result.mp4?sig=SIGNED'}


class ContractTests(unittest.TestCase):
    def test_edit_valid(self):
        x = make_edit()
        self.assertEqual(x['duration'],-1)
        self.assertEqual(x['ratio'],'adaptive')
        self.assertEqual(x['omni_reference_task_type'],'edit')
        self.assertEqual(x['content'][1]['role'],'reference_video')

    def test_reference_type_keeps_content(self):
        x=build_video_request(prompt='new video',references=[source_video()],task_type='reference',
              ratio='9:16', duration=8,resolution='480p',generate_audio=False)
        self.assertEqual(len(x['content']),2)

    def test_empty_edit_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[])

    def test_edit_reference_image_only_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[{'role':'reference_image','url':'https://a.example.com/a.png'}])

    def test_edit_wrong_duration_blocked(self):
        with self.assertRaises(ContractError):make_edit(duration=10)

    def test_edit_wrong_ratio_blocked(self):
        with self.assertRaises(ContractError):make_edit(ratio='9:16')

    def test_unknown_reference_role_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[{'role':'depth_video','url':'https://a.example.com/a.mp4'}])

    def test_unknown_media_field_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[dict(source_video(), special='data')])

    def test_reference_person_classification_refused_until_contract_verified(self):
        with self.assertRaises(ContractError):
            make_edit(references=[source_video(), {'role':'reference_image','url':'https://x.example.com/x.png', 'person_reference':True}])

    def test_too_many_videos_blocked(self):
        refs=[{'role':'reference_video','url':f'https://a.example.com/{i}.mp4'} for i in range(11)]
        with self.assertRaises(ContractError):make_edit(references=refs)

    def test_duplicate_source_videos_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[source_video(),source_video()])

    def test_local_reference_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[{'role':'reference_video','url':'https://127.0.0.1/x'}])

    def test_ssrf_credentialed_url_blocked(self):
        with self.assertRaises(ContractError):make_edit(references=[{'role':'reference_video','url':'https://user:pass@a.example.com/vid'}])

    def test_silent_model_fallback_forbidden(self):
        with self.assertRaises(ContractError):make_edit(model='some-other-model')

    def test_unknown_status_not_running(self):
        with self.assertRaises(ContractError):state_from({'status':'PENDING_MAYBE'})

    def test_status_conflict_blocked(self):
        with self.assertRaises(ContractError):state_from({'status':'running','data':{'status':'failed'}})

    def test_extract_completed(self):
        self.assertEqual(state_from({'data':{'status':'succeeded'}}),'ready')

    def test_response_without_id_blocked(self):
        with self.assertRaises(ContractError):task_id_from({'status':'queued'})

    def test_result_missing_output_blocked(self):
        with self.assertRaises(ContractError):result_url_from({'status':'succeeded','pricing_url':'https://evil.example.com/p'})

    def test_result_unknown_url_field_not_guessed(self):
        with self.assertRaises(ContractError):result_url_from({'status':'succeeded','web_site':'https://evil.example.com/p'})


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Path(self.temp.name)/'ledger.db'
        self.transport = FakeTransport()
        self.g = DurableVideoGateway(self.ledger,self.transport,expected_account='user_token_slot',verified_transport=True)
        self.payload=make_edit()
        self.approval=ExplicitApproval(request_hash=request_sha256(self.payload),account_id='user_token_slot',
               contract_confirmed=True,spend_confirmed=True,expires_at_epoch=time.time()+300)

    def test_unverified_transport_blocks_no_post(self):
        g=DurableVideoGateway(self.ledger, self.transport,expected_account='user_token_slot')
        with self.assertRaises(PermissionError):g.submit(self.payload,self.approval)
        self.assertEqual(self.transport.posts,0)

    def test_expired_approval_blocks_before_post(self):
        expired=ExplicitApproval(request_hash=self.approval.request_hash,account_id='user_token_slot',
                  contract_confirmed=True,spend_confirmed=True,expires_at_epoch=time.time()-1)
        with self.assertRaises(PermissionError):self.g.submit(self.payload,expired)
        self.assertEqual(self.transport.posts,0)

    def test_out_of_scope_approval_blocked(self):
        long=ExplicitApproval(request_hash=self.approval.request_hash,account_id='user_token_slot',
                  contract_confirmed=True,spend_confirmed=True,expires_at_epoch=time.time()+7200)
        with self.assertRaises(PermissionError):self.g.submit(self.payload,long)
        self.assertEqual(self.transport.posts,0)

    def test_no_cost_approval_blocks_no_post(self):
        approval=ExplicitApproval(request_hash=self.approval.request_hash,account_id='user_token_slot',contract_confirmed=True,expires_at_epoch=time.time()+300)
        with self.assertRaises(PermissionError):self.g.submit(self.payload,approval)
        self.assertEqual(self.transport.posts,0)

    def test_account_mismatch_blocks(self):
        approval=ExplicitApproval(request_hash=self.approval.request_hash,account_id='other_account',contract_confirmed=True,spend_confirmed=True,expires_at_epoch=time.time()+300)
        with self.assertRaises(PermissionError):self.g.submit(self.payload,approval)
        self.assertEqual(self.transport.posts,0)

    def test_hash_mismatch_blocks(self):
        bad=ExplicitApproval(request_hash='a'*64,account_id='user_token_slot',contract_confirmed=True,spend_confirmed=True,expires_at_epoch=time.time()+300)
        with self.assertRaises(PermissionError):self.g.submit(self.payload,bad)
        self.assertEqual(self.transport.posts,0)

    def test_start_receipt_then_poll(self):
        self.assertEqual(self.g.submit(self.payload,self.approval),'task_valid_123')
        self.assertEqual(self.g.info(self.payload)['phase'],'submitted')
        polled=self.g.poll(self.payload)
        self.assertEqual(polled['phase'],'ready')
        self.assertEqual(polled['task_id'],'task_valid_123')
        self.assertNotIn('SIGNED',str(self.g.info(self.payload)))

    def test_repeat_submit_is_refused(self):
        self.g.submit(self.payload,self.approval)
        with self.assertRaises(SubmissionUnknown):self.g.submit(self.payload,self.approval)
        self.assertEqual(self.transport.posts,1)

    def test_ambiguous_post_must_never_retry(self):
        self.transport.mode='ambiguous'
        with self.assertRaises(SubmissionUnknown):self.g.submit(self.payload,self.approval)
        self.assertEqual(self.g.info(self.payload)['phase'],'submission_unknown')
        with self.assertRaises(SubmissionUnknown):self.g.submit(self.payload,self.approval)
        self.assertEqual(self.transport.posts,1)

    def test_submit_missing_id_no_retry(self):
        self.transport.mode='no_id'
        with self.assertRaises(SubmissionUnknown):self.g.submit(self.payload,self.approval)
        self.assertEqual(self.transport.posts,1)
        self.assertEqual(self.g.info(self.payload)['phase'],'submission_unknown')

    def test_deny_unexpected_payload(self):
        bad=dict(self.payload, provider='unapproved-channel')
        approval=ExplicitApproval(request_hash=request_sha256(bad),account_id='user_token_slot',contract_confirmed=True,spend_confirmed=True,expires_at_epoch=time.time()+300)
        with self.assertRaises(ContractError):self.g.submit(bad,approval)
        self.assertEqual(self.transport.posts,0)

    def test_poll_unknown_status_no_wrong_success(self):
        self.g.submit(self.payload,self.approval)
        self.transport.mode='unknown_poll'
        with self.assertRaises(ContractError):self.g.poll(self.payload)
        self.assertEqual(self.g.info(self.payload)['phase'],'submitted')

    def test_cross_task_poll_response_blocked(self):
        self.g.submit(self.payload,self.approval)
        self.transport.mode='wrong_identity'
        with self.assertRaises(ContractError):self.g.poll(self.payload)
        self.assertEqual(self.g.info(self.payload)['phase'],'submitted')

    def test_poll_missing_output_not_success(self):
        self.g.submit(self.payload,self.approval)
        self.transport.mode='missing_output'
        with self.assertRaises(ContractError):self.g.poll(self.payload)
        self.assertEqual(self.g.info(self.payload)['phase'],'submitted')

    def test_concurrent_same_request_single_post(self):
        def go(_):
            try:return self.g.submit(self.payload,self.approval)
            except SubmissionUnknown:return 'duplicate'
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            values=list(pool.map(go,range(5)))
        self.assertEqual(values.count('task_valid_123'),1)
        self.assertEqual(values.count('duplicate'),4)
        self.assertEqual(self.transport.posts,1)

    def test_duplicate_across_process_objects(self):
        self.g.submit(self.payload,self.approval)
        g2=DurableVideoGateway(self.ledger,self.transport,expected_account='user_token_slot',verified_transport=True)
        with self.assertRaises(SubmissionUnknown):g2.submit(self.payload,self.approval)
        self.assertEqual(self.transport.posts,1)


if __name__=='__main__':unittest.main()
