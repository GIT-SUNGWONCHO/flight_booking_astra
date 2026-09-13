"""A4b 요청 구성과 A4c 응답 판정의 합성 시험. 실제 전송 없음."""
from dataclasses import replace
from decimal import Decimal
import json
import unittest

from availability import Target
from fare import Quote
from eligibility import Evidence
from order_flow import Checks, Event, OrderFlow, State
from travellers import PATH, prepare_draft, build_request, judge


class TravellersTests(unittest.TestCase):
    def setUp(self):
        self.target=Target('2027-09-09','ICN','CDG','KEBONUSPR','KE','901')
        self.quote=Quote(self.target,'fare','avail','session','fake-ticket',
                         Decimal(100),Decimal(120),Decimal(62500),101.,100.)
        self.evidence=Evidence(self.quote,'subject',102.,105.,True,True,62500)
        self.template={'travellerInfoList':[{'fixturePerson':'synthetic'}],
                       'contactList':[{'fixtureContact':'synthetic'}],'preferLanguage':'ko'}
        self.draft=prepare_draft('subject','session',self.template,
                                identity_checked=True,contact_checked=True)

    def build(self, **kw):
        args=dict(quote=self.quote,evidence=self.evidence,draft=self.draft,target=self.target,
            session='session',subject='subject',fare_generation='fare',
            availability_generation='avail',now=103.)
        args.update(kw)
        return build_request(**args)

    def test_inert_request_and_exact_snapshot(self):
        request=self.build()
        def fake_consumer(req):return req.path, json.loads(req.body_json)
        path, body=fake_consumer(request)
        self.assertEqual(path,PATH)
        self.assertEqual(body,self.template)
        self.assertTrue(request.offline_only)
        self.assertFalse(request.wire_contract_verified)
        self.assertNotIn('pageTicket',body)
        self.assertNotIn('flightId',body)
        self.assertNotIn('synthetic',repr(request))
        self.assertNotIn('fake-ticket',repr(request))
        self.template['contactList'][0]['fixtureContact']='changed'
        self.assertEqual(json.loads(self.build().body_json),body)

    def test_revalidates_instead_of_trusting_previous_ready(self):
        self.build()
        for kw in ({'now':105.},{'fare_generation':'new'}, {'availability_generation':'new'},
                   {'session':'new'}, {'subject':'new'},
                   {'evidence':replace(self.evidence,available_mileage=62499)},
                   {'evidence':replace(self.evidence,member_eligible=None)},
                   {'evidence':replace(self.evidence,session_valid=False)}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):self.build(**kw)

    def test_changed_quote_and_target(self):
        for kw in ({'quote':replace(self.quote,page_ticket='new')},
                   {'target':replace(self.target,date='2027-09-10')}):
            with self.assertRaises(ValueError):self.build(**kw)

    def test_draft_binding_and_checks(self):
        for changes in ({'subject':'other'},{'session':'other'},{'identity_checked':None},
                        {'contact_checked':False},{'identity_checked':1}):
            with self.assertRaises(ValueError):self.build(draft=replace(self.draft,**changes))

    def test_rejects_missing_multiple_and_unknown_top_level_fields(self):
        for template in ({}, {**self.template,'pageTicket':'invented'},
                         {**self.template,'contactList':[]},
                         {**self.template,'travellerInfoList':[{},{}]},
                         {**self.template,'preferLanguage':''},
                         {**self.template,'contactList':[{'a':1},{}]},
                         {**self.template,'contactList':[{'a':1},{'b':1},{'c':1}]}):
            with self.assertRaises(ValueError):prepare_draft('subject','session',template)

    def test_observed_two_contact_items_are_accepted(self):
        # 9/12 실측 inputTravellers 요청 2회 모두 contactList 2항목(각 필드 값은 합성).
        template = {**self.template, 'contactList': [{'type': 'a'}, {'type': 'b'}]}
        draft = prepare_draft('subject', 'session', template)
        self.assertEqual(len(__import__('json').loads(draft.body_json)['contactList']), 2)

    def test_direct_draft_corruption_is_checked(self):
        for body in ('{broken','[]','{}'):
            with self.assertRaises(ValueError):self.build(draft=replace(self.draft,body_json=body))

    def test_default_flags_do_not_pass(self):
        draft=prepare_draft('subject','session',self.template)
        with self.assertRaises(ValueError):self.build(draft=draft)



_DEFAULT = object()


