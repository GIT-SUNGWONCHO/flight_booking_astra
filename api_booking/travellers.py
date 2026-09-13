"""A4b inputTravellers 요청 구성과 A4c 응답 판정.

전송·A1 상태 변경·영속 저장은 하지 않는다. 응답 배치는 합성 계약이며
문서에 관측으로 기록된 pnr 문자열과 boundList 구간 status=HK 만 근거다.
"""
from dataclasses import dataclass, field
from datetime import datetime
import json
import math

from eligibility import judge as judge_eligibility
from evidence import money
from fare import Quote

PATH = '/api/ap/booking/traveller/inputTravellers'


@dataclass(frozen=True)
class Draft:
    subject: str = field(repr=False)
    session: str = field(repr=False)
    body_json: str = field(repr=False)
    identity_checked: bool = False
    contact_checked: bool = False


def prepare_draft(subject, session, template, *, identity_checked=False, contact_checked=False):
    """개인정보 세부 필드는 추측하지 않으며 판정 플래그는 호출자 책임이다."""
    if (not all(isinstance(x, str) and x.strip() for x in (subject, session))
            or type(template) is not dict
            or set(template) != {'travellerInfoList','contactList','preferLanguage'}):
        raise ValueError('unsupported-draft')
    # 승객은 1명(본인)만 다룬다. 연락처는 9/12 실측 주문 요청 2회 모두 2항목이었다(D3).
    counts = {'travellerInfoList': (1,), 'contactList': (1, 2)}
    for key, allowed in counts.items():
        items = template[key]
        if (type(items) is not list or len(items) not in allowed
                or any(type(item) is not dict or not item for item in items)):
            raise ValueError('unsupported-draft-shape')
    if not isinstance(template['preferLanguage'], str) or not template['preferLanguage'].strip():
        raise ValueError('missing-language')
    try:
        body = json.dumps(template, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise ValueError('invalid-draft-json') from None
    return Draft(subject, session, body, identity_checked, contact_checked)


@dataclass(frozen=True)
class Request:
    body_json: str = field(repr=False)
    quote_generation: str
    availability_generation: str
    subject: str = field(repr=False)
    session: str = field(repr=False)
    page_ticket: str = field(repr=False)
    created: float
    path: str = PATH
    method: str = 'POST'
    offline_only: bool = True
    wire_contract_verified: bool = False


def build_request(quote, evidence, draft, *, target, session, subject,
                  fare_generation, availability_generation, now, max_age=5.0):
    validation = judge_eligibility(quote, evidence, target=target, session=session, subject=subject,
        fare_generation=fare_generation, availability_generation=availability_generation,
        now=now, max_age=max_age)
    if not validation.ready:
        raise ValueError(validation.state)
    if (type(draft) is not Draft or draft.subject != subject or draft.session != session
            or draft.identity_checked is not True or draft.contact_checked is not True):
        raise ValueError('unverified-draft-context')
    # 직접 만든 Draft도 동일한 구조 검사를 거친다. 개인정보는 오류에 출력하지 않는다.
    try:
        checked = prepare_draft(subject, session, json.loads(draft.body_json),
            identity_checked=True, contact_checked=True)
    except (ValueError, TypeError):
        raise ValueError('invalid-draft') from None
    # pageTicket의 실제 헤더/본문 위치는 미확인. 문맥 메타데이터로만 보존한다.
    return Request(checked.body_json, quote.generation, quote.availability_generation,
                   subject, session, quote.page_ticket, now)


@dataclass(frozen=True)
class Order:
    """실행 내 메모리 판정 결과. 좌석 보유·발권 확정이 아니다."""
    quote: Quote = field(repr=False)
    subject: str = field(repr=False)
    session: str = field(repr=False)
    reference: str = field(repr=False)  # 응답 pnr. 파일·문서에 원문을 남기지 않는다
    request_created: float
    received: float
    segment_status: str | None = None  # 관측된 HK만 보존. 보유 의미는 미확정
    amounts_matched: bool = False      # 응답에 금액이 없으면 False로 남는다


@dataclass(frozen=True)
class Outcome:
    state: str
    order: Order | None = None
    # 어떤 상태에서도 좌석 확보를 판정하지 않는다. HK 관측도 근거가 아니다.
    seat_hold_verified: bool = False
    # 이 판정기는 이미 전송된 응답만 다룬다. 오프라인에서 주문 미생성을 입증할
    # 수단이 없으므로 어떤 경로에서도 False 로 내리지 않는다. README 의
    # '부재 응답 한 번으로 미생성 판정하지 않음'과 같은 기준이다.
    order_possible: bool = True


_ERROR_KEYS = ('error','errors','code','errorCode','errorList','resultCode',
               'errorMessage','responseCode','responseMessage')


def judge(request, status, payload, *, quote, target, session, subject, now):
    """A4c inputTravellers 응답 판정. 전송·A1 상태 변경·영속 저장은 하지 않는다.

    주문을 만드는 요청이므로 '실패'와 '불명'을 구분한다. 어떤 상태도 주문
    미생성의 증거가 아니다. 업무 오류 필드나 로컬 문맥 검증 실패 역시 서버가
    주문을 만들지 않았다는 계약이 확인된 바 없으므로 order_possible 을 유지한다.
    반환된 state 는 이 실행을 중단할지 판단하는 용도이며 재전송 허가가 아니다.
    """
    if type(request) is not Request or type(quote) is not Quote:
        return Outcome('missing-context')
    if (not isinstance(subject, str) or not subject.strip()
            or not isinstance(session, str) or not session.strip()
            or request.subject != subject or request.session != session
            or quote.session != session or quote.target != target
            or quote.generation != request.quote_generation
            or quote.availability_generation != request.availability_generation):
        return Outcome('context-mismatch')
    if not all(type(v) in (int, float) and math.isfinite(v) for v in (now, request.created)):
        return Outcome('invalid-time')
    if now < request.created:
        return Outcome('invalid-time')
    # 여기서부터는 요청이 나갔을 수 있는 구간이다.
    if status is None:
        return Outcome('order-unknown')
    if type(status) is not int or isinstance(status, bool):
        return Outcome('order-unknown')
    if status != 200:
        return Outcome('http-error')
    if isinstance(payload, (str, bytes)):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            # 200 인데 읽을 수 없다. 주문 생성 여부를 단정하지 않는다.
            return Outcome('order-unknown')
    if type(payload) is not dict:
        return Outcome('order-unknown')
    if (any(payload.get(k) not in (None, '', False, [], {}) for k in _ERROR_KEYS)
            or payload.get('success') is False or payload.get('isSuccess') is False):
        return Outcome('business-error')
    bounds = payload.get('boundList')
    if type(bounds) is not list or len(bounds) != 1 or type(bounds[0]) is not dict:
        return Outcome('invalid-itinerary')
    segments = bounds[0].get('segmentList')
    if type(segments) is not list or len(segments) != 1 or type(segments[0]) is not dict:
        return Outcome('invalid-itinerary')
    leg = segments[0]
    expected = dict(departureAirport=target.origin, arrivalAirport=target.destination,
                    flightNumber=target.flight, operationCarrierCode=target.carrier,
                    fareFamily=target.family)
    if any(leg.get(k) != v for k, v in expected.items()):
        return Outcome('target-mismatch')
    date = leg.get('departureDateTime')
    try:
        if not isinstance(date, str) or len(date) != 14:
            raise ValueError()
        datetime.strptime(date, '%Y%m%d%H%M%S')
    except ValueError:
        return Outcome('invalid-date')
    if date[:8] != target.date.replace('-', ''):
        return Outcome('target-mismatch')
    # 금액은 이 응답에서 관측된 적이 없다. 있으면 대조하고, 없으면 미확인으로 남긴다.
    matched = False
    fare = payload.get('pnrFareInfo')
    if fare is not None:
        if type(fare) is not dict or fare.get('currency') != target.currency:
            return Outcome('currency-mismatch')
        amount, total, mileage = (money(fare.get(k)) for k in ('amount','totalAmount','mileage'))
        if None in (amount, total, mileage):
            return Outcome('invalid-amounts')
        if (amount, total, mileage) != (quote.amount, quote.total_amount, quote.mileage):
            return Outcome('amount-mismatch')
        matched = True
    reference = payload.get('pnr')
    if not isinstance(reference, str) or not reference.strip():
        # 주문이 만들어졌는데 번호를 못 읽었을 수 있다. 재전송으로 넘기지 않는다.
        return Outcome('missing-reference')
    # 관측된 HK 외의 상태 문자열은 해석하지 않고 버린다.
    observed = leg.get('status')
    observed = observed if observed == 'HK' else None
    return Outcome('order-recorded', Order(quote, subject, session, reference,
                                           request.created, now, observed, matched))
