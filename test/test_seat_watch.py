"""좌석 감시기(dev/seat_watch.py)의 좌석 읽기·변화 판정 시험. 실사이트 요청 없음."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
sys.path.insert(0, str(ROOT / 'api_booking'))
import seat_watch as sw  # noqa: E402
from availability import Target  # noqa: E402

TARGET = Target('2026-12-17', 'CDG', 'ICN', 'KEBONUSEY', 'KE', '902')


def body(seat='5', flight='902', carrier='KE', code_share=False, date='20261217'):
    return json.dumps({'currency': 'KRW', 'upsellBoundAvailList': [{'availFlightList': [{
        'flightId': '1', 'soldOut': False,
        'flightInfoList': [{'flightNumber': flight, 'operationCarrierCode': carrier,
                            'codeShare': code_share, 'departureDateTime': date + '193000'}],
        'commercialFareFamilyList': [{'recommendId': '1', 'fareFamily': 'KEBONUSEY',
                                      'seatCount': seat, 'soldout': False}]}]}]})


def sample(at, seat, state='ok'):
    return {'at': f'2026-09-20T{at}+09:00', 'state': state, 'seat': seat}


class ReadSeatTests(unittest.TestCase):
    def test_reads_target_seat_count(self):
        self.assertEqual(sw.read_seat(body('5'), TARGET), ('ok', '5'))

    def test_rejects_other_flight_codeshare_or_date(self):
        for kw in ({'flight': '901'}, {'carrier': 'AF', 'code_share': True}, {'date': '20261218'}):
            with self.subTest(kw):
                self.assertEqual(sw.read_seat(body(**kw), TARGET), ('target-absent', None))

    def test_business_error_and_broken_body(self):
        self.assertEqual(sw.read_seat(json.dumps({'code': 'ERT.10032'}), TARGET), ('error:ERT.10032', None))
        self.assertEqual(sw.read_seat('<html>', TARGET), ('invalid-json', None))
        self.assertEqual(sw.read_seat(json.dumps([1]), TARGET), ('invalid-schema', None))


class TransitionTests(unittest.TestCase):
    def test_decrease_then_increase_is_reported_with_gap(self):
        rows = [sample('12:00:00.000', '5'), sample('12:01:00.000', '4'),
                sample('12:02:00.000', '4'), sample('12:03:00.000', '5')]
        out = sw.transitions(rows)
        self.assertEqual([(t['from'], t['to'], t['increase']) for t in out],
                         [('5', '4', False), ('4', '5', True)])
        self.assertEqual(out[1]['gapSeconds'], 60.0)
        self.assertEqual(out[1]['lastSeenAt'], rows[2]['at'])

    def test_error_samples_are_skipped(self):
        rows = [sample('12:00:00.000', '5'), sample('12:01:00.000', None, 'error:ERT.3002'),
                sample('12:02:00.000', '5')]
        self.assertEqual(sw.transitions(rows), [])

    def test_no_transition_when_seat_is_steady(self):
        self.assertEqual(sw.transitions([sample('12:00:00.000', '5')] * 3), [])


if __name__ == '__main__':
    unittest.main()
