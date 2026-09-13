"""A6 인계 판정의 합성 시험. 실제 창 전환·화면 주입·최종 승인 없음."""
from dataclasses import replace
from decimal import Decimal
import unittest

from availability import Target
from fare import Quote
from order_flow import Checks, Event, OrderFlow, State
from payment import Session
from travellers import Order
from handoff import (ORDER_PATH, SITE_ORIGIN, Handoff, Plan, Route, ScreenOrder,
                     compare, judge_screen_order, prepare, reference_in)


def _fixtures(**kw):
    target = kw.pop('target', Target('2027-09-09','ICN','CDG','KEBONUSPR','KE','901'))
    quote = Quote(target,'fare','avail','session','fake-ticket',
                  Decimal(100),Decimal(120),Decimal(62500),101.,100.)
    order = Order(quote,'subject','session','FAKEREF',103.,104.,'HK',False)
    session = Session('FAKE-RESERVE','FAKEREF',Decimal(120),'KRW',105.)
    plan = Plan(Route.CONTINUOUS, 99., 99.5)
    return target, order, session, plan


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.target, self.order, self.session, self.plan = _fixtures()

    def run_prepare(self, **kw):
        args = dict(order=self.order, session=self.session, plan=self.plan,
                    target=self.target, subject='subject', session_id='session', now=106.)
        args.update(kw)
        order, session, plan = args.pop('order'), args.pop('session'), args.pop('plan')
        return prepare(order, session, plan, **args)

    def test_expectation_is_a_checklist_not_an_arrival(self):
        handoff = self.run_prepare()
        self.assertEqual(handoff.route, Route.CONTINUOUS)
        self.assertEqual(handoff.expectation.provider,'npay')
        self.assertEqual(handoff.expectation.order_reference,'FAKEREF')
        self.assertEqual(handoff.expectation.reserve_id,'FAKE-RESERVE')
        self.assertEqual(handoff.expectation.amount, Decimal(120))
        self.assertEqual((handoff.expectation.origin, handoff.expectation.destination),
                         ('ICN','CDG'))
        # 인계가 됐다거나 창에 도착했다고 말하지 않는다
        self.assertFalse(handoff.screen_state_verified)
        self.assertFalse(handoff.payment_window_reached)
        self.assertTrue(handoff.offline_only)

    def test_deferred_route_never_promises_handoff(self):
        with self.assertRaises(ValueError):
            self.run_prepare(plan=Plan(Route.DEFERRED, 99., 99.5))

    def test_resume_route_is_allowed(self):
        handoff = self.run_prepare(plan=Plan(Route.RESUME, 99., 99.5))
        self.assertEqual(handoff.route, Route.RESUME)

    def test_route_must_be_fixed_before_the_run_starts(self):
        # 실행이 시작된 뒤 고른 경로는 주문 전이라도 실행 중 변경이다
        for fixed_at in (99.6, 100.5, 102., 103.1, 200.):
            with self.assertRaises(ValueError):
                self.run_prepare(plan=Plan(Route.CONTINUOUS, fixed_at, 99.5))

    def test_route_chosen_after_search_but_before_order_is_refused(self):
        # 검토 지적: 조회·운임(100~101)이 지난 102에 고정하는 것을 막는다
        with self.assertRaises(ValueError):
            self.run_prepare(plan=Plan(Route.CONTINUOUS, 102., 99.5))

    def test_plan_from_another_run_is_refused(self):
        # 이 실행의 첫 단계(100.)보다 늦게 시작한 실행의 계획이면 다른 실행 것이다
        for run_started in (100.5, 103., 500.):
            with self.assertRaises(ValueError):
                self.run_prepare(plan=Plan(Route.CONTINUOUS, run_started - 1., run_started))

    def test_plan_times_must_be_finite(self):
        for plan in (Plan(Route.CONTINUOUS, float('nan'), 99.5),
                     Plan(Route.CONTINUOUS, 99., float('inf')),
                     Plan(Route.CONTINUOUS, 99., '99.5')):
            with self.assertRaises(ValueError):self.run_prepare(plan=plan)

    def test_session_must_belong_to_this_order(self):
        with self.assertRaises(ValueError):
            self.run_prepare(session=replace(self.session, order_reference='OTHER'))

    def test_expired_session_is_refused(self):
        with self.assertRaises(ValueError):self.run_prepare(now=10006.)
        with self.assertRaises(ValueError):
            self.run_prepare(plan=Plan(Route.CONTINUOUS, 99., 99.5, max_session_age=0.5))

    def test_amount_and_currency_must_agree_with_the_quote(self):
        with self.assertRaises(ValueError):
            self.run_prepare(session=replace(self.session, amount=Decimal(121)))
        with self.assertRaises(ValueError):
            self.run_prepare(session=replace(self.session, currency='USD'))

    def test_arrival_direction_is_out_of_scope(self):
        target, order, session, plan = _fixtures(
            target=Target('2027-09-09','CDG','ICN','KEBONUSPR','KE','902'))
        with self.assertRaises(ValueError):
            prepare(order, session, plan, target=target, subject='subject',
                    session_id='session', now=106.)

    def test_context_must_match(self):
        for kw in ({'subject':'other'}, {'session_id':'other'}, {'subject':''},
                   {'target':Target('2027-09-09','ICN','LHR','KEBONUSPR','KE','901')}):
            with self.assertRaises(ValueError):self.run_prepare(**kw)

    def test_time_must_be_ordered_and_finite(self):
        for kw in ({'now':104.9}, {'now':float('nan')}, {'now':'106'},
                   {'session':replace(self.session, received=103.5)},
                   {'session':replace(self.session, received=float('inf'))}):
            with self.assertRaises(ValueError):self.run_prepare(**kw)

    def test_missing_references_are_refused(self):
        for kw in ({'session':replace(self.session, reserve_id='')},
                   {'session':replace(self.session, reserve_id='   ')}):
            with self.assertRaises(ValueError):self.run_prepare(**kw)

    def test_requires_real_objects(self):
        for kw in ({'order':'order'}, {'session':'session'}, {'plan':'plan'},
                   {'target':'target'}, {'plan':None}):
            with self.assertRaises(ValueError):self.run_prepare(**kw)


