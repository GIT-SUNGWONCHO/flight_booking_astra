"""A4a 필수 검증의 오프라인 판정. 회원 API/주문 전송·서버 자격 추정 없음."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import math

from fare import Quote


@dataclass(frozen=True)
class Evidence:
    quote: Quote = field(repr=False)
    subject: str = field(repr=False)  # 계정 원문이 아닌 실행 내 승객/회원 문맥표지
    observed: float
    expires: float
    member_eligible: bool | None = None
    session_valid: bool | None = None
    available_mileage: object = field(default=None, repr=False)
    own_mileage_only: bool = True


@dataclass(frozen=True)
class Result:
    state: str
    # 판정 시점의 결과일 뿐 주문 권한/보유 증거가 아니다.
    ready: bool = False
    seat_hold_verified: bool = False


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _miles(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        result = Decimal(str(value))
        if result.is_finite() and result >= 0 and result == result.to_integral_value():
            return result
    except InvalidOperation:
        pass
    return None


def judge(quote, evidence, *, target, session, subject, fare_generation,
          availability_generation, now, max_age=5.0):
    if type(quote) is not Quote or type(evidence) is not Evidence:
        return Result('missing-evidence')
    if (not isinstance(subject, str) or not subject.strip()
            or not isinstance(session, str) or not session.strip()
            or quote.target != target or quote.session != session
            or quote.generation != fare_generation
            or quote.availability_generation != availability_generation
            or evidence.subject != subject or evidence.quote != quote):
        return Result('context-mismatch')
    if not all(_finite(v) for v in (now, max_age, quote.selection_received,
                                    quote.received, evidence.observed, evidence.expires)):
        return Result('invalid-time')
    if (max_age <= 0 or not quote.selection_received <= quote.received <= evidence.observed <= now
            or evidence.expires <= now or evidence.expires <= evidence.observed
            or now - quote.selection_received > max_age):
        return Result('expired-evidence')
    if not isinstance(quote.page_ticket, str) or not quote.page_ticket.strip():
        return Result('missing-session-context')
    if evidence.own_mileage_only is not True:
        return Result('unsupported-family-mileage')
    if evidence.member_eligible is False:
        return Result('member-ineligible')
    if evidence.member_eligible is not True:
        return Result('member-unverified')
    if evidence.session_valid is False:
        return Result('session-invalid')
    if evidence.session_valid is not True:
        return Result('session-unverified')
    required, available = _miles(quote.mileage), _miles(evidence.available_mileage)
    if required is None or required <= 0:
        return Result('invalid-required-mileage')
    if available is None:
        return Result('mileage-unverified')
    if available < required:
        return Result('insufficient-mileage')
    return Result('ready-offline', ready=True)
