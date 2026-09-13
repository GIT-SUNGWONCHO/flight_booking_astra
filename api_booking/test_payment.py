"""A5 결제수단·Npay 세션의 합성 시험. 실제 전송·결제창 진입 없음."""
from dataclasses import replace
from decimal import Decimal
import json
import unittest

from availability import Target
from fare import Quote
from travellers import Order
from payment import (MILEAGE_MODE, SESSION_PATH, TYPES_PATH, Session, SessionRequest,
                     build_session_request, build_types_request, judge_session, judge_types)

_DEFAULT = object()


def _order(**kw):
    target = kw.pop('target', Target('2027-09-09','ICN','CDG','KEBONUSPR','KE','901'))
    quote = kw.pop('quote', Quote(target,'fare','avail','session','fake-ticket',
                                  Decimal(100),Decimal(120),Decimal(62500),101.,100.))
    args = dict(quote=quote, subject='subject', session='session', reference='FAKEREF',
                request_created=103., received=104., segment_status='HK', amounts_matched=False)
    args.update(kw)
    return Order(**args), target


class TypesRequestTests(unittest.TestCase):
    def setUp(self):
        self.order, self.target = _order()

    def build(self, **kw):
        args = dict(target=self.target, session='session', subject='subject', now=105.)
        args.update(kw)
        return build_types_request(self.order, **args)

    def test_inputs_are_currency_and_mode_only(self):
        request = self.build()
        self.assertEqual((request.path, request.currency, request.mode),
                         (TYPES_PATH, 'KRW', MILEAGE_MODE))
        self.assertTrue(request.offline_only)
        self.assertFalse(request.wire_contract_verified)

    def test_context_must_match_the_order(self):
        for kw in ({'session':'other'}, {'subject':'other'}, {'subject':''},
                   {'target':Target('2027-09-09','ICN','LHR','KEBONUSPR','KE','901')}):
            with self.assertRaises(ValueError):self.build(**kw)

    def test_rejects_stale_or_impossible_times(self):
        for kw in ({'now':103.9}, {'now':float('nan')}, {'now':'105'},
                   {'now':10005.}, {'max_age':0}):
            with self.assertRaises(ValueError):self.build(**kw)

    def test_requires_a_real_order(self):
        for bad in ('order', None, self.order.quote):
            with self.assertRaises(ValueError):
                build_types_request(bad, target=self.target, session='session',
                                    subject='subject', now=105.)


class TypesResponseTests(unittest.TestCase):
    def setUp(self):
        self.order, self.target = _order()
        self.request = build_types_request(self.order, target=self.target, session='session',
                                           subject='subject', now=105.)

    def call(self, status=200, payload=_DEFAULT, **kw):
        args = dict(order=self.order, now=106.)
        args.update(kw)
        return judge_types(self.request, status,
                           {'availablePaymentTypeList':[{'paymentType':'NAVERPAY'}]}
                           if payload is _DEFAULT else payload, **args)

    def test_never_claims_the_method_is_usable(self):
        out = self.call()
        self.assertEqual(out.state,'types-unverified')
        self.assertTrue(out.payload_seen)
        self.assertFalse(out.applicability_verified)

    def test_transport_and_business_failures(self):
        self.assertEqual(self.call(status=None).state,'no-response')
        self.assertEqual(self.call(status=500).state,'http-error')
        self.assertEqual(self.call(payload='{broken').state,'unreadable-response')
        self.assertEqual(self.call(payload=[]).state,'unreadable-response')
        for key in ('error','errorCode','responseMessage'):
            self.assertEqual(self.call(payload={key:'X'}).state,'business-error')
        self.assertEqual(self.call(payload={'isSuccess':False}).state,'business-error')

    def test_context_and_time(self):
        other, _ = _order(reference='OTHERREF')
        self.assertEqual(self.call(order=other).state,'context-mismatch')
        self.assertEqual(self.call(now=104.9).state,'invalid-time')
        self.assertEqual(judge_types('x',200,{},order=self.order,now=106.).state,
                         'missing-context')

    def test_expired_request_is_not_accepted(self):
        # 구성 단계에서 막힐 시각의 응답을 판정에서 통과시키면 안 된다
        self.assertEqual(self.call(now=10005.).state,'expired-context')

    def test_applicability_never_verified_on_any_path(self):
        for out in (self.call(), self.call(status=500), self.call(status=None),
                    self.call(payload='{broken'), self.call(payload={'error':'X'}),
                    self.call(order=_order(reference='OTHER')[0])):
            self.assertFalse(out.applicability_verified, out.state)


