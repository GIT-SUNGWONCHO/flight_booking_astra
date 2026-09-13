"""합성 계약 시험. 기존 UI fixture(8/27)에 빠진 운항/공동운항/구간 시각 필드를 보강한다.

보강 필드 이름은 수집 20260912-232135-f66cf6b8 에서 관측된 이름이며 값은 합성이다.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from availability import PATH, Target, build_request, judge


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.target = Target('2027-08-21', 'ICN', 'FCO', 'KEBONUSEY', 'KE', '931')
        # 실제 캡처를 가장하지 않는 요청 fixture. travelers 내부 계약도 합성이다.
        self.template = {'currency': 'KRW', 'travelers': [{'ptc': 'ADT', 'count': 1}],
            'segmentList': [{'departureDate': '20270820', 'departureAirport': 'ICN',
                             'arrivalAirport': 'FCO'}], 'fixtureExtra': 'preserved'}
        self.request = build_request(self.target, self.template,
            {'Content-Type': 'application/json', 'fixture-session': 'fake'}, 'local-session', 100.)
        self.data = json.loads((Path(__file__).resolve().parents[1] /
            'test/fixture/api/awardAvailability.json').read_text())
        self.flight = self.data['upsellBoundAvailList'][0]['availFlightList'][0]
        self.flight['flightInfoList'][0].update(operationCarrierCode='KE', codeShare=False,
                                                departureDateTime='20270821132000')

    def result(self, data=None, **kw):
        args = dict(status=200, payload=self.data if data is None else data,
                    generation=self.request.generation, session='local-session', now=101.)
        args.update(kw)
        return judge(self.request, **args)

    def blocked(self, **kw):
        r = self.result(**kw)
        self.assertIsNone(r.selection)
        self.assertFalse(r.seat_hold_verified)

    def test_request_and_fake_transport(self):
        calls = []
        def fake(request):
            calls.append((request.path, request.method, request.credentials))
            return 200, deepcopy(self.data)
        status, payload = fake(self.request)
        r = self.result(status=status, payload=payload)
        self.assertEqual(calls, [(PATH, 'POST', 'include')])
        self.assertTrue(self.request.offline_only)
        self.assertEqual(r.state, 'selected')
        self.assertEqual((r.selection.recommend_id, r.selection.flight_id), ('0', '0'))
        self.assertEqual(r.selection.generation, self.request.generation)
        self.assertFalse(r.seat_hold_verified)
        self.assertEqual(self.request.body['segmentList'][0]['departureDate'], '20270821')
        self.assertEqual(self.template['segmentList'][0]['departureDate'], '20270820')
        self.template['travelers'].clear()
        self.assertEqual(len(self.request.body['travelers']), 1)
        self.assertNotIn('fake', repr(self.request))

    def test_wrong_target_dimensions(self):
        for key, wrong in [('departureDate','20270822132000'), ('departureDate','20270821139900'),
                           ('departureAirport','GMP'), ('arrivalAirport','CDG')]:
            with self.subTest(key=key, wrong=wrong):
                data = deepcopy(self.data)
                data['upsellBoundAvailList'][0]['availFlightList'][0][key] = wrong
                self.blocked(data=data)
        for key, wrong in [('carrierCode','AF'), ('operationCarrierCode','AF'),
                           ('flightNumber','932'), ('codeShare',True)]:
            data = deepcopy(self.data)
            data['upsellBoundAvailList'][0]['availFlightList'][0]['flightInfoList'][0][key] = wrong
            self.blocked(data=data)
        self.flight['commercialFareFamilyList'][0]['fareFamily'] = 'KEBONUSPR'
        self.blocked()

    def test_soldout_missing_stock_and_ids(self):
        for key, values in [('soldout',[True, None]), ('seatCount',['0', None, '-1', True]),
                            ('recommendId',[None, '', ' '])]:
            for value in values:
                data = deepcopy(self.data)
                data['upsellBoundAvailList'][0]['availFlightList'][0]['commercialFareFamilyList'][0][key] = value
                self.blocked(data=data)
        self.flight['flightId'] = None
        self.blocked()

    def test_http_json_business_and_unopened(self):
        for status in (401,403,429,500):
            self.blocked(status=status)
        for data in ('<html/>', '{}', '[]', {'code':'ERT.10032'}, {'errorCode':'unknown'}):
            self.blocked(data=data)
        for key, value in [('success',False), ('error',{'message':'failure'}), ('code','unrecognized')]:
            data = deepcopy(self.data); data[key] = value
            self.blocked(data=data)
        self.assertEqual(self.result(data={'code':'ERT.10032'}).state, 'not-open')

    def test_stale_session_generation_and_time(self):
        for kw in ({'session':'other'}, {'generation':'old'}, {'now':106.}, {'now':99.},
                   {'now':float('nan')}, {'max_age':0}):
            self.blocked(**kw)
        new = build_request(self.target, self.template, {}, 'local-session', 102.)
        r = judge(new, 200, self.data, self.request.generation, 'local-session', 103.)
        self.assertEqual(r.state, 'stale-context')

    def test_ambiguous_or_incomplete_data(self):
        self.data['upsellBoundAvailList'][0]['availFlightList'].append(deepcopy(self.flight))
        self.assertEqual(self.result().state, 'ambiguous-target')
        self.data['upsellBoundAvailList'][0]['availFlightList'].pop()
        del self.flight['flightInfoList'][0]['operationCarrierCode']
        self.blocked()

    def test_latest_selected_identifiers_not_old_or_other_class(self):
        self.flight['flightId'] = 'new-flight'
        self.flight['commercialFareFamilyList'][0]['recommendId'] = 'new-economy'
        r = self.result()
        self.assertEqual((r.selection.recommend_id, r.selection.flight_id),
                         ('new-economy','new-flight'))
        self.data['currency'] = 'USD'
        self.blocked()

    def test_observed_request_currency_is_not_required_to_be_krw(self):
        # 9/12 실측 요청 currency 는 KRW/USD 가 아닌 문자열이었다. 값은 보존한다.
        req = build_request(self.target, {**self.template, 'currency': 'observed-other'},
                            {}, 'local-session', 100.)
        self.assertEqual(req.body['currency'], 'observed-other')
        with self.assertRaises(ValueError):
            build_request(self.target, {**self.template, 'currency': None}, {}, 'session', 100.)

    def test_fields_outside_the_912_allowlist_are_checked_only_when_present(self):
        del self.flight['soldOut']
        del self.flight['flightInfoList'][0]['carrierCode']
        self.assertEqual(self.result().state, 'selected')
        self.flight['soldOut'] = None
        self.assertEqual(self.result().state, 'unverified-stock')

    def test_leg_date_is_required_and_must_agree_with_flight_date(self):
        del self.flight['departureDate']
        self.assertEqual(self.result().state, 'selected')
        self.flight['flightInfoList'][0]['departureDateTime'] = '20270822132000'
        self.blocked()
        self.flight['flightInfoList'][0]['departureDateTime'] = '20270821132000'
        self.flight['departureDate'] = '20270822132000'
        self.blocked()
        del self.flight['departureDate']
        del self.flight['flightInfoList'][0]['departureDateTime']
        self.blocked()

    def test_leg_airports_must_match_when_present(self):
        self.flight['flightInfoList'][0].update(departureAirport='ICN', arrivalAirport='FCO')
        self.assertEqual(self.result().state, 'selected')
        self.flight['flightInfoList'][0]['arrivalAirport'] = 'CDG'
        self.blocked()

    def test_bad_request_template_rejected(self):
        for body in ({}, {'currency':'KRW'}, {**self.template, 'segmentList': []}):
            with self.assertRaises(ValueError):
                build_request(self.target, body, {}, 'session', 100.)
        with self.assertRaises(ValueError):
            Target('2027-02-30','ICN','FCO','KEBONUSEY','KE','931')


if __name__ == '__main__':
    unittest.main()
