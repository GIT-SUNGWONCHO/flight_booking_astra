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


class AutoCaptureTests(unittest.TestCase):
    """--capture-iso auto: 임시 날짜로 달력을 열고, 읽어서 고르고, 다르면 그 날짜로 다시 연다."""

    CELLS = [{'text': '13 09월 13일 (월) 일반석', 'disabled': False},
             {'text': '15 09월 15일 (수) 일반석', 'disabled': False},
             {'text': '18 09월 18일 (토) 좌석 없음', 'disabled': True}]

    def run_chain(self, cells, codes=(0,), target='2027-09-20'):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        codes = list(codes)
        self.setups, self.orders, self.logs = [], [], []

        def fake_prepare(report, a):
            self.setups.append(a.capture_iso)
            return True

        def fake_call(args, **kw):
            self.orders.append(args[args.index('--capture-iso') + 1])
            return codes.pop(0)
        argv = ['api_day.py', '--mode', 'rehearsal', '--target-date', target, '--capture-iso', 'auto',
                '--own-mileage', '100000']
        with mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(api_day, 'OUT', tmp), \
                mock.patch.object(api_day, 'keep_awake'), \
                mock.patch.object(api_day, 'lingering_orders', return_value=[]), \
                mock.patch.object(api_day, 'browser', return_value=True), \
                mock.patch.object(api_day, 'prepare', fake_prepare), \
                mock.patch.object(api_day.capture_date, 'read_cells', return_value=cells), \
                mock.patch.object(api_day, 'log', self.logs.append), \
                mock.patch.object(api_day.subprocess, 'call', fake_call), \
                mock.patch.object(api_day.time, 'sleep'):
            code = api_day.main()
        return code, json.loads(next(tmp.glob('rehearsal-*.json')).read_text(encoding='utf-8'))

    def test_picks_from_the_calendar_and_reopens_it_on_that_day(self):
        code, report = self.run_chain(self.CELLS)
        self.assertEqual(code, 0)
        self.assertEqual(self.setups, ['2027-09-13', '2027-09-15'])     # 임시(한 주 전) → 고른 날
        self.assertEqual(self.orders, ['2027-09-15'])
        self.assertEqual(report['captureIso'], '2027-09-15')
        self.assertEqual(report['captureAuto']['picked'], '2027-09-15')

    def test_no_second_setup_when_the_tentative_day_is_the_pick(self):
        code, _ = self.run_chain(self.CELLS[:1])
        self.assertEqual((code, self.setups, self.orders), (0, ['2027-09-13'], ['2027-09-13']))

    def test_nothing_to_pick_never_starts_the_order_program(self):
        code, report = self.run_chain(self.CELLS[2:])
        self.assertEqual((code, report['result'], self.orders), (2, 'prepare-failed', []))
        self.assertEqual(len(self.setups), 3)                           # 준비 시도 3번 모두 임시 날짜로
        self.assertTrue(any('캡처 날짜를 고를 수 없다' in x for x in self.logs))

    def test_each_new_attempt_reads_the_calendar_again(self):
        code, _ = self.run_chain(self.CELLS, codes=[live_order.EXIT_REPREPARE, 0])
        self.assertEqual(code, 0)
        self.assertEqual(self.setups, ['2027-09-13', '2027-09-15', '2027-09-13', '2027-09-15'])

    def test_bad_capture_value_is_refused(self):
        with mock.patch.object(sys, 'argv', ['api_day.py', '--mode', 'rehearsal', '--target-date', '2027-09-20',
                                              '--capture-iso', 'tomorrow', '--own-mileage', '1']), \
                mock.patch.object(api_day, 'log', lambda m: None):
            self.assertEqual(api_day.main(), 2)


if __name__ == '__main__':
    unittest.main(verbosity=0)