class ResponseTests(unittest.TestCase):
    """A4c 합성 응답 판정. 실제 계약이 아니며 전송하지 않는다."""

    def setUp(self):
        self.target=Target('2027-09-09','ICN','CDG','KEBONUSPR','KE','901')
        self.quote=Quote(self.target,'fare','avail','session','fake-ticket',
                         Decimal(100),Decimal(120),Decimal(62500),101.,100.)
        self.evidence=Evidence(self.quote,'subject',102.,105.,True,True,62500)
        template={'travellerInfoList':[{'fixturePerson':'synthetic'}],
                  'contactList':[{'fixtureContact':'synthetic'}],'preferLanguage':'ko'}
        draft=prepare_draft('subject','session',template,
                            identity_checked=True,contact_checked=True)
        self.request=build_request(quote=self.quote,evidence=self.evidence,draft=draft,
            target=self.target,session='session',subject='subject',fare_generation='fare',
            availability_generation='avail',now=103.)
        self.leg={'departureAirport':'ICN','arrivalAirport':'CDG','flightNumber':'901',
                  'operationCarrierCode':'KE','fareFamily':'KEBONUSPR',
                  'departureDateTime':'20270909103000','status':'HK'}

    def payload(self, **kw):
        body={'boundList':[{'segmentList':[dict(self.leg)]}],'pnr':'FAKEREF'}
        body.update(kw)
        return body

    def call(self, status=200, payload=_DEFAULT, **kw):
        # None 도 시험 대상 응답이므로 별도 표지를 쓴다
        args=dict(quote=self.quote,target=self.target,session='session',
                  subject='subject',now=104.)
        args.update(kw)
        return judge(self.request, status,
                     self.payload() if payload is _DEFAULT else payload, **args)

    def test_recorded_order_never_claims_seat_hold(self):
        out=self.call()
        self.assertEqual(out.state,'order-recorded')
        self.assertFalse(out.seat_hold_verified)
        self.assertEqual(out.order.reference,'FAKEREF')
        self.assertEqual(out.order.segment_status,'HK')
        self.assertFalse(out.order.amounts_matched)

    def test_unobserved_status_is_dropped(self):
        for value in ('RR','','hk',None,7,True):
            out=self.call(payload=self.payload(
                boundList=[{'segmentList':[{**self.leg,'status':value}]}]))
            self.assertEqual(out.state,'order-recorded')
            self.assertIsNone(out.order.segment_status)

    def test_unreadable_response_is_unknown_not_failure(self):
        # 200 인데 못 읽으면 주문이 생겼을 수 있다. 실패로 단정하지 않는다.
        for payload in ('{broken','', b'{broken',[],7,None):
            out=self.call(payload=payload)
            self.assertEqual(out.state,'order-unknown')
            self.assertTrue(out.order_possible)
            self.assertIsNone(out.order)

    def test_missing_status_is_unknown(self):
        for status in (None,'200',200.0,True):
            out=self.call(status=status)
            self.assertEqual(out.state,'order-unknown')
            self.assertTrue(out.order_possible)

    def test_http_error_keeps_order_possible(self):
        for status in (500,404,302):
            out=self.call(status=status)
            self.assertEqual(out.state,'http-error')
            self.assertTrue(out.order_possible)

    def test_business_error_on_http_200(self):
        # 이 응답들은 pnr·정상 여정을 함께 갖는다. 오류 표시를 미생성 증거로 쓰지 않는다.
        for key in ('error','errorCode','errorMessage','resultCode','responseCode'):
            out=self.call(payload=self.payload(**{key:'X'}))
            self.assertEqual(out.state,'business-error',key)
            self.assertTrue(out.order_possible,key)
            self.assertIsNone(out.order)
        for flag in ('success','isSuccess'):
            out=self.call(payload=self.payload(**{flag:False}))
            self.assertEqual(out.state,'business-error',flag)
            self.assertTrue(out.order_possible,flag)

    def test_itinerary_and_grade_mismatch(self):
        for key,value in (('departureAirport','GMP'),('arrivalAirport','LHR'),
                          ('flightNumber','902'),('operationCarrierCode','OZ'),
                          ('fareFamily','KEBONUSEY')):
            out=self.call(payload=self.payload(
                boundList=[{'segmentList':[{**self.leg,key:value}]}]))
            self.assertEqual(out.state,'target-mismatch',key)
            self.assertTrue(out.order_possible)

    def test_broken_itinerary_shape(self):
        for bounds in (None,[],[{},{}],[{'segmentList':[]}],[{'segmentList':[{},{}]}],'x'):
            self.assertEqual(self.call(payload=self.payload(boundList=bounds)).state,
                             'invalid-itinerary')

    def test_date_checks(self):
        for value,state in (('20270908103000','target-mismatch'),('2027090910300','invalid-date'),
                            ('notadatetime14','invalid-date'),(None,'invalid-date')):
            out=self.call(payload=self.payload(
                boundList=[{'segmentList':[{**self.leg,'departureDateTime':value}]}]))
            self.assertEqual(out.state,state,value)

    def test_amounts_checked_only_when_present(self):
        fare={'currency':'KRW','amount':100,'totalAmount':120,'mileage':62500}
        out=self.call(payload=self.payload(pnrFareInfo=fare))
        self.assertEqual(out.state,'order-recorded')
        self.assertTrue(out.order.amounts_matched)
        for key in ('amount','totalAmount','mileage'):
            out=self.call(payload=self.payload(pnrFareInfo={**fare,key:999}))
            self.assertEqual(out.state,'amount-mismatch',key)
        self.assertEqual(self.call(payload=self.payload(
            pnrFareInfo={**fare,'currency':'USD'})).state,'currency-mismatch')
        self.assertEqual(self.call(payload=self.payload(
            pnrFareInfo={**fare,'amount':'x'})).state,'invalid-amounts')

    def test_missing_reference_is_not_a_clean_failure(self):
        for value in (None,'','   ',7,True,['A']):
            out=self.call(payload=self.payload(pnr=value))
            self.assertEqual(out.state,'missing-reference')
            self.assertTrue(out.order_possible)

    def test_context_must_match_request(self):
        self.assertEqual(self.call(session='other').state,'context-mismatch')
        self.assertEqual(self.call(subject='other').state,'context-mismatch')
        self.assertEqual(self.call(subject='').state,'context-mismatch')
        other=Quote(self.target,'other-fare','avail','session','fake-ticket',
                    Decimal(100),Decimal(120),Decimal(62500),101.,100.)
        self.assertEqual(self.call(quote=other).state,'context-mismatch')
        self.assertEqual(judge('not-a-request',200,self.payload(),quote=self.quote,
            target=self.target,session='session',subject='subject',now=104.).state,
            'missing-context')

    def test_time_must_not_precede_request(self):
        for now in (102.9,float('nan'),float('inf'),'104',None):
            self.assertEqual(self.call(now=now).state,'invalid-time')

    def _every_path(self):
        return [self.call(),self.call(status=500),self.call(status=None),
                self.call(status='200'),self.call(payload='{broken'),
                self.call(payload=self.payload(error='X')),
                self.call(payload=self.payload(isSuccess=False)),
                self.call(payload=self.payload(pnr=None)),
                self.call(payload=self.payload(boundList=None)),
                self.call(payload=self.payload(
                    boundList=[{'segmentList':[{**self.leg,'flightNumber':'902'}]}])),
                self.call(payload=self.payload(
                    pnrFareInfo={'currency':'KRW','amount':1,'totalAmount':2,'mileage':3})),
                self.call(session='other'),self.call(subject='other'),
                self.call(now=102.9),
                judge('not-a-request',200,self.payload(),quote=self.quote,target=self.target,
                      session='session',subject='subject',now=104.)]

    def test_no_path_claims_seat_hold(self):
        self.assertTrue(all(out.seat_hold_verified is False for out in self._every_path()))

    def test_no_path_rules_out_an_order(self):
        # 오프라인에서 주문 미생성을 입증할 수 없다. 어떤 경로도 재전송을 허가하지 않는다.
        for out in self._every_path():
            self.assertTrue(out.order_possible,out.state)

    def test_fake_consumer_drives_a1_state(self):
        # 판정 결과를 A1에 연결하는 것은 호출자 책임임을 보인다. 모듈은 상태를 바꾸지 않는다.
        def run(outcome):
            flow=OrderFlow()
            flow.prepare(Checks(True,True,True,True,True,True))
            flow.advance(Event.RECORD_INTENT)
            flow.advance(Event.BEGIN_SEND)
            if outcome.state=='order-recorded':
                return flow.advance(Event.CONFIRM_ORDER)
            return flow.advance(Event.INTERRUPT)
        self.assertEqual(run(self.call()),State.ORDER_CONFIRMED)
        self.assertEqual(run(self.call(status=None)),State.ORDER_UNKNOWN)
        self.assertEqual(run(self.call(payload=self.payload(error='X'))),State.ORDER_UNKNOWN)

if __name__ == '__main__':unittest.main()
