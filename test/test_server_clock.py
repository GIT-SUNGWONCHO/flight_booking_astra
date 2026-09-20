"""서버 시각 추정기(dev/server_clock.py)의 경계 계산 시험. 실제 요청 없음."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
import server_clock as sc  # noqa: E402

BASE = 1_700_000_000.0


def row(sent, received, date):
    return {'sent': BASE + sent, 'received': BASE + received, 'date': BASE + date,
            'rttMs': (received - sent) * 1000}


class EstimateTests(unittest.TestCase):
    def test_edge_is_between_last_send_of_d_and_first_receive_of_next(self):
        # 서버가 로컬보다 0.3초 느리다고 하자: 로컬 10.30 에 서버 초가 10 으로 바뀐다.
        rows = [row(10.20, 10.25, 9), row(10.25, 10.30, 9), row(10.32, 10.37, 10), row(10.40, 10.45, 10)]
        out = sc.estimate(rows)
        self.assertEqual(len(out['edges']), 1)
        edge = out['edges'][0]
        self.assertAlmostEqual(edge['aheadLowMs'], 250.0, places=1)    # 마지막 d 응답의 송신
        self.assertAlmostEqual(edge['aheadHighMs'], 370.0, places=1)   # 첫 d+1 응답의 수신
        self.assertEqual(out['localAheadOfServerMs'], [250.0, 370.0])

    def test_multiple_edges_narrow_the_window(self):
        rows = [row(10.20, 10.25, 9), row(10.32, 10.37, 10),
                row(11.28, 11.33, 10), row(11.34, 11.39, 11)]
        out = sc.estimate(rows)
        self.assertEqual(len(out['edges']), 2)
        low, high = out['localAheadOfServerMs']
        self.assertAlmostEqual(low, 280.0, places=1)     # 두 경계 하한 중 큰 값
        self.assertAlmostEqual(high, 370.0, places=1)    # 두 경계 상한 중 작은 값
        self.assertNotIn('edgesDisagree', out)

    def test_disagreeing_edges_report_the_widest_window(self):
        rows = [row(10.20, 10.25, 9), row(10.32, 10.37, 10),
                row(11.50, 11.55, 10), row(11.60, 11.65, 11)]
        out = sc.estimate(rows)
        self.assertTrue(out['edgesDisagree'])
        low, high = out['localAheadOfServerMs']
        self.assertLess(low, high)

    def test_true_time_window_uses_the_ntp_offset(self):
        rows = [row(10.20, 10.25, 9), row(10.32, 10.37, 10)]
        out = dict(sc.estimate(rows))
        # measure() 가 하는 계산: 기준시각 = 로컬 + offset
        window = out['localAheadOfServerMs']
        offset = -0.3
        self.assertAlmostEqual(window[0] + offset * 1000, -100.0, places=1)
        self.assertAlmostEqual(window[1] + offset * 1000, 70.0, places=1)

    def test_no_edge_when_only_one_second_seen(self):
        out = sc.estimate([row(10.20, 10.25, 9), row(10.30, 10.35, 9)])
        self.assertEqual(out['edges'], [])
        self.assertNotIn('localAheadOfServerMs', out)


if __name__ == '__main__':
    unittest.main()
