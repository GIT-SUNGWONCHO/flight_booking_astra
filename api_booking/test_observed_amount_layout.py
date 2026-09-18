"""9/14 수치 형태를 합성 자료로 재현한다. 실제 고객 응답 원문은 사용하지 않는다."""
from dataclasses import replace
from decimal import Decimal
import json
import unittest
import travellers
from test_state_bridge import Fixture


class ObservedLayoutTests(Fixture,unittest.TestCase):
    def setUp(self):
        self.setup_fixture()
        self.quote=replace(self.quote,target=replace(self.quote.target,family='KEBONUSEY'),
            amount=Decimal(0),total_amount=Decimal(349500),mileage=Decimal(35000))
        for response,amount in ((self.fare,'0'),(self.order,'349500.00')):
            data=json.loads(response['body'])
            data['boundList'][0]['segmentList'][0]['fareFamily']='KEBONUSEY'
            data['pnrFareInfo'].update(amount=amount,totalAmount='349500.00',mileage='35000')
            response['body']=json.dumps(data)

    def judge(self,enabled=True):
        return travellers.judge(self.request,200,self.order['body'],quote=self.quote,
            target=self.quote.target,session=self.quote.session,subject=self.request.subject,
            now=104.,allow_observed_amount_layout=enabled)

    def test_explicit_research_policy_preserves_actual_difference(self):
        self.assertEqual(self.judge(False).state,'amount-mismatch')
        verdict=self.judge()
        self.assertEqual(verdict.state,'order-recorded')
        self.assertFalse(verdict.order.amounts_matched)
        self.assertTrue(verdict.order.payment_amounts_matched)
        self.assertEqual(verdict.order.amount_layout,'observed-zero-to-total')
        self.assertFalse(verdict.seat_hold_verified)

    def test_bridge_preserves_both_original_payloads(self):
        with self.assertRaisesRegex(ValueError,'unverified-order'):self.build()
        patch=self.build(allow_observed_amount_layout=True)
        self.assertEqual(json.loads(patch.after['fareInformation'])['model']['pnrFareInfo']['amount'],'0')
        self.assertEqual(json.loads(patch.after['inputTravellers'])['model']['pnrFareInfo']['amount'],'349500.00')

    def test_other_amount_total_mileage_currency_rejected(self):
        original=self.order['body']
        for key,value in [('amount','349501'),('amount','0.01'),('totalAmount','349501'),
                          ('mileage','35001'),('currency','USD')]:
            data=json.loads(original);data['pnrFareInfo'][key]=value
            self.order['body']=json.dumps(data)
            with self.subTest(key=key,value=value):
                self.assertNotEqual(self.judge().state,'order-recorded')

    def test_prestige_target_enabled_by_2026_09_19_policy(self):
        # 사용자 승인(2026-09-19): 노선·편·등급 고정을 이 실행의 목표로 일반화한다.
        target=replace(self.quote.target,family='KEBONUSPR')
        self.quote=replace(self.quote,target=target)
        data=json.loads(self.order['body'])
        data['boundList'][0]['segmentList'][0]['fareFamily']='KEBONUSPR'
        self.order['body']=json.dumps(data)
        verdict=self.judge()
        self.assertEqual(verdict.state,'order-recorded')
        self.assertEqual(verdict.order.amount_layout,'observed-zero-to-total')
        self.assertEqual(self.judge(False).state,'amount-mismatch')

    def test_nonzero_original_amount_not_reinterpreted(self):
        self.quote=replace(self.quote,amount=Decimal(1))
        self.assertEqual(self.judge().state,'amount-mismatch')

if __name__=='__main__':unittest.main()
