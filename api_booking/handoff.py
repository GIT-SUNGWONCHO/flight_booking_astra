"""A6 동일 주문 브라우저 인계의 오프라인 계약.

실제 창 전환·URL 이동·화면 상태 주입·최종 승인은 하지 않는다. 여기서 만드는 것은
**인계를 시도해도 되는지의 판정**과 **브라우저가 대조해야 할 기대값**뿐이다.

§3.3: 인계 경계는 기능 시험으로 고정한 뒤 실행하며 09시 실행 중에 경로를 바꾸지
않는다. §2.2: URL만 여는 것으로 같은 화면을 만들 수 없고, 정상 화면 상태로 넘기는
연결은 아직 미해결 과제다. 그래서 이 모듈은 인계 성공을 약속하지 않는다.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import json
import math
from urllib.parse import parse_qs, urlsplit

from availability import Target
from payment import NPAY_ORIGIN, Session
from travellers import Order


class Route(str, Enum):
    """§3.3의 인계 방법. 실행 전에 골라 고정한다."""
    CONTINUOUS = 'continuous'   # 우선안: 데이터 상태만 연속 반영하고 한 번 표시
    RESUME = 'resume'           # 대안: 같은 주문을 정상 재개 경로로 읽어 진입
    DEFERRED = 'deferred'       # 보류: 인계를 약속하지 않음


@dataclass(frozen=True)
class Plan:
    """실행 전에 고정한 인계 경계. 09시 중에 바꾸지 않는다.

    `run_started` 는 이 계획을 적용할 실행의 시작 시각이다. 경계는 그 전에
    고정돼 있어야 하며, 조회·운임이 진행된 뒤에 고르는 것은 실행 중 변경이다.
    """
    route: Route
    fixed_at: float
    run_started: float
    max_session_age: float = 600.0
    # 정상 화면 상태 연결이 실제로 가능한지는 미입증이다(§3.3).
    state_bridge_verified: bool = False


@dataclass(frozen=True)
class Expectation:
    """브라우저가 새 창에서 대조해야 할 값. 여기서 확인한 것이 아니다."""
    provider: str
    order_reference: str = field(repr=False)
    reserve_id: str = field(repr=False)
    currency: str
    amount: Decimal = field(repr=False)
    origin: str
    destination: str
    carrier: str
    flight: str
    date: str
    family: str


@dataclass(frozen=True)
class Handoff:
    route: Route
    expectation: Expectation
    subject: str = field(repr=False)
    session: str = field(repr=False)
    prepared: float
    # 화면 상태가 실제로 옮겨졌는지, 제공자 창에 도착했는지는 여기서 판정하지 않는다.
    screen_state_verified: bool = False
    payment_window_reached: bool = False
    offline_only: bool = True


def _finite(*values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def prepare(order, session, plan, *, target, subject, session_id, now):
    """인계를 시도해도 되는지 판정하고 기대값을 만든다.

    실패는 예외로 알린다. 반환값은 인계 권한이 아니라 대조표다.
    """
    if (type(order) is not Order or type(session) is not Session
            or type(plan) is not Plan or type(target) is not Target):
        raise ValueError('missing-context')
    if plan.route is Route.DEFERRED:
        # §3.3 3번. 인계를 약속하지 않기로 고정한 실행이다.
        raise ValueError('handoff-not-promised')
    if type(plan.route) is not Route:
        raise ValueError('unfixed-route')
    if (not isinstance(subject, str) or not subject.strip()
            or not isinstance(session_id, str) or not session_id.strip()
            or order.subject != subject or order.session != session_id
            or order.quote.session != session_id or order.quote.target != target):
        raise ValueError('context-mismatch')
    if not _finite(now, plan.fixed_at, plan.run_started, plan.max_session_age,
                   order.received, session.received, order.quote.selection_received):
        raise ValueError('invalid-time')
    if plan.max_session_age <= 0:
        raise ValueError('invalid-time')
    # 경계는 실행이 시작되기 전에 고정돼 있어야 한다. 조회·운임이 지난 뒤에
    # 고르는 것도 실행 중 변경이므로 주문 시각과 비교하지 않는다(§3.3).
    if plan.fixed_at > plan.run_started:
        raise ValueError('route-changed-mid-run')
    # 이 실행의 첫 단계가 선언된 실행 시작보다 앞서면 다른 실행의 계획이다.
    if plan.run_started > order.quote.selection_received:
        raise ValueError('plan-not-for-this-run')
    if not order.received <= session.received <= now:
        raise ValueError('invalid-time')
    if now - session.received > plan.max_session_age:
        raise ValueError('expired-session')
    # 결제 세션이 이 주문의 것인지 대조한다. 과거 세션을 끌어오지 않는다.
    if session.order_reference != order.reference:
        raise ValueError('session-order-mismatch')
    if session.currency != target.currency:
        raise ValueError('currency-mismatch')
    if session.amount != order.quote.total_amount:
        raise ValueError('amount-mismatch')
    if not all(isinstance(v, str) and v.strip()
               for v in (order.reference, session.reserve_id)):
        raise ValueError('missing-reference')
    # 방향별 제공자. ICN 출발만 Npay 이며 도착은 현대카드로 A6 범위가 아니다.
    if target.origin != NPAY_ORIGIN:
        raise ValueError('unsupported-provider')
    expectation = Expectation('npay', order.reference, session.reserve_id,
                              target.currency, order.quote.total_amount,
                              target.origin, target.destination, target.carrier,
                              target.flight, target.date, target.family)
    return Handoff(plan.route, expectation, subject, session_id, now)


@dataclass(frozen=True)
class Mismatch:
    """브라우저 관측과 기대값의 차이. 빈 목록이 도착 성공을 뜻하지는 않는다."""
    fields: tuple
    compared: bool = True


def compare(handoff, observed):
    """브라우저가 보고한 값을 기대값과 대조한다.

    실제 제공자 창 도착 판정은 운영 `dev/payment_window.py`와 사용자 몫이다.
    여기서는 어긋난 항목만 돌려주며 일치한다고 도착·승인으로 바꾸지 않는다.
    """
    if type(handoff) is not Handoff or type(observed) is not dict:
        return Mismatch(('missing-observation',), compared=False)
    expectation = handoff.expectation
    differences = []
    for name in ('provider', 'order_reference', 'reserve_id', 'currency',
                 'origin', 'destination', 'carrier', 'flight', 'date', 'family'):
        if name not in observed:
            differences.append(name)
        elif observed[name] != getattr(expectation, name):
            differences.append(name)
    if 'amount' not in observed:
        differences.append('amount')
    else:
        seen = observed['amount']
        # 문자열/정수 표기 차이는 값으로 비교하고 형이 이상하면 어긋난 것으로 둔다.
        try:
            if isinstance(seen, bool) or Decimal(str(seen)) != expectation.amount:
                differences.append('amount')
        except (ValueError, ArithmeticError, TypeError):
            differences.append('amount')
    return Mismatch(tuple(differences))


# ---------------------------------------------------------------------------
# D1 동일 주문 인계: 화면이 쓰는 주문 참조와 API 주문의 연결 판정
#
# 근거(수집 20260912-232135-f66cf6b8, 정상 UI 주문 2회): 게이트 문서가 자기
# inputTravellers 응답을 받은 직후(약 0.09초) 보내는 baggagePolicies **GET** 요청의
# 쿼리 orderId 가 그 응답 pnr 과 같은 익명 참조였고, 두 주문의 참조는 서로 달랐다.
# 한계: API 재전송 주문 뒤 게이트를 다시 열었을 때 이 요청이 나오는지·어느
# 주문을 가리키는지는 관측하지 않았다. 그래서 참조가 안 보이면 성공이 아니다.
# 날짜·마일리지·KRW 문구는 두 주문이 같을 수 있어(9/13 08:17) 판정에 쓰지 않는다.
# ---------------------------------------------------------------------------
SITE_ORIGIN = 'https://www.koreanair.com'
ORDER_PATH = '/api/ap/booking/traveller/inputTravellers'
# 인계 중에 나오면 선택·운임을 다시 시작한 것이다. 주문 요청은 아니지만 실패로 둔다.
SELECTION_PATHS = ('/api/ap/booking/avail/awardAvailability',
                   '/api/ap/booking/avail/fareInformation')
NPAY_SESSION_PATH = '/api/pp/payment/NaverPay'
# 화면 주문 참조의 출처. 경로 → (메서드, 위치, 이름). 관측 밖 형태는 읽지 않는다.
#  baggagePolicies GET 쿼리 orderId: 수집 f66cf6b8 정상 UI 2/2(원본 있음).
#  NaverPay POST 본문 reservationRecLoc: 9/10 수집 113950 문서 기록(원본이 이 PC에 없음).
#    본문 안 위치를 모르므로 JSON 전체에서 같은 이름을 찾고 모두 같아야 읽힌 것으로 본다.
REFERENCE_SOURCES = {'/api/et/ibeSupport/baggagePolicies': ('GET', 'query', 'orderId'),
                     NPAY_SESSION_PATH: ('POST', 'json', 'reservationRecLoc')}


def _json_values(data, name, depth=0, out=None):
    out = [] if out is None else out
    if depth > 8:
        return out
    if type(data) is dict:
        for key, value in data.items():
            if key == name:
                out.append(value)
            _json_values(value, name, depth + 1, out)
    elif type(data) is list:
        for item in data[:50]:
            _json_values(item, name, depth + 1, out)
    return out


def reference_in(method, url, body=None):
    """요청에서 주문 참조를 읽는다. (값, 읽힘) 을 돌려주며 원문을 기록하지 않는다.

    출처별 메서드·위치가 다르면 읽지 않고 실패로 둔다(GET 참조를 POST 본문에서 찾지 않는다).
    """
    if not isinstance(url, str):
        return None, False
    parts = urlsplit(url)
    source = REFERENCE_SOURCES.get(parts.path)
    if source is None:
        return None, True
    want_method, where, name = source
    if method != want_method:
        return None, False
    if where == 'query':
        values = parse_qs(parts.query, keep_blank_values=True).get(name, [])
    else:
        try:
            values = _json_values(json.loads(body), name) if isinstance(body, str) else []
        except ValueError:
            values = []
    if (not values or not all(isinstance(v, str) and v.strip() for v in values)
            or len(set(values)) != 1):
        return None, False
    return values[0], True


@dataclass(frozen=True)
class ScreenOrder:
    """인계 중 관측한 요청으로 본 화면 주문 판정. 원문 참조는 담지 않는다."""
    state: str
    references_seen: int = 0
    references_matched: int = 0
    other_page_references: int = 0
    order_requests: int = 0
    order_requests_unblocked: int = 0
    selection_requests: int = 0
    stale_ignored: int = 0
    # 일치한 참조 출처 경로. 결제 세션(NaverPay)이 같은 주문인지 따로 볼 때 쓴다.
    matched_sources: tuple = ()
    # 참조 일치만 뜻한다. 화면 전체 상태·동의·Npay 도착은 따로 확인한다.
    same_reference: bool = False
    screen_state_verified: bool = False
    payment_window_reached: bool = False
    # 차단하지 못한 주문 요청이 있었으면 서버 주문이 늘었을 수 있다.
    extra_order_possible: bool = False


_RECORD_TYPES = {'path': str, 'origin': str, 'readable': bool, 'blocked': bool,
                 'target': bool, 'document': int}


def judge_screen_order(reference, *, ordered_at, started, requests, now):
    """API 주문 참조와 인계 중 화면이 보낸 요청을 대조한다.

    `requests` 는 인계 관찰기가 모은 기록이다: path·origin·at·reference(읽은 값
    또는 None)·readable·blocked·target(인계 대상 페이지 주 프레임)·document(관찰
    시작 뒤 그 페이지의 주 프레임 이동 횟수, 대상이 아니면 -1).
    인계 시작 전 기록은 이전 화면의 것이라 무시한다. 주문·선택 요청은 어느 탭에서
    나와도 실패다. 주문 참조는 대상 페이지가 이동한 뒤의 요청만 판정에 쓴다.
    같은 날짜·마일리지라도 다른 참조가 보이면 거부한다.
    """
    if not isinstance(reference, str) or not reference.strip():
        return ScreenOrder('missing-reference')
    if not _finite(ordered_at, started, now):
        return ScreenOrder('invalid-time')
    if started < ordered_at:
        # 이 주문 응답보다 먼저 시작한 관찰은 다른 주문을 섞을 수 있다.
        return ScreenOrder('handoff-before-order')
    if now < started:
        return ScreenOrder('invalid-time')
    if type(requests) not in (list, tuple):
        return ScreenOrder('invalid-observation')
    window, stale = [], 0
    for rec in requests:
        if (type(rec) is not dict
                or any(type(rec.get(k)) is not t for k, t in _RECORD_TYPES.items())
                or not _finite(rec.get('at'))
                or not (rec.get('reference') is None or isinstance(rec.get('reference'), str))):
            return ScreenOrder('invalid-observation')
        if rec['at'] < started:
            stale += 1
            continue
        if rec['at'] > now:
            return ScreenOrder('invalid-time')
        window.append(rec)
    orders = [r for r in window if r['path'] == ORDER_PATH]
    selections = [r for r in window if r['path'] in SELECTION_PATHS]
    all_refs = [r for r in window if r['path'] in REFERENCE_SOURCES]
    # 다른 탭·이동 전 문서의 참조는 이 화면이 무엇을 띄웠는지 말해 주지 않는다.
    refs = [r for r in all_refs if r['target'] and r['document'] >= 1]
    matched = [r for r in refs if r['readable'] and r['reference'] == reference]
    counts = dict(references_seen=len(refs), references_matched=len(matched),
                  matched_sources=tuple(sorted({r['path'] for r in matched})),
                  other_page_references=len(all_refs) - len(refs),
                  order_requests=len(orders),
                  order_requests_unblocked=sum(not r['blocked'] for r in orders),
                  selection_requests=len(selections), stale_ignored=stale)
    counts['extra_order_possible'] = counts['order_requests_unblocked'] > 0
    if orders:
        state = 'new-order-requested' if counts['extra_order_possible'] else 'new-order-blocked'
        return ScreenOrder(state, **counts)
    if selections:
        return ScreenOrder('selection-request', **counts)
    if any(not r['readable'] for r in refs):
        return ScreenOrder('reference-unreadable', **counts)
    if len(matched) != len(refs):
        return ScreenOrder('other-order', **counts)
    if not any(r['origin'] == SITE_ORIGIN for r in matched):
        return ScreenOrder('reference-unobserved', **counts)
    return ScreenOrder('same-order-reference', same_reference=True, **counts)
