"""수동 조작 기록기(dev/flow_trace.py)의 경로 판정·요약 시험. 브라우저·네트워크 없음."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
import flow_trace as ft  # noqa: E402


class PathTests(unittest.TestCase):
    def test_path_is_taken_from_the_site_host(self):
        self.assertEqual(ft.path_of('https://www.koreanair.com/api/x/y?z=1'), '/api/x/y')

    def test_other_hosts_are_left_alone(self):
        self.assertEqual(ft.path_of('https://other.example/api/x'), 'https://other.example/api/x')

    def test_order_creating_paths_are_dangerous(self):
        for p in ('/api/ap/booking/traveller/inputTravellers', '/api/x/createOrder',
                  '/api/x/reservationAdd', '/api/x/pnrStatus', '/api/x/travelerInfo'):
            with self.subTest(p):
                self.assertTrue(ft.is_dangerous(p))

    def test_search_and_fare_paths_are_allowed(self):
        for p in ('/api/ap/booking/avail/awardAvailability', '/api/ap/booking/avail/fareInformation',
                  '/api/et/ibeSupport/officeMeta', '/api/li/auth/refresh'):
            with self.subTest(p):
                self.assertFalse(ft.is_dangerous(p))


class SummaryTests(unittest.TestCase):
    def test_counts_and_unique_paths(self):
        calls = [{'method': 'GET', 'path': '/api/a'}, {'method': 'POST', 'path': '/api/b'},
                 {'method': 'POST', 'path': '/api/b'}, {'method': 'POST', 'path': '/api/c'}]
        blocked = [{'path': '/api/ap/booking/traveller/inputTravellers'}]
        out = ft.summarize(calls, blocked)
        self.assertEqual((out['total'], out['posts'], out['blocked']), (4, 3, 1))
        self.assertEqual(out['postPaths'], ['/api/b', '/api/c'])
        self.assertEqual(out['blockedPaths'], ['/api/ap/booking/traveller/inputTravellers'])

    def test_empty_run(self):
        out = ft.summarize([], [])
        self.assertEqual((out['total'], out['posts'], out['blocked']), (0, 0, 0))
        self.assertEqual(out['postPaths'], [])


if __name__ == '__main__':
    unittest.main()