class CompareTests(unittest.TestCase):
    def setUp(self):
        self.target, self.order, self.session, self.plan = _fixtures()
        self.handoff = prepare(self.order, self.session, self.plan, target=self.target,
                               subject='subject', session_id='session', now=106.)
        self.observed = {'provider':'npay','order_reference':'FAKEREF',
                         'reserve_id':'FAKE-RESERVE','currency':'KRW','amount':'120',
                         'origin':'ICN','destination':'CDG','carrier':'KE',
                         'flight':'901','date':'2027-09-09','family':'KEBONUSPR'}

    def test_matching_observation_reports_no_difference(self):
        result = compare(self.handoff, self.observed)
        self.assertEqual(result.fields, ())
        self.assertTrue(result.compared)

    def test_amount_compares_by_value_not_text(self):
        for value in ('120', 120, 120.0, Decimal(120), '120.00'):
            self.assertEqual(compare(self.handoff, {**self.observed,'amount':value}).fields, ())
        for value in ('121', 0, None, True, 'abc', [120]):
            self.assertIn('amount',
                          compare(self.handoff, {**self.observed,'amount':value}).fields)

    def test_each_wrong_field_is_reported(self):
        for name, value in (('provider','card'),('order_reference','OTHER'),
                            ('reserve_id','OTHER'),('currency','USD'),('origin','GMP'),
                            ('destination','LHR'),('carrier','OZ'),('flight','902'),
                            ('date','2027-09-08'),('family','KEBONUSEY')):
            self.assertEqual(compare(self.handoff, {**self.observed,name:value}).fields,
                             (name,), name)

    def test_missing_fields_count_as_differences(self):
        result = compare(self.handoff, {})
        self.assertIn('order_reference', result.fields)
        self.assertIn('amount', result.fields)
        self.assertEqual(len(result.fields), 11)

    def test_bad_input_is_not_a_silent_match(self):
        for observed in (None,'{}',[],7):
            result = compare(self.handoff, observed)
            self.assertFalse(result.compared)
            self.assertEqual(result.fields, ('missing-observation',))
        result = compare('handoff', self.observed)
        self.assertFalse(result.compared)

    def test_clean_comparison_is_not_an_arrival_claim(self):
        # 일치해도 도착·승인으로 바뀌지 않는다. 그 판정은 운영 코드와 사용자 몫이다
        self.assertEqual(compare(self.handoff, self.observed).fields, ())
        self.assertFalse(self.handoff.payment_window_reached)
        self.assertFalse(self.handoff.screen_state_verified)

    def test_fake_consumer_drives_a1_state(self):
        # 대조 결과를 A1에 연결하는 것은 호출자 책임이다. 이 모듈은 상태를 바꾸지 않는다
        def run(observed):
            flow = OrderFlow()
            flow.prepare(Checks(True,True,True,True,True,True))
            flow.advance(Event.RECORD_INTENT)
            flow.advance(Event.BEGIN_SEND)
            flow.advance(Event.CONFIRM_ORDER)
            flow.advance(Event.BEGIN_HANDOFF)
            if compare(self.handoff, observed).fields:
                return flow.advance(Event.HANDOFF_FAILED)
            return flow.state
        self.assertEqual(run(self.observed), State.HANDOFF)
        self.assertEqual(run({**self.observed,'order_reference':'OTHER'}),
                         State.ORDER_CONFIRMED)


