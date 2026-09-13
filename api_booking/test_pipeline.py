"""D3 발사 판정 연결 시험. 전송·브라우저 없음.

픽스처의 필드 이름 구성은 수집 20260912-232135-f66cf6b8(허용목록 기반 구조)와 8/27 조회
fixture 를 따르고, 값은 전부 합성이다. 실제 사이트 응답 값의 증거가 아니다.
"""
from copy import deepcopy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline  # noqa: E402
from availability import Target  # noqa: E402

TARGET = Target('2027-09-09', 'ICN', 'CDG', 'KEBONUSPR', 'KE', '901')

# 캡처 본문. 조회 요청 currency 는 9/12 실측에서 KRW/USD 가 아니었다(값은 합성).
CAP_AWARD = json.dumps({'currency': 'observed-non-krw', 'o1': 1, 'o2': 2, 'o3': 3,
                        'segmentList': [{'departureDate': '20270907', 'departureAirport': 'ICN',
                                         'arrivalAirport': 'CDG'}],
                        'travelers': [{'t': 1}]})
CAP_FARE = json.dumps({'recommendList': [{'recommendId': 'OLD-R', 'flightId': 'OLD-F'}]})
CAP_ORDER = json.dumps({'travellerInfoList': [{'travellerId': 'T1', 'x': 1}],
                        'contactList': [{'c': 1}, {'c': 2}], 'preferLanguage': 'KO'})
HEADERS = {'Content-Type': 'application/json', 'ksessionId': 'FAKE-SESSION'}


def award_response(date='20270909112000', family='KEBONUSPR', seats='3', soldout=False,
                   arrival='CDG', flight='901'):
    return {'currency': 'KRW', 'upsellBoundAvailList': [{'boundId': 'B', 'availFlightList': [{
        'flightId': 'NEW-F', 'departureAirport': 'ICN', 'departureDate': date,
        'arrivalAirport': arrival, 'arrivalDate': '20270909180000',
        'flightInfoList': [{'flightNumber': flight, 'operationCarrierCode': 'KE',
                            'departureAirport': 'ICN', 'arrivalAirport': arrival,
                            'departureDateTime': date, 'arrivalDateTime': '20270909180000',
                            'codeShare': False}],
        'commercialFareFamilyList': [
            {'recommendId': 'NEW-EY', 'fareFamily': 'KEBONUSEY', 'seatCount': '9', 'soldout': False,
             'bookingClass': 'X', 'cabinClass': 'E', 'totalTax': '1', 'totalMileage': '35000'},
            {'recommendId': 'NEW-PR', 'fareFamily': family, 'seatCount': seats, 'soldout': soldout,
             'bookingClass': 'O', 'cabinClass': 'P', 'totalTax': '1', 'totalMileage': '62500'}]}]}]}


def fare_response(mileage='62500', family='KEBONUSPR', date='20270909112000'):
    return {'pageTicket': 'FAKE-TICKET', 'boundList': [{
        'departureAirport': 'ICN', 'arrivalAirport': 'CDG', 'departureDateTime': date,
        'arrivalDateTime': '20270909180000', 'segmentList': [{
            'flightNumber': '901', 'operationCarrierCode': 'KE', 'codeShare': False,
            'departureAirport': 'ICN', 'arrivalAirport': 'CDG', 'departureDateTime': date,
            'arrivalDateTime': '20270909180000', 'fareFamily': family, 'fareBasis': 'F',
            'bookingClass': 'O', 'cabinClass': 'P', 'status': 'x'}]}],
        'travellerFareInfoList': [{'travellerId': 'T1'}],
        'pnrFareInfo': {'totalAmount': '325500', 'amount': '0', 'mileage': mileage,
                        'currency': 'KRW'}}


def order_response(**kw):
    data = fare_response(**kw)
    data.pop('pageTicket')
    data['boundList'][0]['segmentList'][0]['status'] = 'HK'
    return {'pnr': 'FAKEPNR', 'officeId': 'O', **data}


def ok(body, status=200):
    return {'ok': True, 'status': status, 'body': body if isinstance(body, str) else json.dumps(body)}


class Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        self.now += 0.5
        return self.now


