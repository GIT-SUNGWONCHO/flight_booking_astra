"""A5 결제수단 조회·Npay 세션 준비의 오프라인 계약.

전송·결제창 진입·최종 승인은 하지 않는다. 경로는 contracts.PATHS 의 관측값이고
`mode=AP`·`resultCode=Success`·`reserveId`는 문서 §2.2 의 호출부 관측이다.
그 밖의 본문 키 이름은 확정되지 않았으므로 호출자가 template 으로 제공하며,
모양이 다르면 조용히 지어내지 않고 실패한다.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal
import json
import math

from availability import Target
from travellers import Order

TYPES_PATH = '/api/pp/payment/GetAvailablePaymentType'
SESSION_PATH = '/api/pp/payment/NaverPay'
# 마일리지 주문 응답 이후 호출부가 쓰는 모드. 다른 모드는 이 단계에서 다루지 않는다.
MILEAGE_MODE = 'AP'
# ICN 출발만 Npay 다. ICN 도착은 현대카드 경로이며 A5 범위가 아니다.
NPAY_ORIGIN = 'ICN'
# 이 요청에서 값을 우리가 소유하는 키. template 에 이미 있어야 주입한다.
OWNED_KEYS = ('reservationRecLoc', 'currency', 'paymentAmount')
_ERROR_KEYS = ('error','errors','errorCode','errorList','errorMessage',
               'responseCode','responseMessage')


def _finite(*values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def _still_current(request, order, now):
    """응답 판정 시점에도 요청·주문이 아직 유효한지 본다.

    요청 이후인지만 보면 오래된 성공 응답을 다시 넣어 새 세션처럼 통과시킬 수 있다.
    과거 결제 세션·URL 재사용 금지(§2.2)와 같은 기준이다.
    """
    if not _finite(now, request.created, request.max_age, request.order_received,
                   order.received):
        return 'invalid-time'
    if now < request.created or request.max_age <= 0:
        return 'invalid-time'
    if request.order_received != order.received:
        return 'context-mismatch'
    if (now - request.created > request.max_age
            or now - order.received > request.max_age):
        return 'expired-context'
    return None


def _bound(order, target, session, subject, now, max_age):
    """A4c Order 에 묶여 있고 아직 쓸 수 있는 문맥인지 본다."""
    if type(order) is not Order or type(target) is not Target:
        return 'missing-context'
    if (not isinstance(subject, str) or not subject.strip()
            or not isinstance(session, str) or not session.strip()
            or order.subject != subject or order.session != session
            or order.quote.session != session or order.quote.target != target):
        return 'context-mismatch'
    if not _finite(now, max_age, order.received, order.request_created):
        return 'invalid-time'
    if max_age <= 0 or not order.request_created <= order.received <= now:
        return 'invalid-time'
    # 과거 주문의 결제 세션을 다시 쓰지 않는다.
    if now - order.received > max_age:
        return 'stale-order'
    if not isinstance(order.reference, str) or not order.reference.strip():
        return 'missing-reference'
    return None


@dataclass(frozen=True)
class TypesRequest:
    currency: str
    mode: str
    order_reference: str = field(repr=False)
    subject: str = field(repr=False)
    session: str = field(repr=False)
    created: float
    max_age: float
    order_received: float
    path: str = TYPES_PATH
    method: str = 'POST'
    offline_only: bool = True
    wire_contract_verified: bool = False


def build_types_request(order, *, target, session, subject, now, max_age=600.0):
    """결제수단 목록 요청. 입력은 통화와 모드뿐이라는 호출부 관측을 따른다."""
    problem = _bound(order, target, session, subject, now, max_age)
    if problem:
        raise ValueError(problem)
    return TypesRequest(target.currency, MILEAGE_MODE, order.reference, subject, session,
                        now, max_age, order.received)


@dataclass(frozen=True)
class TypesOutcome:
    state: str
    payload_seen: bool = False
    # 이 수단을 현재 주문에 실제로 쓸 수 있는지는 오프라인에서 확인할 수 없다.
    applicability_verified: bool = False


def judge_types(request, status, payload, *, order, now):
    """목록 응답의 판정. 구체 목록 구조가 관측되지 않아 항목을 해석하지 않는다."""
    if type(request) is not TypesRequest or type(order) is not Order:
        return TypesOutcome('missing-context')
    if (request.order_reference != order.reference or request.subject != order.subject
            or request.session != order.session):
        return TypesOutcome('context-mismatch')
    problem = _still_current(request, order, now)
    if problem:
        return TypesOutcome(problem)
    if type(status) is not int or isinstance(status, bool):
        return TypesOutcome('no-response')
    if status != 200:
        return TypesOutcome('http-error')
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return TypesOutcome('unreadable-response')
    if type(payload) is not dict:
        return TypesOutcome('unreadable-response')
    if (any(payload.get(k) not in (None, '', False, [], {}) for k in _ERROR_KEYS)
            or payload.get('success') is False or payload.get('isSuccess') is False):
        return TypesOutcome('business-error')
    # 목록 키 이름·항목 구조가 미확정이라 '수단 있음'으로 판정하지 않는다.
    return TypesOutcome('types-unverified', payload_seen=True)


@dataclass(frozen=True)
class SessionRequest:
    body_json: str = field(repr=False)
    order_reference: str = field(repr=False)
    subject: str = field(repr=False)
    session: str = field(repr=False)
    amount: Decimal = field(repr=False)
    currency: str
    created: float
    max_age: float
    order_received: float
    provider: str = 'npay'
    path: str = SESSION_PATH
    method: str = 'POST'
    offline_only: bool = True
    wire_contract_verified: bool = False


def build_session_request(order, template, *, target, session, subject, now, max_age=600.0):
    """Npay 세션 요청 구성.

    금액·통화·예약번호만 우리가 채우고 영업소·출도착일·콜백 등 나머지는 호출자
    template 값을 그대로 둔다. template 에 소유 키가 없으면 지어내지 않고 실패한다.
    """
    problem = _bound(order, target, session, subject, now, max_age)
    if problem:
        raise ValueError(problem)
    if target.origin != NPAY_ORIGIN:
        raise ValueError('unsupported-provider')
    if type(template) is not dict or not all(k in template for k in OWNED_KEYS):
        raise ValueError('unverified-template-shape')
    amount = order.quote.total_amount
    if type(amount) is not Decimal or not amount.is_finite() or amount <= 0:
        raise ValueError('invalid-amount')
    body = deepcopy(template)
    body.update(reservationRecLoc=order.reference, currency=target.currency,
                paymentAmount=str(amount))
    try:
        body_json = json.dumps(body, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('invalid-template-json') from None
    return SessionRequest(body_json, order.reference, subject, session,
                          amount, target.currency, now, max_age, order.received)


@dataclass(frozen=True)
class Session:
    reserve_id: str = field(repr=False)
    order_reference: str = field(repr=False)
    amount: Decimal = field(repr=False)
    currency: str
    received: float


@dataclass(frozen=True)
class SessionOutcome:
    state: str
    session: Session | None = None
    # 실제 제공자 창 도착은 브라우저·사용자가 판정한다. 여기서는 절대 참이 아니다.
    payment_window_reached: bool = False
    # 세션이 만들어졌을 수 있다. 과거 세션·URL 재사용 금지 판단에 쓴다.
    session_possible: bool = True


def judge_session(request, status, payload, *, order, now):
    """Npay 세션 응답 판정. resultCode=Success 와 유효 reserveId 만 통과시킨다."""
    if type(request) is not SessionRequest or type(order) is not Order:
        return SessionOutcome('missing-context')
    if (request.order_reference != order.reference or request.subject != order.subject
            or request.session != order.session):
        return SessionOutcome('context-mismatch')
    problem = _still_current(request, order, now)
    if problem:
        return SessionOutcome(problem)
    if type(status) is not int or isinstance(status, bool):
        return SessionOutcome('session-unknown')
    if status != 200:
        return SessionOutcome('http-error')
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return SessionOutcome('session-unknown')
    if type(payload) is not dict:
        return SessionOutcome('session-unknown')
    if (any(payload.get(k) not in (None, '', False, [], {}) for k in _ERROR_KEYS)
            or payload.get('success') is False or payload.get('isSuccess') is False):
        return SessionOutcome('business-error')
    if payload.get('resultCode') != 'Success':
        return SessionOutcome('session-rejected')
    reserve = payload.get('reserveId')
    if not isinstance(reserve, str) or not reserve.strip():
        return SessionOutcome('missing-reserve-id')
    return SessionOutcome('session-ready', Session(reserve, order.reference,
                                                   request.amount, request.currency, now))
