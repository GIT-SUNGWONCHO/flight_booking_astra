"""실제 달력 응답의 공항 필드 누락과 잘못된 노선·오류 응답 회귀 시험."""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'dev'))
from manual_booking import verify_calendar_route


class BookingRouteTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'segmentList': [{'departureAirport': 'ICN', 'arrivalAirport': 'CDG',
                                        'departureDate': '20270904'}],
                        'travelers': [{'firstName': 'PRIVATE_TEST_VALUE'}]}
        self.data = {'currency': 'KRW', 'boundFareCalendarList': [
            {'departureDate': '20270904', 'fareCalendarList': []}]}

    def response(self, **overrides):
        values = {'url': 'https://www.koreanair.com/api/ap/booking/avail/calendarFareMatrix',
                  'status': 200, 'request': SimpleNamespace(method='POST', post_data_json=self.payload),
                  'json': lambda: self.data}
        values.update(overrides)
        return SimpleNamespace(**values)

    def verify(self, **overrides):
        return verify_calendar_route(self.response(**overrides), 'ICN', 'CDG')

    def test_actual_response_without_airports(self):
        result = self.verify()
        self.assertFalse(result['responseRouteFieldsPresent'])
        self.assertEqual((result['origin'], result['destination']), ('ICN', 'CDG'))
        self.assertNotIn('PRIVATE_TEST_VALUE', json.dumps(result))

    def test_alias_and_reverse_direction(self):
        self.payload['segmentList'][0]['departureAirport'] = 'SEL'
        self.assertEqual(self.verify()['origin'], 'ICN')
        self.payload['segmentList'][0].update(departureAirport='CDG', arrivalAirport='SEL')
        self.assertEqual(verify_calendar_route(self.response(), 'CDG', 'ICN')['destination'], 'ICN')

    def test_wrong_request_even_if_response_matches(self):
        self.data['boundFareCalendarList'][0].update(departureAirport='ICN', arrivalAirport='CDG')
        self.payload['segmentList'][0]['arrivalAirport'] = 'FCO'
        with self.assertRaisesRegex(ValueError, '요청과 확정 노선 불일치'):
            self.verify()

    def test_explicit_response_route_is_still_checked(self):
        self.data['boundFareCalendarList'][0].update(departureAirport='SEL', arrivalAirport='CDG')
        self.assertTrue(self.verify()['responseRouteFieldsPresent'])
        for values in [('CDG', 'ICN'), ('ICN', None), ('ASEL', 'CDG')]:
            self.data['boundFareCalendarList'][0].update(zip(['departureAirport', 'arrivalAirport'], values))
            with self.assertRaises(ValueError):
                self.verify()

    def test_ambiguous_or_malformed_bounds(self):
        bound = copy.deepcopy(self.data['boundFareCalendarList'][0])
        for bounds in [None, [], [bound, bound], [None], [{'fareCalendarList': None}]]:
            self.data['boundFareCalendarList'] = bounds
            with self.assertRaises(ValueError):
                self.verify()

    def test_ambiguous_or_malformed_requests(self):
        segment = copy.deepcopy(self.payload['segmentList'][0])
        for segments in [None, [], [segment, segment], [None], [{}]]:
            self.payload['segmentList'] = segments
            with self.assertRaises(ValueError):
                self.verify()

    def test_http_application_and_endpoint_errors(self):
        for overrides in [{'status': 503}, {'url': 'https://example.com/api/ap/booking/avail/calendarFareMatrix'},
                          {'request': SimpleNamespace(method='GET')}]:
            with self.assertRaises(ValueError):
                self.verify(**overrides)
        for data in [{'code': 503}, {'code': 'ERT.10032'}, {}, None, []]:
            self.data = data
            with self.assertRaises(ValueError):
                self.verify()

    def test_parse_error_does_not_expose_raw_exception(self):
        def failed():
            raise RuntimeError('PRIVATE_TEST_VALUE')
        with self.assertRaisesRegex(ValueError, '^달력 요청/응답 해석 실패$'):
            self.verify(json=failed)


if __name__ == '__main__':
    unittest.main()
