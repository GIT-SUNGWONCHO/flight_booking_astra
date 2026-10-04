"""무인 체인(api_day)의 재준비 판단 시험. 브라우저·사이트·실제 프로세스 없음.

체인은 live_order 의 종료 코드와 상태 폴더의 기록만 보고 '다시 준비해도 되는가'를 정한다.
주문이 나갔을 수 있는 경우에 다시 실행하지 않는 것이 핵심이다.
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
import api_day  # noqa: E402
import live_order  # noqa: E402

REHEARSAL = ['api_day.py', '--mode', 'rehearsal', '--target-date', '2027-09-09', '--capture-iso', '2027-09-07',
             '--own-mileage', '100000']


class OrderNotSentTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def intent(self, state, day='2099-01-01'):
        (self.dir / f'order-intent-{day}.json').write_text(json.dumps({'state': state}), encoding='utf-8')

    def test_empty_folder_means_nothing_was_sent(self):
        self.assertTrue(api_day.order_not_sent(self.dir))
        self.assertTrue(api_day.order_not_sent(self.dir / 'never-created'))

    def test_preparation_states_mean_nothing_was_sent(self):
        for state in api_day.PRE_ORDER_STATES:
            with self.subTest(state):
                self.intent(state)
                self.assertTrue(api_day.order_not_sent(self.dir))

    def test_permit_means_the_order_may_have_gone(self):
        self.intent('preparing')
        (self.dir / 'order-permit.json').write_text('{}', encoding='utf-8')
        self.assertFalse(api_day.order_not_sent(self.dir))

    def test_any_order_state_or_unreadable_record_is_not_safe(self):
        for state in ('sending', 'unknown', 'ordered', 'prep-order-possible', None):
            with self.subTest(state):
                self.intent(state)
                self.assertFalse(api_day.order_not_sent(self.dir))
        (self.dir / 'order-intent-2099-01-01.json').write_text('not json', encoding='utf-8')
        self.assertFalse(api_day.order_not_sent(self.dir))

    def test_states_match_the_order_program(self):
        self.assertEqual(set(api_day.PRE_ORDER_STATES), set(live_order.NON_BLOCKING))
        self.assertEqual((api_day.EXIT_REPREPARE, api_day.EXIT_RECAPTURE),
                         (live_order.EXIT_REPREPARE, live_order.EXIT_RECAPTURE))


class RetryLoopTests(unittest.TestCase):
    """live_order 의 종료 코드 순서를 주고 체인이 무엇을 하는지 본다."""

    def run_chain(self, codes, *extra, sent=False, chrome_ok=True):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        codes = list(codes)
        self.browser_calls, self.prepares, self.logs = [], [], []

        def fake_browser(report, restart, port=9232):
            self.browser_calls.append(restart)
            return chrome_ok or not restart

        def fake_prepare(report, a):
            self.prepares.append(1)
            return True

        def fake_call(args, **kw):
            self.orders = getattr(self, 'orders', 0) + 1
            return codes.pop(0)
        with mock.patch.object(sys, 'argv', REHEARSAL + list(extra)), \
                mock.patch.object(api_day, 'OUT', tmp), \
                mock.patch.object(api_day, 'keep_awake'), \
                mock.patch.object(api_day, 'lingering_orders', return_value=[]), \
                mock.patch.object(api_day, 'browser', fake_browser), \
                mock.patch.object(api_day, 'prepare', fake_prepare), \
                mock.patch.object(api_day, 'order_not_sent', return_value=not sent), \
                mock.patch.object(api_day, 'log', self.logs.append), \
                mock.patch.object(api_day.subprocess, 'call', fake_call), \
                mock.patch.object(api_day.time, 'sleep'):
            code = api_day.main()
        report = json.loads(next(tmp.glob('rehearsal-*.json')).read_text(encoding='utf-8'))
        return code, report

    def test_failed_capture_restarts_chrome_and_prepares_again(self):
        code, report = self.run_chain([live_order.EXIT_RECAPTURE, 0])
        self.assertEqual((code, report['result']), (0, 'done'))
        self.assertEqual(self.browser_calls, [False, True])      # 처음(리허설은 그대로) → 다시 띄움
        self.assertEqual(len(self.prepares), 2)
        self.assertTrue(any('주문은 나가지 않았다' in x for x in self.logs))

    def test_session_problem_prepares_again_without_restarting_chrome(self):
        code, _ = self.run_chain([live_order.EXIT_REPREPARE, 0])
        self.assertEqual(code, 0)
        self.assertEqual(self.browser_calls, [False])
        self.assertEqual(len(self.prepares), 2)

    def test_crash_before_the_order_is_retried(self):
        code, _ = self.run_chain([1, 0], sent=False)
        self.assertEqual((code, self.browser_calls, len(self.prepares)), (0, [False, True], 2))

    def test_crash_after_the_order_is_never_retried(self):
        code, report = self.run_chain([1, 0], sent=True)
        self.assertEqual((code, report['result'], self.orders), (1, 'live_order-exit-1', 1))

    def test_other_exit_codes_are_never_retried(self):
        for bad in (2, 5, 137):
            with self.subTest(bad):
                self.orders = 0
                code, _ = self.run_chain([bad, 0])
                self.assertEqual((code, self.orders), (bad, 1))

    def test_gives_up_after_the_allowed_attempts(self):
        code, report = self.run_chain([live_order.EXIT_RECAPTURE] * 5)
        self.assertEqual((code, report['result'], self.orders), (2, 'attempts-exhausted', 3))
        self.assertEqual(self.browser_calls, [False, True, True])   # 마지막 실패 뒤에는 다시 띄우지 않는다

    def test_no_restart_flag_prepares_again_in_the_same_chrome(self):
        code, _ = self.run_chain([live_order.EXIT_RECAPTURE, 0], '--no-restart')
        self.assertEqual((code, self.browser_calls, len(self.prepares)), (0, [False], 2))

    def test_chrome_that_will_not_come_back_stops_the_chain(self):
        code, report = self.run_chain([live_order.EXIT_RECAPTURE, 0], chrome_ok=False)
        self.assertEqual((code, report['result'], self.orders), (2, 'chrome-failed', 1))


if __name__ == '__main__':
    unittest.main(verbosity=0)