class SessionRequestTests(unittest.TestCase):
    def setUp(self):
        self.order, self.target = _order()
        self.template = {'reservationRecLoc':'','currency':'','paymentAmount':'',
                         'officeId':'SYNTHETIC','callbackUrl':'https://example.invalid/cb',
                         'departureDate':'20270909','arrivalDate':'20270910'}

    def build(self, **kw):
        template = kw.pop('template', self.template)
        args = dict(target=self.target, session='session', subject='subject', now=105.)
        args.update(kw)
        return build_session_request(self.order, template, **args)

    def test_only_owned_values_are_injected(self):
        request = self.build()
        body = json.loads(request.body_json)
        self.assertEqual(body['reservationRecLoc'],'FAKEREF')
        self.assertEqual(body['currency'],'KRW')
        self.assertEqual(body['paymentAmount'],'120')
        # 나머지 필드는 호출자 값 그대로 둔다. 이름/값을 지어내지 않는다.
        for key in ('officeId','callbackUrl','departureDate','arrivalDate'):
            self.assertEqual(body[key], self.template[key])
        self.assertEqual(request.path, SESSION_PATH)
        self.assertTrue(request.offline_only)
        self.assertFalse(request.wire_contract_verified)

    def test_amount_comes_from_the_quote_not_the_caller(self):
        request = self.build(template={**self.template,'paymentAmount':'999999'})
        self.assertEqual(json.loads(request.body_json)['paymentAmount'],'120')
        self.assertEqual(request.amount, self.order.quote.total_amount)

    def test_template_must_declare_the_owned_keys(self):
        for key in ('reservationRecLoc','currency','paymentAmount'):
            broken = {k:v for k,v in self.template.items() if k != key}
            with self.assertRaises(ValueError):self.build(template=broken)
        for bad in ('{}', None, [], 7):
            with self.assertRaises(ValueError):self.build(template=bad)

    def test_arrival_direction_is_not_npay(self):
        order, target = _order(target=Target('2027-09-09','CDG','ICN','KEBONUSPR','KE','902'))
        with self.assertRaises(ValueError):
            build_session_request(order, self.template, target=target, session='session',
                                  subject='subject', now=105.)

    def test_does_not_build_from_a_stale_order(self):
        with self.assertRaises(ValueError):self.build(now=10005.)

    def test_template_that_cannot_serialize(self):
        with self.assertRaises(ValueError):
            self.build(template={**self.template,'officeId':float('nan')})
        with self.assertRaises(ValueError):
            self.build(template={**self.template,'officeId':object()})


