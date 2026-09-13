"""A4a 합성 검증 증거. 실제 회원 자격/잔액/세션을 조회하지 않는다."""
from dataclasses import replace
from decimal import Decimal
import unittest

from availability import Target
from fare import Quote
from eligibility import Evidence, judge


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.target = Target('2027-09-09','ICN','CDG','KEBONUSPR','KE','901')
        self.quote = Quote(self.target,'fare-1','avail-1','session','fake-ticket',
                           Decimal(100),Decimal(120),Decimal(62500),101.,100.)
        self.evidence = Evidence(self.quote,'subject-1',102.,105.,True,True,'62500')

    def result(self, **kw):
        args=dict(quote=self.quote,evidence=self.evidence,target=self.target,session='session',
            subject='subject-1',fare_generation='fare-1',availability_generation='avail-1',now=103.)
        args.update(kw)
        return judge(**args)

    def test_exact_balance_and_surplus(self):
        for balance in ('62500',62501,Decimal(70000)):
            r=self.result(evidence=replace(self.evidence,available_mileage=balance))
            self.assertTrue(r.ready)
            self.assertFalse(r.seat_hold_verified)

    def test_missing_and_false_checks(self):
        for key in ('member_eligible','session_valid'):
            for value in (None,False,1,'true'):
                r=self.result(evidence=replace(self.evidence,**{key:value}))
                self.assertFalse(r.ready)
        self.assertEqual(self.result(evidence=None).state,'missing-evidence')

    def test_invalid_and_insufficient_balances(self):
        for value in (None,True,'NaN','Infinity',-1,'1.5','unknown'):
            self.assertEqual(self.result(evidence=replace(self.evidence,
                available_mileage=value)).state,'mileage-unverified')
        self.assertEqual(self.result(evidence=replace(self.evidence,
            available_mileage=62499)).state,'insufficient-mileage')

    def test_current_context_and_subject(self):
        for kw in ({'session':'other'}, {'subject':'other'}, {'fare_generation':'new'},
                   {'availability_generation':'new'}, {'target':replace(self.target,flight='903')}):
            self.assertEqual(self.result(**kw).state,'context-mismatch')

    def test_changed_quote_cannot_reuse_evidence(self):
        for kw in ({'mileage':Decimal(63000)}, {'page_ticket':'new-ticket'}, {'received':102.}):
            self.assertEqual(self.result(quote=replace(self.quote,**kw)).state,'context-mismatch')

    def test_expiry_future_and_bad_clocks(self):
        for kw in ({'now':105.},{'now':101.},{'max_age':0}, {'now':float('nan')},
                   {'evidence':replace(self.evidence,observed=100.)},
                   {'evidence':replace(self.evidence,expires=102.)}):
            self.assertFalse(self.result(**kw).ready)
        # 증거 기한을 늘려도 원래 조회 선택의 TTL을 연장하지 못한다.
        self.assertFalse(self.result(now=106.,evidence=replace(self.evidence,expires=110.)).ready)

    def test_family_mileage_not_silently_added(self):
        self.assertEqual(self.result(evidence=replace(self.evidence,
            own_mileage_only=False)).state,'unsupported-family-mileage')

    def test_invalid_required_mileage_and_ticket(self):
        for change in ({'mileage':Decimal('NaN')},{'mileage':Decimal(0)}, {'page_ticket':''}):
            quote=replace(self.quote,**change)
            self.assertFalse(self.result(quote=quote,evidence=replace(self.evidence,quote=quote)).ready)


if __name__ == '__main__':
    unittest.main()
