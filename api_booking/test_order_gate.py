"""대체 예매 게이트: 신호를 읽고 '주문한다/안 한다'를 정하는 규칙 시험.

핵심은 **닫히는 쪽이 기본**이라는 것이다. 신호가 없거나 애매하면 주문하지 않는다.
주문을 두 번 보내는 쪽으로는 어떤 입력으로도 갈 수 없어야 한다.
"""
from datetime import datetime, timedelta, timezone
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_order

KST = timezone(timedelta(hours=9))


class OrderGateTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / 'outcome.json'

    def write(self, **rec):
        self.path.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')

    def test_missing_file_waits(self):
        self.assertEqual(live_order.read_order_outcome(self.path), 'wait')

    def test_seat_taken_blocks(self):
        self.write(state='order-recorded', referencePresent=True)
        self.assertEqual(live_order.read_order_outcome(self.path), 'seat-taken')

    def test_business_error_without_reference_proceeds(self):
        """ERT.15012 처럼 예약번호가 없는 업무 오류만 대체 주문의 근거다."""
        self.write(state='business-error', referencePresent=False)
        self.assertEqual(live_order.read_order_outcome(self.path), 'proceed')

    def test_business_error_with_reference_does_not_proceed(self):
        """예약번호가 있으면 업무 오류라도 좌석을 잡았을 수 있다. 주문하지 않는다."""
        self.write(state='business-error', referencePresent=True)
        self.assertTrue(live_order.read_order_outcome(self.path).startswith('ambiguous'))

    def test_unknown_states_do_not_proceed(self):
        for state in ('order-unknown', 'http-error', 'order-judge-exception',
                      'amount-mismatch', 'target-mismatch', '', None):
            with self.subTest(state=state):
                self.write(state=state, referencePresent=False)
                self.assertNotEqual(live_order.read_order_outcome(self.path), 'proceed')

    def test_reference_missing_field_does_not_proceed(self):
        """referencePresent 가 없으면 False 로 취급하지 않는다(모르면 안 한다)."""
        self.write(state='business-error')
        self.assertTrue(live_order.read_order_outcome(self.path).startswith('ambiguous'))

    def test_garbage_file_does_not_proceed(self):
        for raw in ('', 'not json', '[]', '"x"', 'null'):
            with self.subTest(raw=raw):
                self.path.write_text(raw, encoding='utf-8')
                self.assertIn(live_order.read_order_outcome(self.path), ('unreadable', 'wait'))

    def test_only_proceed_lets_the_order_through(self):
        """fire() 는 'proceed' 가 아니면 주문하지 않는다. 그 대칭을 값으로 고정한다."""
        self.assertEqual(live_order.GATE_ORDER, 'order-recorded')
        self.assertEqual(live_order.GATE_PROCEED, ('business-error',))


class StaleSignalTests(unittest.TestCase):
    """같은 날 다시 실행하면 앞선 시도의 신호가 파일에 남아 있다. 그것으로 주문하면 안 된다."""

    START = datetime(2099, 1, 1, 8, 20, 0, tzinfo=KST)

    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / 'outcome.json'

    def write(self, at, state='business-error', ref=False):
        self.path.write_text(json.dumps({'state': state, 'referencePresent': ref, 'at': at}), encoding='utf-8')

    def test_signal_written_before_this_run_started_is_not_here_yet(self):
        self.write('2099-01-01T08:19:59+09:00')
        self.assertEqual(live_order.read_order_outcome(self.path, self.START), 'wait')
        self.write('2099-01-01T08:19:59+09:00', state='order-recorded', ref=True)
        self.assertEqual(live_order.read_order_outcome(self.path, self.START), 'wait')

    def test_signal_written_after_the_start_counts(self):
        self.write('2099-01-01T09:00:06+09:00')
        self.assertEqual(live_order.read_order_outcome(self.path, self.START), 'proceed')
        self.write('2099-01-01T09:00:08+09:00', state='order-recorded', ref=True)
        self.assertEqual(live_order.read_order_outcome(self.path, self.START), 'seat-taken')

    def test_signal_without_a_usable_time_never_proceeds(self):
        for at in (None, 'soon', '2099-01-01T09:00:06'):          # 없음 · 못 읽음 · 시간대 없음
            with self.subTest(at=at):
                self.write(at)
                self.assertTrue(live_order.read_order_outcome(self.path, self.START).startswith('ambiguous'))

    def test_without_a_start_time_the_old_rule_holds(self):
        self.write('2001-01-01T00:00:00+09:00')
        self.assertEqual(live_order.read_order_outcome(self.path), 'proceed')

    def test_signal_this_program_writes_passes_the_freshness_check(self):
        class Args:
            order_outcome_file = str(self.path)
        live_order.write_order_outcome(Args, 'business-error', {'body': '{}', 'status': 200})
        started = datetime.now(KST) - timedelta(minutes=40)
        self.assertEqual(live_order.read_order_outcome(self.path, started), 'proceed')


class OutcomeWriteTests(unittest.TestCase):
    class Args:
        def __init__(self, path):
            self.order_outcome_file = path

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / 'out.json'

    def test_writes_state_and_reference_flag(self):
        body = json.dumps({'errorCode': 'ERT.15012'})
        live_order.write_order_outcome(self.Args(str(self.path)), 'business-error',
                                       {'body': body, 'status': 200})
        rec = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(rec['state'], 'business-error')
        self.assertIs(rec['referencePresent'], False)
        self.assertEqual(live_order.read_order_outcome(self.path), 'proceed')

    def test_pnr_sets_reference_present(self):
        body = json.dumps({'pnr': 'ABCDEF'})
        live_order.write_order_outcome(self.Args(str(self.path)), 'order-recorded',
                                       {'body': body, 'status': 200})
        rec = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertIs(rec['referencePresent'], True)
        self.assertNotIn('ABCDEF', self.path.read_text(encoding='utf-8').replace('true', ''))
        self.assertEqual(live_order.read_order_outcome(self.path), 'seat-taken')

    def test_no_path_writes_nothing(self):
        live_order.write_order_outcome(self.Args(''), 'business-error', {'body': '{}'})
        self.assertFalse(self.path.exists())

    def test_unparsable_body_is_not_a_reference(self):
        live_order.write_order_outcome(self.Args(str(self.path)), 'business-error',
                                       {'body': 'not json', 'status': 500})
        rec = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertIs(rec['referencePresent'], False)


if __name__ == '__main__':
    unittest.main(verbosity=0)