class SessionResponseTests(unittest.TestCase):
    def setUp(self):
        self.order, self.target = _order()
        self.template = {'reservationRecLoc':'','currency':'','paymentAmount':'',
                         'officeId':'SYNTHETIC','callbackUrl':'https://example.invalid/cb'}
        self.request = build_session_request(self.order, self.template, target=self.target,
                                             session='session', subject='subject', now=105.)

    def call(self, status=200, payload=_DEFAULT, **kw):
        args = dict(order=self.order, now=106.)
        args.update(kw)
        return judge_session(self.request, status,
                             {'resultCode':'Success','reserveId':'FAKE-RESERVE'}
                             if payload is _DEFAULT else payload, **args)

    def test_ready_session_is_not_a_payment_window(self):
        out = self.call()
        self.assertEqual(out.state,'session-ready')
        self.assertEqual(out.session.reserve_id,'FAKE-RESERVE')
        self.assertEqual(out.session.order_reference,'FAKEREF')
        self.assertEqual(out.session.amount, Decimal(120))
        self.assertFalse(out.payment_window_reached)

    def test_only_success_result_code_passes(self):
        for value in ('success','SUCCESS','Failure','', None, True, 1):
            out = self.call(payload={'resultCode':value,'reserveId':'FAKE-RESERVE'})
            self.assertEqual(out.state,'session-rejected', repr(value))
        self.assertEqual(self.call(payload={'reserveId':'FAKE-RESERVE'}).state,
                         'session-rejected')

    def test_reserve_id_must_be_usable(self):
        for value in (None,'','   ',7,True,['A'],{'a':1}):
            out = self.call(payload={'resultCode':'Success','reserveId':value})
            self.assertEqual(out.state,'missing-reserve-id', repr(value))

    def test_unreadable_response_is_unknown(self):
        for payload in ('{broken','', b'{broken',[],7,None):
            self.assertEqual(self.call(payload=payload).state,'session-unknown')
        for status in (None,'200',200.0,True):
            self.assertEqual(self.call(status=status).state,'session-unknown')

    def test_http_and_business_errors(self):
        self.assertEqual(self.call(status=500).state,'http-error')
        for key in ('error','errorCode','errorMessage','responseCode'):
            self.assertEqual(self.call(payload={key:'X','resultCode':'Success',
                                                'reserveId':'R'}).state,'business-error')

    def test_context_and_time(self):
        other, _ = _order(reference='OTHERREF')
        self.assertEqual(self.call(order=other).state,'context-mismatch')
        self.assertEqual(self.call(now=104.9).state,'invalid-time')
        self.assertEqual(self.call(now=float('inf')).state,'invalid-time')
        self.assertEqual(judge_session('x',200,{},order=self.order,now=106.).state,
                         'missing-context')
        self.assertEqual(judge_session(replace(self.request,order_reference='X'),200,
            {'resultCode':'Success','reserveId':'R'},order=self.order,now=106.).state,
            'context-mismatch')

    def test_stale_success_response_cannot_be_replayed(self):
        # 검토 지적: 오래된 요청·성공 응답을 다시 넣어 새 세션처럼 만들지 못한다
        fresh = self.call()
        self.assertEqual(fresh.state,'session-ready')
        stale = self.call(now=10005.)
        self.assertEqual(stale.state,'expired-context')
        self.assertIsNone(stale.session)
        self.assertTrue(stale.session_possible)
        # 요청 자체도 그 시각에는 구성되지 않는다
        with self.assertRaises(ValueError):
            build_session_request(self.order, self.template, target=self.target,
                                  session='session', subject='subject', now=10005.)

    def test_rebuilt_order_is_not_the_same_context(self):
        # 같은 예약번호라도 다른 시각에 판정된 주문이면 그 세션 요청이 아니다
        other, _ = _order(received=104.5)
        self.assertEqual(self.call(order=other).state,'context-mismatch')

    def test_no_path_claims_a_payment_window(self):
        cases = [self.call(), self.call(status=500), self.call(status=None),
                 self.call(payload='{broken'), self.call(payload={'error':'X'}),
                 self.call(payload={'resultCode':'Failure'}),
                 self.call(payload={'resultCode':'Success','reserveId':''}),
                 self.call(order=_order(reference='OTHER')[0]), self.call(now=104.9),
                 self.call(now=10005.), self.call(order=_order(received=104.5)[0])]
        self.assertTrue(all(out.payment_window_reached is False for out in cases))
        self.assertTrue(all(out.session_possible for out in cases))


if __name__ == '__main__':unittest.main()
