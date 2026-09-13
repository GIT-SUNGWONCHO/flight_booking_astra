"""A3 운임 오프라인 계약. 실제 API 전송 없음, 구체 응답 배치는 합성 계약.

pageTicket은 메모리에서만 보존한다. 정상 서버 토큰이라는 판정은 하지 않는다.

D3 실측 대조(수집 20260912-232135-f66cf6b8): 운임 요청은 recommendList 만 있었다
(currency 없음) → 템플릿 currency 를 요구하지 않는다. 응답 cabinClass 값은 기록되지
않았으므로 cabin 기대값이 None 이면 대조하지 않고 등급은 fareFamily 로 판정한다.
업무 오류는 HTTP 200 에 code·status·message 로 온 사례가 있다(요청 39, 옛 식별자).
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
import json
import math
from uuid import uuid4

from availability import Selection, Target
from evidence import money

PATH = '/api/ap/booking/avail/fareInformation'


def fresh(selection, generation, session, target, now, max_age):
    return (type(selection) is Selection and selection.target == target
        and selection.generation == generation and selection.session == session
        and isinstance(session, str) and bool(session)
        and all(type(v) in (float, int) and math.isfinite(v)
                for v in (selection.received, now, max_age))
        and max_age > 0 and 0 <= now - selection.received <= max_age
        and all(isinstance(v, str) and bool(v.strip())
                for v in (selection.recommend_id, selection.flight_id)))


@dataclass(frozen=True)
class Request:
    selection: Selection = field(repr=False)
    cabin: str
    generation: str
    created: float
    max_age: float
    body: dict = field(repr=False)
    headers: dict = field(repr=False)
    path: str = PATH
    method: str = 'POST'
    credentials: str = 'include'
    offline_only: bool = True


def build_request(selection, current_generation, session, target, cabin,
                  template, headers, now, max_age=5.0):
    if not fresh(selection, current_generation, session, target, now, max_age):
        raise ValueError('stale-selection')
    if (type(target) is not Target
            or not (cabin is None or (isinstance(cabin, str) and cabin.strip()))
            or type(template) is not dict or type(headers) is not dict
            or ('currency' in template and template.get('currency') != target.currency)
            or not all(isinstance(k, str) and isinstance(v, str) for k,v in headers.items())):
        raise ValueError('invalid-template-context')
    recommendations = template.get('recommendList')
    if (type(recommendations) is not list or len(recommendations) != 1
            or type(recommendations[0]) is not dict
            or not all(k in recommendations[0] for k in ('recommendId','flightId'))):
        raise ValueError('unverified-template-shape')
    body = deepcopy(template)
    body['recommendList'][0].update(recommendId=selection.recommend_id,
                                    flightId=selection.flight_id)
    return Request(selection, cabin, uuid4().hex, now, max_age, body, deepcopy(headers))


@dataclass(frozen=True)
class Quote:
    target: Target
    generation: str
    availability_generation: str
    session: str = field(repr=False)
    page_ticket: str = field(repr=False)
    amount: Decimal = field(repr=False)
    total_amount: Decimal = field(repr=False)
    mileage: Decimal = field(repr=False)
    received: float
    selection_received: float


@dataclass(frozen=True)
class Result:
    state: str
    quote: Quote | None = None
    seat_hold_verified: bool = False


def judge(request, status, payload, generation, current_availability_generation,
          session, target, now):
    s = request.selection
    if (generation != request.generation
            or not fresh(s, current_availability_generation, session, target, now, request.max_age)
            or now < request.created):
        return Result('stale-context')
    if type(status) is not int or status != 200:
        return Result('http-error')
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return Result('invalid-json')
    if type(payload) is not dict:
        return Result('invalid-schema')
    if (any(payload.get(k) not in (None, '', False, [], {}) for k in
            ('error','errors','code','errorCode','errorList','resultCode',
             'errorMessage','responseCode','responseMessage'))
            or payload.get('success') is False or payload.get('isSuccess') is False):
        return Result('business-error')
    bounds = payload.get('boundList')
    if type(bounds) is not list or len(bounds) != 1 or type(bounds[0]) is not dict:
        return Result('invalid-itinerary')
    segments = bounds[0].get('segmentList')
    if type(segments) is not list or len(segments) != 1 or type(segments[0]) is not dict:
        return Result('invalid-itinerary')
    leg = segments[0]
    expected = dict(departureAirport=target.origin, arrivalAirport=target.destination,
        flightNumber=target.flight, operationCarrierCode=target.carrier,
        fareFamily=target.family)
    if request.cabin is not None:
        expected['cabinClass'] = request.cabin
    if any(leg.get(k) != v for k,v in expected.items()) or leg.get('codeShare') is not False:
        return Result('target-mismatch')
    date = leg.get('departureDateTime')
    try:
        if not isinstance(date, str) or len(date) != 14:
            raise ValueError()
        datetime.strptime(date, '%Y%m%d%H%M%S')
    except ValueError:
        return Result('invalid-date')
    if date[:8] != target.date.replace('-', ''):
        return Result('target-mismatch')
    fare = payload.get('pnrFareInfo')
    if type(fare) is not dict or fare.get('currency') != target.currency:
        return Result('currency-mismatch')
    amount, total, mileage = (money(fare.get(k)) for k in ('amount','totalAmount','mileage'))
    if (None in (amount, total, mileage) or total < amount or mileage <= 0
            or mileage != mileage.to_integral_value()):
        return Result('invalid-amounts')
    ticket = payload.get('pageTicket')
    if not isinstance(ticket, str) or not ticket.strip():
        return Result('missing-page-ticket')
    return Result('validated', Quote(target, request.generation, s.generation, session,
                                    ticket, amount, total, mileage, now, s.received))
