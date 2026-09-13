"""A1: 단일 실행의 주문 상태 모델. 통신·예약·영속 저장 없음.

검증 플래그는 향후 사이트 판정기가 전달할 입력이며 자체로 서버 증거는 아니다.
재시작·다중 프로세스의 중복 방지는 A2의 영속 기록과 배타 잠금이 필요하다.
"""
from dataclasses import dataclass, fields
from enum import Enum


class State(str, Enum):
    VALIDATING = 'validating'
    READY = 'ready'
    ORDER_INTENT = 'order-intent'
    ORDER_IN_FLIGHT = 'order-in-flight'
    ORDER_UNKNOWN = 'order-unknown'
    ORDER_CONFIRMED = 'order-confirmed'
    HANDOFF = 'handoff'
    PAYMENT_READY = 'payment-ready'
    STOPPED = 'stopped'


class Event(str, Enum):
    RECORD_INTENT = 'record-intent'
    BEGIN_SEND = 'begin-send'
    CONFIRM_ORDER = 'confirm-order'
    BEGIN_HANDOFF = 'begin-handoff'
    HANDOFF_FAILED = 'handoff-failed'
    CONFIRM_PAYMENT_WINDOW = 'confirm-payment-window'
    INTERRUPT = 'interrupt'


@dataclass(frozen=True)
class Checks:
    target_matches: bool = False
    fare_is_current: bool = False
    session_matches: bool = False
    required_checks_passed: bool = False
    no_unresolved_order: bool = False
    user_started: bool = False

    def complete(self):
        return all(getattr(self, field.name) is True for field in fields(self))


class InvalidTransition(ValueError):
    pass


_NEXT = {
    (State.READY, Event.RECORD_INTENT): State.ORDER_INTENT,
    (State.ORDER_INTENT, Event.BEGIN_SEND): State.ORDER_IN_FLIGHT,
    (State.ORDER_IN_FLIGHT, Event.CONFIRM_ORDER): State.ORDER_CONFIRMED,
    (State.ORDER_CONFIRMED, Event.BEGIN_HANDOFF): State.HANDOFF,
    (State.HANDOFF, Event.HANDOFF_FAILED): State.ORDER_CONFIRMED,
    (State.HANDOFF, Event.CONFIRM_PAYMENT_WINDOW): State.PAYMENT_READY,
}


class OrderFlow:
    """단일 스레드용. 상태 확인과 변경은 같은 객체를 공유해서 수행한다."""

    def __init__(self):
        self._state = State.VALIDATING

    @property
    def state(self):
        return self._state

    def prepare(self, checks):
        if self._state is not State.VALIDATING:
            raise InvalidTransition('prepare-not-allowed')
        if type(checks) is not Checks:
            raise TypeError('checks-required')
        self._state = State.READY if checks.complete() else State.STOPPED
        return self._state

    def advance(self, event):
        # 원문 응답/HTTP200으로 성공을 추정하지 않는다. 이벤트는 별도 판정기의 책임.
        if type(event) is not Event:
            raise TypeError('event-required')
        if event is Event.INTERRUPT:
            if self._state in (State.VALIDATING, State.READY):
                next_state = State.STOPPED
            elif self._state in (State.ORDER_INTENT, State.ORDER_IN_FLIGHT):
                next_state = State.ORDER_UNKNOWN
            elif self._state is State.HANDOFF:
                next_state = State.ORDER_CONFIRMED
            else:
                next_state = self._state
        else:
            next_state = _NEXT.get((self._state, event))
            if next_state is None:
                raise InvalidTransition('transition-not-allowed')
        self._state = next_state
        return next_state
