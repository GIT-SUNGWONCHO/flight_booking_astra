from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import order_evidence as store
from test_state_bridge import Fixture
from test_live_order import FlowHarness,CAL_URL
import live_order

class EvidenceTests(Fixture,unittest.TestCase):
    def setUp(self):
        self.setup_fixture();self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.run='abcdef123456'
        data=json.loads(self.order['body'])
        data.update(pnr='FAKEPNR',orderId='FAKE-ORDER',token='SECRET_TOKEN',
            travellerInfoList=[{'name':'SECRET_NAME','travellerId':'SECRET_ID'}],
            headers={'Authorization':'SECRET_AUTH'},pageTicket='SECRET_TICKET')
        self.order.update(body=json.dumps(data),startedAt=1000.)
    def save(self):
        return store.save_received(self.root,run_id=self.run,target=self.quote.target,
            response=self.order,quote=self.quote,passenger_fingerprint='a'*64,received_at=1006.)
    def test_allowlist_round_trip_preserves_reference_on_failed_judgment(self):
        path=self.save();before=path.read_bytes()
        store.save_judgment(self.root,self.run,'amount-mismatch')
        self.assertEqual(before,path.read_bytes())
        data=store.load_received(self.root,self.run)
        self.assertEqual(data['pnr'],'FAKEPNR');self.assertEqual(data['orderId'],'FAKE-ORDER')
        self.assertEqual(data['passengerFingerprint'],'a'*64)
        self.assertNotIn('SECRET',path.read_text());self.assertIsNone(data['serverOrderCreatedAt'])
        self.assertNotEqual(data['orderRequestStartedAt'],data['orderResponseReceivedAt'])
    def test_invalid_or_missing_response_keeps_missing_fields_null(self):
        self.order['body']='not json SECRET'
        data=json.loads(self.save().read_text())
        self.assertIsNone(data['pnr']);self.assertIsNone(data['orderId'])
        self.assertFalse(data['diagnostic']['responseObject'])
    def test_no_overwrite_or_path_traversal(self):
        self.save()
        with self.assertRaises(ValueError):self.save()
        with self.assertRaises(ValueError):store.load_received(self.root,'../secret')
    def test_business_error_code_and_message_are_persisted_without_identifiers(self):
        # 2026-09-19 승인(AGENTS §5). 9/19 09:00 business-error 는 이 기록이 없어 원인을 못 가렸다.
        self.order['body']=json.dumps({'code':'ERT.20001','message':'좌석 없음 SECRET9 1234567',
            'trace':'SECRET_TRACE','uuid':'SECRET_UUID','status':400})
        data=json.loads(self.save().read_text(encoding='utf-8'))
        self.assertIsNone(data['pnr'])
        self.assertEqual(data['diagnostic']['error'],
                         {'code':'ERT.20001','status':400,'message':'좌석 없음 <id> <n>'})
        self.assertNotIn('SECRET',json.dumps(data,ensure_ascii=False))
    def test_success_receipt_has_no_error_block(self):
        self.assertIsNone(json.loads(self.save().read_text(encoding='utf-8'))['diagnostic']['error'])
    def test_unknown_error_text_is_not_persisted(self):
        store.save_judgment(self.root,self.run,'SECRET_EXCEPTION')
        self.assertNotIn('SECRET', (self.root/'order-evidence'/self.run/'judgment.json').read_text())

class EvidenceRunnerTests(FlowHarness):
    def test_disk_delay_does_not_change_reported_response_time(self):
        original=live_order.time.monotonic
        offset=[0.]
        def slow_store(*args,**kwargs):offset[0]=100.
        with patch.object(live_order.order_evidence,'save_received',side_effect=slow_store), \
             patch.object(live_order.time,'monotonic',side_effect=lambda:original()+offset[0]):
            code,_,_,_=self.run_main(initial_url=CAL_URL)
        self.assertEqual(code,0)
        line=next(x for x in self.logs if x.startswith('주문 status='))
        seconds=float(line.rsplit('(+',1)[1].split('s)',1)[0])
        self.assertLess(seconds,100.)

    def test_receipt_saved_before_judge_exception(self):
        with patch.object(live_order.pipeline.Pipeline,'judge_order',side_effect=ValueError('SECRET')):
            code,_,calls,_=self.run_main(initial_url=CAL_URL)
        self.assertEqual(code,2)
        paths=list(self.tmp.rglob('received.json'))
        self.assertEqual(len(paths),1)
        self.assertEqual(json.loads(paths[0].read_text())['pnr'],'FAKEPNR')
        self.assertEqual(sum(c.startswith('send:inputTravellers') for c in calls),1)

if __name__=='__main__':unittest.main()


import order_evidence  # noqa: E402


class ServerCreatedTests(unittest.TestCase):
    """주문 응답 생성 시각은 정밀도와 간격만 남긴다(원문 저장 없음, 9/20)."""

    def test_second_precision_gives_gap_from_request(self):
        out = order_evidence.server_created({'createDateTime': '20260920090003'}, 1789862400.0)
        row = out['createDateTime']
        self.assertEqual(row['precision'], 'second')
        self.assertIsInstance(row['secondsFromRequest'], float)

    def test_minute_precision_is_flagged(self):
        out = order_evidence.server_created({'createDateTimeOfKST': '20260920180000'}, None)
        self.assertEqual(out['createDateTimeOfKST']['precision'], 'minute')
        self.assertNotIn('secondsFromRequest', out['createDateTimeOfKST'])

    def test_missing_or_broken_fields_are_none(self):
        self.assertIsNone(order_evidence.server_created({}, None))
        self.assertIsNone(order_evidence.server_created(None, None))
        self.assertEqual(order_evidence.server_created({'createDateTime': 'x'}, None)
                         ['createDateTime']['precision'], 'unknown-format')

    def test_raw_value_is_not_stored(self):
        out = order_evidence.server_created({'createDateTime': '20260920090003'}, None)
        self.assertNotIn('20260920090003', json.dumps(out))