BAGGAGE = '/api/et/ibeSupport/baggagePolicies'
FARE_PATH = '/api/ap/booking/avail/fareInformation'


def _req(path, at, reference=None, *, readable=True, blocked=False, origin=SITE_ORIGIN,
         target=True, document=1):
    return dict(path=path, origin=origin, at=at, reference=reference,
                readable=readable, blocked=blocked, target=target,
                document=document if target else -1)


class ScreenOrderTests(unittest.TestCase):
    """D1 화면 주문 판정. 값은 합성이며 참조 출처 계약은 수집 f66cf6b8 의 정상 UI 2회뿐이다."""

    def judge(self, requests, reference='NEWREF', **kw):
        args = dict(ordered_at=100., started=101., now=110.)
        args.update(kw)
        return judge_screen_order(reference, requests=requests, **args)

    def test_same_reference_passes_but_claims_nothing_more(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF')])
        self.assertEqual(got.state, 'same-order-reference')
        self.assertTrue(got.same_reference)
        self.assertFalse(got.screen_state_verified)
        self.assertFalse(got.payment_window_reached)
        self.assertFalse(got.extra_order_possible)

    def test_previous_order_is_refused_even_if_everything_else_matches(self):
        # 9/13 08:17: 캡처 주문과 API 주문이 같은 날짜·35,000 마일이었다. 참조만 다르다.
        got = self.judge([_req(BAGGAGE, 102., 'OLDREF')])
        self.assertEqual(got.state, 'other-order')
        self.assertFalse(got.same_reference)

    def test_any_mismatch_refuses_even_with_a_match(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF'), _req(BAGGAGE, 103., 'OLDREF')])
        self.assertEqual(got.state, 'other-order')
        self.assertEqual((got.references_seen, got.references_matched), (2, 1))

    def test_no_reference_is_not_success(self):
        # 날짜·마일리지 문구만 보이고 참조 요청이 없으면 어느 주문인지 모른다.
        self.assertEqual(self.judge([]).state, 'reference-unobserved')

    def test_reference_from_another_origin_does_not_establish(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF', origin='https://evil.example')])
        self.assertEqual(got.state, 'reference-unobserved')

    def test_reference_from_another_tab_neither_establishes_nor_refuses(self):
        # 다른 탭이 새 참조를 보내도 대상 게이트가 무엇을 띄웠는지는 모른다(검토 d2574cfe P1).
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF', target=False)])
        self.assertEqual(got.state, 'reference-unobserved')
        self.assertEqual(got.other_page_references, 1)
        got = self.judge([_req(BAGGAGE, 102., 'OLDREF', target=False),
                          _req(BAGGAGE, 103., 'NEWREF')])
        self.assertEqual(got.state, 'same-order-reference')

    def test_reference_before_the_target_navigates_does_not_count(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF', document=0)])
        self.assertEqual(got.state, 'reference-unobserved')

    def test_order_request_from_another_tab_still_fails(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF'),
                          _req(ORDER_PATH, 103., blocked=True, target=False)])
        self.assertEqual(got.state, 'new-order-blocked')

    def test_unreadable_reference_is_refused(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF'),
                          _req(BAGGAGE, 103., None, readable=False)])
        self.assertEqual(got.state, 'reference-unreadable')

    def test_order_request_during_handoff_fails_even_when_blocked(self):
        blocked = self.judge([_req(BAGGAGE, 102., 'NEWREF'), _req(ORDER_PATH, 102.5, blocked=True)])
        self.assertEqual(blocked.state, 'new-order-blocked')
        self.assertFalse(blocked.same_reference)
        self.assertFalse(blocked.extra_order_possible)
        sent = self.judge([_req(BAGGAGE, 102., 'NEWREF'), _req(ORDER_PATH, 102.5)])
        self.assertEqual(sent.state, 'new-order-requested')
        self.assertTrue(sent.extra_order_possible)

    def test_selection_restart_during_handoff_fails(self):
        got = self.judge([_req(FARE_PATH, 102.), _req(BAGGAGE, 103., 'NEWREF')])
        self.assertEqual(got.state, 'selection-request')

    def test_requests_before_the_handoff_are_ignored(self):
        # 이전 화면이 이전 주문 참조를 보냈어도 인계 시작 전 기록은 섞지 않는다.
        got = self.judge([_req(ORDER_PATH, 99.), _req(BAGGAGE, 100.5, 'OLDREF'),
                          _req(BAGGAGE, 102., 'NEWREF')])
        self.assertEqual(got.state, 'same-order-reference')
        self.assertEqual(got.stale_ignored, 2)

    def test_watch_started_before_the_order_is_refused(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF')], started=99.)
        self.assertEqual(got.state, 'handoff-before-order')

    def test_bad_inputs_are_refused(self):
        self.assertEqual(self.judge([], reference='').state, 'missing-reference')
        self.assertEqual(self.judge([], reference=None).state, 'missing-reference')
        self.assertEqual(self.judge([], now=100.5).state, 'invalid-time')
        self.assertEqual(self.judge([], started=float('nan')).state, 'invalid-time')
        self.assertEqual(self.judge([_req(BAGGAGE, 111., 'NEWREF')]).state, 'invalid-time')
        for bad in ('x', None, [{'path': BAGGAGE}], [_req(BAGGAGE, True, 'NEWREF')],
                    [{**_req(BAGGAGE, 102.), 'blocked': 'no'}],
                    [{**_req(BAGGAGE, 102.), 'document': 1.0}]):
            self.assertEqual(self.judge(bad).state, 'invalid-observation')

    def test_verdict_has_no_raw_reference(self):
        got = self.judge([_req(BAGGAGE, 102., 'NEWREF')])
        self.assertNotIn('NEWREF', repr(got))
        self.assertIs(type(got), ScreenOrder)


class ReferenceInTests(unittest.TestCase):
    """관측 계약: baggagePolicies GET 쿼리 orderId(수집 f66cf6b8 행 20·64)."""
    URL = SITE_ORIGIN + BAGGAGE

    def test_reads_only_the_observed_query_parameter(self):
        self.assertEqual(reference_in('GET', self.URL + '?orderId=R1&x=1'), ('R1', True))
        self.assertEqual(reference_in('POST', SITE_ORIGIN + FARE_PATH), (None, True))

    def test_other_shapes_are_unreadable(self):
        for method, query in (('POST', '?orderId=R1'), ('GET', ''), ('GET', '?orderId='),
                              ('GET', '?orderId=%20'), ('GET', '?orderId=R1&orderId=R2'),
                              ('GET', '?orderid=R1')):
            self.assertEqual(reference_in(method, self.URL + query), (None, False), query)
        self.assertEqual(reference_in('GET', None), (None, False))


if __name__ == '__main__':unittest.main()
