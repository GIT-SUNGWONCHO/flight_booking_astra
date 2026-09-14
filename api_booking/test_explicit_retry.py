import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import explicit_retry as retry
import permit

class RetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.old='abcdef123456';self.day='2026-09-14'
        self.target={'date':'2027-09-07','origin':'ICN','destination':'CDG','flight':'901','family':'KEBONUSEY'}
        self.pending={self.day:{'runId':self.old,'state':'unknown'}}
        permit.acquire(self.root,day=self.day,run_id=self.old,target=self.target,now_iso='before')
    def claim(self):return retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,pending=self.pending,at='after')
    def test_old_evidence_preserved_and_single_transfer(self):
        c=self.claim()
        self.assertEqual(permit.existing_permit(self.root)['runId'],self.old)
        saved=json.loads((self.root/'retry-history'/self.old/'previous.json').read_text())
        self.assertEqual(saved['intent']['state'],'unknown')
        self.assertIsNotNone(retry.transfer(c,run_id='new',day=self.day,target=self.target,at='send'))
        self.assertIsNone(retry.transfer(c,run_id='again',day=self.day,target=self.target,at='send'))
        self.assertEqual(permit.existing_permit(self.root)['retryOf'],self.old)
        self.assertIsNone(permit.acquire(self.root,day=self.day,run_id='other',target=self.target,now_iso='x'))
    def test_repeated_claim_refused(self):
        self.claim()
        with self.assertRaises(FileExistsError):self.claim()
    def test_other_status_or_target_refused(self):
        self.pending[self.day]['state']='ordered'
        with self.assertRaises(ValueError):self.claim()
    def test_completed_checkout_needs_separate_explicit_rehearsal(self):
        self.pending[self.day].update(state='ordered',handoff='npay-checkout',
            paymentWindowReached=True,paymentAmountsMatched=True)
        with self.assertRaises(ValueError):self.claim()
        c=retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,
            pending=self.pending,at='user-new-rehearsal',after_checkout=True)
        saved=json.loads((self.root/'retry-history'/self.old/'previous.json').read_text())
        self.assertTrue(saved['intent']['paymentWindowReached'])
        self.assertIsNotNone(retry.transfer(c,run_id='one',day=self.day,target=self.target,at='send'))
        self.assertIsNone(retry.transfer(c,run_id='two',day=self.day,target=self.target,at='send'))
    def test_changed_permit_is_not_overwritten(self):
        c=self.claim();permit.durable_json(permit.permit_path(self.root),{'runId':'other'})
        self.assertIsNone(retry.transfer(c,run_id='new',day=self.day,target=self.target,at='send'))
        self.assertEqual(permit.existing_permit(self.root)['runId'],'other')

    def test_two_claims_with_stale_permit_reads_only_one_transfer(self):
        first=self.claim();second=retry.Claim(self.root,first.previous,self.old)
        with patch.object(permit,'existing_permit',return_value=first.previous):
            self.assertIsNotNone(retry.transfer(first,run_id='one',day=self.day,target=self.target,at='send'))
            self.assertIsNone(retry.transfer(second,run_id='two',day=self.day,target=self.target,at='send'))
        self.assertEqual(permit.existing_permit(self.root)['runId'],'one')

    def test_ordered_handoff_failure_needs_explicit_flag_and_preserves_state(self):
        self.pending[self.day].update(state='ordered',handoff='bridge-exception',
            paymentWindowReached=False,paymentAmountsMatched=True)
        with self.assertRaises(ValueError):self.claim()
        retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,
            pending=self.pending,at='new-approval',after_handoff_failure=True)
        saved=json.loads((self.root/'retry-history'/self.old/'previous.json').read_text())
        self.assertEqual(saved['intent']['state'],'ordered')

    def test_completed_payment_cannot_use_handoff_failure_retry(self):
        self.pending[self.day].update(state='ordered',handoff='bridge-exception',
            paymentWindowReached=True,paymentAmountsMatched=True)
        with self.assertRaises(ValueError):
            retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,
                pending=self.pending,at='new-approval',after_handoff_failure=True)

    def test_resume_before_any_preparation_preserves_ordered_record(self):
        self.pending[self.day].update(day=self.day,state='ordered',handoff='bridge-exception',
            paymentWindowReached=False,paymentAmountsMatched=True)
        retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,
            pending=self.pending,at='new-approval',after_handoff_failure=True)
        permit.durable_json(self.root/f'order-intent-{self.day}.json',self.pending[self.day])
        c=retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,
            pending=self.pending,at='resume',resume_preparation=True,after_handoff_failure=True)
        self.assertEqual(c.previous_id,self.old)

    def resume(self):
        return retry.claim(self.root,previous_id=self.old,day=self.day,target=self.target,
                           pending={},at='resume',resume_preparation=True)
    def setup_resume(self):
        self.claim()
        permit.durable_json(self.root/f'order-intent-{self.day}.json',
            {'day':self.day,'state':'prep-no-unblocked-order'})
    def test_resume_preparation_once_preserves_history(self):
        self.setup_resume(); c=self.resume()
        with self.assertRaises(FileExistsError):self.resume()
        saved=json.loads((self.root/'retry-history'/self.old/'previous.json').read_text())
        self.assertEqual(saved['intent']['state'],'unknown')
        self.assertIsNotNone(retry.transfer(c,run_id='new',day=self.day,target=self.target,at='send'))
    def test_resume_after_actual_send_refused(self):
        self.setup_resume()
        permit.durable_json(permit.permit_path(self.root),{'runId':'new'})
        with self.assertRaises(ValueError):self.resume()
    def test_resume_uncertain_preparation_refused(self):
        self.setup_resume()
        permit.durable_json(self.root/f'order-intent-{self.day}.json',
            {'day':self.day,'state':'preparing'})
        with self.assertRaises(ValueError):self.resume()

if __name__=='__main__':unittest.main()
