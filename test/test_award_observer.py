"""예매 조회 계측기(dev/award_observer.py)의 일정·요약 로컬 시험. 실사이트 요청 없음."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
import award_observer as o  # noqa: E402

OPEN = 1_000_000.0


def res(i, sent, recv, state, **kw):
    return {'kind': 'response', 'id': i, 'sentAt': OPEN + sent, 'receivedAt': OPEN + recv,
            'lagMs': 3, 'bytes': 40000, 'state': state, **kw}


class ScheduleTests(unittest.TestCase):
    def test_default_schedule(self):
        s = o.schedule(o.DEFAULTS)
        self.assertEqual(s[0], -1800)
        self.assertEqual(s[1] - s[0], 150)
        self.assertIn(600, s)
        self.assertEqual(s[s.index(600) + 1], 900)       # 촘촘한 구간 뒤 성긴 간격
        self.assertLessEqual(s[-1], 10000)
        self.assertEqual(s, sorted(s))

    def test_config_has_award_observer(self):
        cfg = o.settings()
        self.assertIn(cfg['mode'], ('calendar', 'award', 'both'))
        self.assertLess(cfg['denseFromMs'], 0)
        self.assertGreater(cfg['maxInflight'], 0)


class SummaryTests(unittest.TestCase):
    def test_edge_between_last_not_open_and_first_open(self):
        ev = [res(0, -1800, -560, 'not-open', code='ERT.10032'),
              res(1, -1650, -400, 'not-open', code='ERT.10032'),
              res(2, -1500, 20, 'open', target='found', seat='2'),
              res(3, -1350, 150, 'open', target='found', seat='2')]
        s = o.summarize(ev, OPEN)
        self.assertEqual(s['edge']['lastNotOpenSent'], -1.65)
        self.assertEqual(s['edge']['firstOpenSent'], -1.5)
        self.assertEqual(s['edge']['notOpenAfterFirstOpen'], 0)
        self.assertEqual(s['notOpen'], 2)
        self.assertEqual(s['open'], 2)
        self.assertEqual(s['firstSeat'], '2')

    def test_not_open_after_first_open_is_counted(self):
        ev = [res(0, -900, 400, 'open', target='found', seat='1'),
              res(1, -750, 500, 'not-open', code='ERT.10032')]
        self.assertEqual(o.summarize(ev, OPEN)['edge']['notOpenAfterFirstOpen'], 1)

    def test_seat_changes_ordered_by_send(self):
        ev = [res(0, 0, 1500, 'open', target='found', seat='2'),
              res(1, 300, 1700, 'open', target='found', seat='2'),
              res(2, 600, 2300, 'open', target='found', seat='1'),     # 늦게 도착해도 송신 순
              res(3, 900, 2100, 'open', target='found', seat='0')]
        s = o.summarize(ev, OPEN)
        self.assertEqual([(c['from'], c['to']) for c in s['seatChanges']], [('2', '1'), ('1', '0')])
        self.assertEqual(s['seatChanges'][0]['lastSentWithFrom'], 0.3)
        self.assertEqual(s['seatChanges'][0]['firstReceivedWithTo'], 2.3)

    def test_errors_and_skips(self):
        ev = [res(0, 0, 900, 'error', code='ERT.3010'), res(1, 150, 900, 'fetch-error'),
              {'kind': 'skipped', 'id': 2, 'due': OPEN + 300, 'why': 'inflight'}]
        s = o.summarize(ev, OPEN)
        self.assertEqual(s['errors'], {'ERT.3010': 1, 'fetch-error': 1})
        self.assertEqual(s['skipped'], 1)
        self.assertIsNone(s['edge']['firstOpenSent'])

    def test_route_and_family_overrides_need_rehearsal(self):
        # 실전 계측은 공통 일정의 노선·등급만 쓴다(리허설에서만 바꿀 수 있다).
        import argparse
        args = argparse.Namespace(day='2026-09-20', at='', target='', rehearsal=False, family='',
                                  origin='CDG', destination='ICN', flight='902', ready_file='')
        with self.assertRaisesRegex(ValueError, 'rehearsal'):
            o.run(args)

    def test_no_blocking_sleep_while_connected(self):
        # 동기 Playwright 가 잠들면 같은 Chrome 의 다른 클라이언트·새 문서가 멈춘다(9/19 리허설).
        src = (ROOT / 'dev' / 'award_observer.py').read_text(encoding='utf-8')
        self.assertNotIn('time.sleep(', src)

    def test_sampler_keeps_no_identifiers(self):
        for word in ('recommendId', 'flightId', 'pageTicket'):
            self.assertNotIn(word, o.SAMPLER)


if __name__ == '__main__':
    unittest.main()
