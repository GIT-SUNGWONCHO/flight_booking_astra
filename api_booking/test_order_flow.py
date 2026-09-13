"""A1: 검증 누락·중복 전송·응답 유실·인계 실패의 상태 전이를 시험한다."""
import unittest
from dataclasses import fields, replace

from order_flow import Checks, Event, InvalidTransition, OrderFlow, State


PASSED = Checks(True, True, True, True, True, True)


class OrderFlowTests(unittest.TestCase):
    def ready(self):
        flow = OrderFlow()
        flow.prepare(PASSED)
        return flow

    def sending(self):
        flow = self.ready()
        flow.advance(Event.RECORD_INTENT)
        flow.advance(Event.BEGIN_SEND)
        return flow

    def test_every_check_must_be_explicitly_true(self):
        for field in fields(Checks):
            for missing in (False, None, 1, 'true'):
                with self.subTest(field=field.name, value=missing):
                    flow = OrderFlow()
                    self.assertEqual(flow.prepare(replace(PASSED, **{field.name: missing})),
                                     State.STOPPED)
                    with self.assertRaises(InvalidTransition):
                        flow.advance(Event.RECORD_INTENT)

    def test_cannot_send_without_validation_and_intent(self):
        for flow in (OrderFlow(), self.ready()):
            state = flow.state
            with self.assertRaises(InvalidTransition):
                flow.advance(Event.BEGIN_SEND)
            self.assertEqual(flow.state, state)

    def test_normal_path_and_no_second_send(self):
        flow = self.sending()
        for event, expected in (
            (Event.CONFIRM_ORDER, State.ORDER_CONFIRMED),
            (Event.BEGIN_HANDOFF, State.HANDOFF),
            (Event.CONFIRM_PAYMENT_WINDOW, State.PAYMENT_READY),
        ):
            with self.assertRaises(InvalidTransition):
                flow.advance(Event.BEGIN_SEND)
            self.assertEqual(flow.advance(event), expected)
        with self.assertRaises(InvalidTransition):
            flow.advance(Event.BEGIN_SEND)

    def test_interruption_after_intent_or_send_latches_unknown(self):
        for sent in (False, True):
            flow = self.ready()
            flow.advance(Event.RECORD_INTENT)
            if sent:
                flow.advance(Event.BEGIN_SEND)
            self.assertEqual(flow.advance(Event.INTERRUPT), State.ORDER_UNKNOWN)
            # 새 시작, 성공 추정, UI 인계로 불명확 상태를 지울 수 없다.
            with self.assertRaises(InvalidTransition):
                flow.prepare(PASSED)
            for event in Event:
                if event is Event.INTERRUPT:
                    self.assertEqual(flow.advance(event), State.ORDER_UNKNOWN)
                else:
                    with self.assertRaises(InvalidTransition):
                        flow.advance(event)

    def test_handoff_failure_keeps_order_and_allows_only_handoff_retry(self):
        for failure in (Event.HANDOFF_FAILED, Event.INTERRUPT):
            flow = self.sending()
            flow.advance(Event.CONFIRM_ORDER)
            flow.advance(Event.BEGIN_HANDOFF)
            self.assertEqual(flow.advance(failure), State.ORDER_CONFIRMED)
            for event in (Event.RECORD_INTENT, Event.BEGIN_SEND):
                with self.assertRaises(InvalidTransition):
                    flow.advance(event)
            self.assertEqual(flow.advance(Event.BEGIN_HANDOFF), State.HANDOFF)

    def test_pre_order_stop_is_terminal(self):
        flow = self.ready()
        self.assertEqual(flow.advance(Event.INTERRUPT), State.STOPPED)
        with self.assertRaises(InvalidTransition):
            flow.prepare(PASSED)
        with self.assertRaises(InvalidTransition):
            flow.advance(Event.RECORD_INTENT)

    def test_wrong_inputs_and_premature_success_do_not_change_state(self):
        flow = OrderFlow()
        with self.assertRaises(TypeError):
            flow.prepare({'target_matches': True})
        self.assertEqual(flow.state, State.VALIDATING)
        flow.prepare(PASSED)
        with self.assertRaises(TypeError):
            flow.advance('confirm-order')
        for event in (Event.CONFIRM_ORDER, Event.BEGIN_HANDOFF,
                      Event.CONFIRM_PAYMENT_WINDOW):
            with self.assertRaises(InvalidTransition):
                flow.advance(event)
        self.assertEqual(flow.state, State.READY)


if __name__ == '__main__':
    unittest.main()