class PipelineTests(unittest.TestCase):
    def make(self, *, miles=100000, reached=True, clock=None, member_expires=None,
             balance_expires=None, reuse=True, capture_award=CAP_AWARD, capture_order=CAP_ORDER,
             family='KEBONUSEY'):
        clock = clock or Clock()
        start = clock.now
        # 캡처는 이미 열린 2027-09-07 일반석, 목표는 2027-09-09 프레스티지(9/13 운영과 같은 조합).
        member = pipeline.member_evidence_from_capture(
            capture_award, capture_order, observed=start, expires=member_expires or start + 3600,
            reached_order_request=reached, family=family, reuse_itinerary=reuse)
        balance = pipeline.BalanceEvidence(start, balance_expires or start + 3600, miles)
        return pipeline.Pipeline(TARGET, member=member, balance=balance, clock=clock)

    def run_until(self, pl, stop, *, award=None, fare=None, order_body=CAP_ORDER):
        body, why = pl.award_body(CAP_AWARD, HEADERS)
        self.assertIsNone(why)
        if pl.judge_award(award or ok(award_response())) != 'selected' or stop == 'award':
            return pl.state
        body, why = pl.fare_body(CAP_FARE, HEADERS)
        self.assertIsNone(why)
        if pl.judge_fare(fare or ok(fare_response())) != 'validated' or stop == 'fare':
            return pl.state
        return pl.prepare_order(order_body)

    def test_valid_chain_reaches_ready_and_records_the_order(self):
        pl = self.make()
        body, _ = pl.award_body(CAP_AWARD, HEADERS)
        sent = json.loads(body)
        self.assertEqual(sent['segmentList'][0], {'departureDate': '20270909',
                                                  'departureAirport': 'ICN', 'arrivalAirport': 'CDG'})
        self.assertEqual((sent['currency'], sent['o3']), ('observed-non-krw', 3))
        self.assertEqual(pl.judge_award(ok(award_response())), 'selected')
        fbody, _ = pl.fare_body(CAP_FARE, HEADERS)
        self.assertEqual(json.loads(fbody)['recommendList'][0],
                         {'recommendId': 'NEW-PR', 'flightId': 'NEW-F'})
        self.assertEqual(pl.judge_fare(ok(fare_response())), 'validated')
        self.assertEqual(pl.mileage_label(), '62,500')
        self.assertEqual(pl.prepare_order(CAP_ORDER), 'ready')
        outcome = pl.judge_order(ok(order_response()))
        self.assertEqual(outcome.state, 'order-recorded')
        self.assertEqual(outcome.order.reference, 'FAKEPNR')
        self.assertFalse(outcome.seat_hold_verified)
        for obj in (pl.award, pl.fare_request, pl.quote, pl.order_request, outcome):
            self.assertNotIn('FAKE-TICKET', repr(obj))
            self.assertNotIn('FAKEPNR', repr(obj))
            self.assertNotIn('FAKE-SESSION', repr(obj))

    def test_wrong_date_route_flight_or_family_never_reaches_ready(self):
        cases = {'date': award_response(date='20270910112000'),
                 'route': award_response(arrival='FCO'),
                 'flight': award_response(flight='905'),
                 'family': award_response(family='KEBONUSFC')}
        for name, data in cases.items():
            with self.subTest(name):
                pl = self.make()
                self.assertEqual(self.run_until(pl, None, award=ok(data)), 'no-target')
                self.assertIsNone(pl.order_request)
                self.assertEqual(pl.fare_body(CAP_FARE, HEADERS), (None, 'no-selection'))

    def test_sold_out_and_unverified_stock(self):
        for data, want in ((award_response(soldout=True), 'sold-out'),
                           (award_response(seats='0'), 'sold-out'),
                           (award_response(seats=None), 'unverified-stock')):
            pl = self.make()
            self.assertEqual(self.run_until(pl, None, award=ok(data)), want)

    def test_not_open_and_business_errors(self):
        pl = self.make()
        self.assertEqual(self.run_until(pl, None, award=ok({'code': 'ERT.10032'})), 'not-open')
        pl = self.make()
        self.assertEqual(self.run_until(pl, None, award=ok({'errorCode': 'E1'})), 'business-error')
        # 9/12 요청 39 형태: 운임 응답 HTTP 200 + code·status·message
        pl = self.make()
        self.assertEqual(self.run_until(pl, None, fare=ok({'code': 'E', 'status': 'x', 'message': 'm'})),
                         'business-error')

    def test_session_expiry_and_transport_failures(self):
        for result, want in ((ok('<html>login</html>', 401), 'session-expired'),
                             (ok('', 403), 'session-expired'),
                             ({'ok': False, 'error': 'TypeError: Failed to fetch'}, 'fetch-failed'),
                             ({'ok': False, 'error': 'origin-changed'}, 'fetch-failed'),
                             (ok('<html>login</html>'), 'invalid-json'),
                             (ok({}, 500), 'http-error')):
            with self.subTest(want=want):
                pl = self.make()
                self.assertEqual(self.run_until(pl, None, award=result), want)
                pl = self.make()
                self.assertEqual(self.run_until(pl, None, fare=result), want)

    def test_balance_member_and_expiry_gate_the_order(self):
        self.assertEqual(self.run_until(self.make(miles=62499), None), 'insufficient-mileage')
        self.assertEqual(self.run_until(self.make(miles=62500), None), 'ready')
        self.assertEqual(self.run_until(self.make(miles=None), None), 'mileage-unverified')
        self.assertEqual(self.run_until(self.make(reached=False), None), 'member-unverified')
        clock = Clock()
        self.assertEqual(self.run_until(self.make(clock=clock, balance_expires=clock.now + 1), None),
                         'expired-evidence')

    def test_member_evidence_is_bound_to_passenger_route_and_itinerary(self):
        # 검토 2331c183 P1: 준비 때 확인한 승객·노선이 아니면 쓰지 않고, 날짜·등급 재사용은 명시할 때만.
        other = json.dumps({**json.loads(CAP_ORDER), 'travellerInfoList': [{'travellerId': 'T2'}]})
        self.assertEqual(self.run_until(self.make(), None, order_body=other),
                         'member-evidence-other-passenger')
        fco = CAP_AWARD.replace('"CDG"', '"FCO"')
        self.assertEqual(self.run_until(self.make(capture_award=fco), None),
                         'member-evidence-other-route')
        self.assertEqual(self.run_until(self.make(reuse=False), None),
                         'member-evidence-other-itinerary')
        same = CAP_AWARD.replace('20270907', '20270909')
        self.assertEqual(self.run_until(self.make(reuse=False, capture_award=same,
                                                  family='KEBONUSPR'), None), 'ready')

    def test_unreadable_capture_gives_no_member_evidence(self):
        for award, order in (('{broken', CAP_ORDER), (CAP_AWARD, '{}'),
                             (CAP_AWARD, json.dumps({'travellerInfoList': [{'travellerId': ''}]}))):
            self.assertIsNone(pipeline.member_evidence_from_capture(
                award, order, observed=1., expires=2., reached_order_request=True, family=None))
        ev = pipeline.member_evidence_from_capture(CAP_AWARD, CAP_ORDER, observed=1., expires=2.,
                                                   reached_order_request=True, family=None)
        self.assertNotIn(ev.traveller_digest, repr(ev))
        self.assertNotIn('T1', repr(ev))

    def test_order_without_observed_segment_status_is_not_recorded(self):
        # 검토 2331c183 P2: 관측된 업무 상태 계약은 HK 뿐이다.
        for status in (None, 'XX'):
            pl = self.make()
            self.assertEqual(self.run_until(pl, None), 'ready')
            data = order_response()
            if status is None:
                del data['boundList'][0]['segmentList'][0]['status']
            else:
                data['boundList'][0]['segmentList'][0]['status'] = status
            outcome = pl.judge_order(ok(data))
            self.assertEqual(outcome.state, 'segment-status-unverified')
            self.assertTrue(outcome.order_possible)

    def test_new_fare_mileage_is_compared_not_the_captured_one(self):
        pl = self.make(miles=70000)
        self.assertEqual(self.run_until(pl, None, fare=ok(fare_response(mileage='80000'))),
                         'insufficient-mileage')

    def test_stale_award_response_is_refused(self):
        clock = Clock()
        pl = self.make(clock=clock)
        pl.award_body(CAP_AWARD, HEADERS)
        clock.now += pipeline.FRESH_MAX_AGE + 1
        self.assertEqual(pl.judge_award(ok(award_response())), 'stale-context')

    def test_fare_for_another_family_or_date_is_refused(self):
        self.assertEqual(self.run_until(self.make(), None, fare=ok(fare_response(family='KEBONUSEY'))),
                         'target-mismatch')
        self.assertEqual(self.run_until(self.make(), None,
                                        fare=ok(fare_response(date='20270910112000'))),
                         'target-mismatch')

    def test_unsupported_captured_order_body(self):
        body = json.dumps({'travellerInfoList': [{'travellerId': 'T1', 'x': 1}],
                           'contactList': [{'c': 1}] * 3, 'preferLanguage': 'KO'})
        self.assertEqual(self.run_until(self.make(), None, order_body=body), 'unsupported-draft-shape')
        self.assertEqual(self.run_until(self.make(), None, order_body='{broken'),
                         'captured-order-not-json')

    def test_order_response_judgement_keeps_order_possible(self):
        pl = self.make()
        self.assertEqual(self.run_until(pl, None), 'ready')
        bad = order_response()
        bad['boundList'][0]['segmentList'][0]['fareFamily'] = 'KEBONUSEY'
        for result, want in ((ok(bad), 'target-mismatch'), (ok({'code': 'E'}), 'business-error'),
                             ({'ok': False, 'error': 'x'}, 'order-unknown'), (ok({}, 500), 'http-error')):
            outcome = pl.judge_order(result)
            self.assertEqual(outcome.state, want)
            self.assertTrue(outcome.order_possible)

    def test_steps_out_of_order_are_refused(self):
        pl = self.make()
        self.assertEqual(pl.judge_award(ok(award_response())), 'award-not-built')
        pl = self.make()
        self.assertEqual(pl.prepare_order(CAP_ORDER), 'no-quote')
        self.assertEqual(pl.judge_order(ok(order_response())).state, 'not-ready')


if __name__ == '__main__':
    unittest.main()
