"""대체 예매 게이트: 신호를 읽고 '주문한다/안 한다'를 정하는 규칙 시험.

핵심은 **닫히는 쪽이 기본**이라는 것이다. 신호가 없거나 애매하면 주문하지 않는다.
주문을 두 번 보내는 쪽으로는 어떤 입력으로도 갈 수 없어야 한다.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_order


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
