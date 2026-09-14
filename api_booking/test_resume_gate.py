"""같은 주문 복구의 혼동 방지와 실제 DOM 판독 회귀. 실사이트 연결 없음."""
import copy
import hashlib
import json
from decimal import Decimal
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from handoff import ScreenOrder
from playwright.sync_api import sync_playwright
import resume_gate
import site_drive


def fixture():
    saved = dict(pnr='FIXTURE', currency='KRW', totalAmount='349500', mileage='35000',
                 passengerFingerprint=hashlib.sha256(json.dumps(['test-id']).encode()).hexdigest(),
                 target=dict(origin='ICN', destination='CDG', flight='901', carrier='KE',
                             family='EY', date='2027-09-07'))
    model = dict(pnr='FIXTURE', pnrFareInfo=dict(currency='KRW', totalAmount='349500', mileage='35000'),
                 travellerFareInfoList=[dict(travellerId='test-id')],
                 boundList=[dict(segmentList=[dict(departureAirport='ICN', arrivalAirport='CDG',
                    flightNumber='901', operationCarrierCode='KE', fareFamily='EY',
                    departureDateTime='20270907112000', status='HK')])])
    return model, saved


class ModelTest(unittest.TestCase):
    def test_conflicting_checkout_amounts_cannot_become_success(self):
        from unittest.mock import Mock
        page = Mock()
        candidate = Mock()
        candidate.is_closed.return_value = False
        candidate.opener.return_value = page
        candidate.evaluate.side_effect = ['349500원', ['1원 결제하기'], ['결제금액349500원']]
        page.context.pages = [candidate]
        with patch('payment_window.inspect_payment_window', return_value={'ready': True}):
            result = site_drive.wait_provider_window(page, [], expected='npay', amount=349500)
        self.assertFalse(result['amountMatched'])
        self.assertEqual(result['amountVerdict'], 'conflicting-payment-amounts')

    def test_network_match_still_revalidates_current_member(self):
        model, saved = fixture()
        page = SimpleNamespace(url=site_drive.GATE)
        watch = resume_gate.StoredOrderWatch(None, page=page, saved=saved, member='member')
        current = dict(model=model, member='other', status=dict(isLoginExpired=False, isSessionExpired=False))
        with patch.object(resume_gate.BridgeGuard, 'judge', return_value=ScreenOrder('same-order-reference', same_reference=True)), patch.object(resume_gate, 'read_context', return_value=current):
            self.assertFalse(watch.judge('FIXTURE', 0).same_reference)
            current['member'] = 'member'
            self.assertTrue(watch.judge('FIXTURE', 0).same_reference)

    def test_network_failures_are_never_overridden(self):
        model, saved = fixture()
        watch = resume_gate.StoredOrderWatch(None, page=SimpleNamespace(url=site_drive.GATE), saved=saved, member='member')
        for state in ('other-order', 'new-order-requested', 'reference-unreadable'):
            with patch.object(resume_gate.BridgeGuard, 'judge', return_value=ScreenOrder(state)):
                self.assertEqual(watch.judge('FIXTURE', 0).state, state)

    def test_same_model(self):
        self.assertTrue(resume_gate.validate_model(*fixture()))

    def test_other_order_passenger_amount_and_itinerary_fail(self):
        for field in ('pnr', 'passenger', 'amount', 'date', 'status', 'currency', 'extra_leg'):
            with self.subTest(field=field):
                model, saved = fixture()
                leg = model['boundList'][0]['segmentList'][0]
                if field == 'pnr': model['pnr'] = 'OTHER'
                elif field == 'passenger': model['travellerFareInfoList'][0]['travellerId'] = 'other'
                elif field == 'amount': model['pnrFareInfo']['totalAmount'] = '1'
                elif field == 'currency': model['pnrFareInfo']['currency'] = 'USD'
                elif field == 'date': leg['departureDateTime'] = '20270908112000'
                elif field == 'status': leg['status'] = 'HL'
                else: model['boundList'][0]['segmentList'].append(copy.deepcopy(leg))
                self.assertFalse(resume_gate.validate_model(model, saved))

    def test_missing_data_fails(self):
        model, saved = fixture()
        self.assertFalse(resume_gate.validate_model({}, saved))
        self.assertFalse(resume_gate.validate_model(model, {}))


class DomTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True, args=['--host-resolver-rules=MAP * ~NOTFOUND'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.page.route('**/*', lambda route: route.abort())
        self.addCleanup(self.page.close)

    def test_accessible_amount_ignores_animation_without_dom_mutation(self):
        self.page.set_content('<div><span>결제금액</span><span aria-hidden="true">0123456789</span>349500원</div>')
        text = site_drive.payment_amount_texts(self.page)
        self.assertEqual(text, ['결제금액349500원'])
        self.assertEqual(site_drive.amount_verdict(text[0], Decimal('349500.00')), 'matched')
        self.assertEqual(self.page.locator('[aria-hidden=true]').count(), 1)

    def test_wrong_amount_and_hidden_total(self):
        self.page.set_content('<div><span>결제금액</span>1원</div><div style="display:none"><span>결제금액</span>349500원</div>')
        text = site_drive.payment_amount_texts(self.page)
        self.assertEqual(len(text), 1)
        self.assertNotEqual(site_drive.amount_verdict(text[0], Decimal('349500')), 'matched')

    def test_mileage_requires_completed_cancel_and_matching_heading(self):
        for completed, cancel, amount, expected in [(True, True, '35,000', True),
                (False, True, '35,000', False), (True, False, '35,000', False),
                (True, True, '30,000', False), (True, True, '135,000', False)]:
            with self.subTest(completed=completed, cancel=cancel, amount=amount):
                self.page.set_content('<div id="award-use-mileage" class="%s"><h3 id="expander-award-use-mileage">%s 마일</h3>%s</div>' %
                    ('-completed' if completed else '', amount, '<button>적용취소</button>' if cancel else ''))
                self.assertEqual(resume_gate.applied_mileage(self.page, '35,000')['verified'], expected)

    def test_apply_without_deduct_network_and_no_second_click(self):
        self.page.set_content('''<div id="award-use-mileage"><h3 id="expander-award-use-mileage">35,000 마일</h3>
          <button id="btnAwardUseMileageApply" onclick="window.clicks=(window.clicks||0)+1;this.parentElement.className='-completed';this.textContent='적용취소'">적용</button></div>''')
        self.assertTrue(resume_gate.ensure_mileage(self.page,'35,000')['verified'])
        self.assertFalse(resume_gate.ensure_mileage(self.page,'35,000')['clicked'])
        self.assertEqual(self.page.evaluate('window.clicks'),1)


if __name__ == '__main__':
    unittest.main()
