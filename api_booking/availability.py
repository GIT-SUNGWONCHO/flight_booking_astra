"""A2 오프라인 계약 모델. 실제 HTTP/브라우저 전송 구현 없음.

입력 템플릿의 구체 필드/헤더 계약은 합성 fixture 수준이다. 실사이트 연결 금지.
단일 편도·직항·비공동운항만 지원하고 불완전/모호한 응답은 차단한다.

D3 실측 대조(수집 20260912-232135-f66cf6b8, 허용목록 기반 구조):
- 요청 currency 는 세 번 모두 KRW/USD 가 아닌 문자열이었다 → 템플릿 currency 를 요구하지 않는다.
- 응답 flightInfoList[0] 에 flightNumber·operationCarrierCode·departure/arrivalAirport·
  departureDateTime·codeShare 가 있었다. carrierCode·항공편 soldOut 은 허용목록 밖이라
  9/12 에는 확인되지 않았고 8/27 fixture 에만 있다 → 있으면 대조, 없으면 통과.
- 9/13 실발사 5회는 flightInfoList[0].departureDateTime 앞 8자리로 날짜를 맞췄다.
"""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
import json
import math
import re
from uuid import uuid4

PATH = '/api/ap/booking/avail/awardAvailability'


@dataclass(frozen=True)
class Target:
    date: str
    origin: str
    destination: str
    family: str
    carrier: str
    flight: str
    currency: str = 'KRW'

    def __post_init__(self):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', self.date):
            raise ValueError('invalid-date')
        datetime.strptime(self.date, '%Y-%m-%d')
        if (not all(re.fullmatch(r'[A-Z]{3}', x) for x in (self.origin, self.destination))
                or self.origin == self.destination
                or self.family not in ('KEBONUSEY', 'KEBONUSPR')
                or not re.fullmatch(r'[A-Z0-9]{2}', self.carrier)
                or not re.fullmatch(r'\d{1,4}', self.flight)
                or self.currency != 'KRW'):
            raise ValueError('unsupported-target')


@dataclass(frozen=True)
class Request:
    target: Target
    session: str = field(repr=False)
    generation: str
    created: float
    body: dict = field(repr=False)
    headers: dict = field(repr=False)
    path: str = PATH
    method: str = 'POST'
    credentials: str = 'include'
    offline_only: bool = True


def build_request(target, template, headers, session, now):
    """승객/추가 필드는 템플릿 보존. 토큰 생성·갱신·헤더 추측은 하지 않는다."""
    if (type(template) is not dict or type(headers) is not dict
            or not isinstance(session, str) or not session
            or type(now) not in (int, float) or not math.isfinite(now)):
        raise ValueError('invalid-context')
    segments = template.get('segmentList')
    # currency 값은 템플릿 그대로 둔다. 실측 요청 값이 KRW 문자열이 아니었다(D3).
    if (type(segments) is not list or len(segments) != 1 or type(segments[0]) is not dict
            or not all(k in segments[0] for k in ('departureDate', 'departureAirport', 'arrivalAirport'))
            or not isinstance(template.get('currency'), str) or not template.get('travelers')):
        raise ValueError('unverified-template-shape')
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ValueError('invalid-headers')
    body = deepcopy(template)
    body['segmentList'][0].update(departureDate=target.date.replace('-', ''),
        departureAirport=target.origin, arrivalAirport=target.destination)
    return Request(target, session, uuid4().hex, now, body, deepcopy(headers))


@dataclass(frozen=True)
class Selection:
    generation: str
    session: str = field(repr=False)
    target: Target
    recommend_id: str = field(repr=False)
    flight_id: str = field(repr=False)
    received: float


@dataclass(frozen=True)
class Result:
    state: str
    selection: Selection | None = None
    seat_hold_verified: bool = False


def _datetime14(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{14}', value):
        return False
    try:
        datetime.strptime(value, '%Y%m%d%H%M%S')
    except ValueError:
        return False
    return True


def judge(request, status, payload, generation, session, now, max_age=5.0):
    """max_age는 로컬 선택 만료 정책이며 서버 토큰의 유효기간을 뜻하지 않는다."""
    if (generation != request.generation or session != request.session
            or type(now) not in (float, int) or not math.isfinite(now)
            or type(max_age) not in (float, int) or not math.isfinite(max_age)
            or max_age <= 0 or not 0 <= now - request.created <= max_age):
        return Result('stale-context')
    if type(status) is not int or status != 200:
        return Result('http-error')
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return Result('invalid-json')
    if type(payload) is not dict:
        return Result('invalid-schema')
    # ERT.10032는 달력의 미개방 관측 코드. 여기서는 보수적 차단용이며
    # awardAvailability에서도 같은 의미라는 실사이트 증거는 아직 없다.
    if any(payload.get(k) == 'ERT.10032' for k in ('code', 'errorCode')):
        return Result('not-open')
    if (any(payload.get(k) not in (None, '', False, [], {})
            for k in ('error', 'errors', 'code', 'errorCode', 'errorList', 'resultCode',
                      'errorMessage', 'responseCode', 'responseMessage'))
            or payload.get('success') is False or payload.get('isSuccess') is False):
        return Result('business-error')
    t = request.target
    if payload.get('emptyFare') is True:
        return Result('no-target')
    bounds = payload.get('upsellBoundAvailList')
    if payload.get('currency') != t.currency:
        return Result('currency-mismatch')
    if type(bounds) is not list or len(bounds) != 1 or type(bounds[0]) is not dict:
        return Result('invalid-schema')
    flights = bounds[0].get('availFlightList')
    if type(flights) is not list or not flights:
        return Result('no-target')
    matches = []
    for flight in flights:
        if type(flight) is not dict:
            return Result('invalid-schema')
        info = flight.get('flightInfoList')
        if type(info) is not list or len(info) != 1 or type(info[0]) is not dict:
            continue
        leg = info[0]
        # 날짜는 실발사에서 쓴 구간 출발시각을 기준으로 하고, 항공편 단위 날짜가 있으면 일치해야 한다.
        dates = [leg.get('departureDateTime')]
        if 'departureDate' in flight:
            dates.append(flight.get('departureDate'))
        if not all(_datetime14(d) for d in dates) or len({d[:8] for d in dates}) != 1:
            continue
        date = dates[0]
        if (date[:8] != t.date.replace('-', '')
                or flight.get('departureAirport') != t.origin
                or flight.get('arrivalAirport') != t.destination
                or any(k in leg and leg.get(k) != v for k, v in
                       (('departureAirport', t.origin), ('arrivalAirport', t.destination),
                        ('carrierCode', t.carrier)))
                or leg.get('operationCarrierCode') != t.carrier
                or leg.get('flightNumber') != t.flight
                or leg.get('codeShare') is not False):
            continue
        fares = flight.get('commercialFareFamilyList')
        if type(fares) is not list or any(type(f) is not dict for f in fares):
            return Result('invalid-schema')
        matches.extend((flight, fare) for fare in fares if fare.get('fareFamily') == t.family)
    if not matches:
        return Result('no-target')
    if len(matches) != 1:
        return Result('ambiguous-target')
    flight, fare = matches[0]
    count = fare.get('seatCount')
    if flight.get('soldOut') is True or fare.get('soldout') is True or count in ('0', 0):
        return Result('sold-out')
    # 등급 soldout=false 와 양수 seatCount 는 9/12 실측 필드다. 항공편 soldOut 은 있으면 false 여야 한다.
    if (('soldOut' in flight and flight.get('soldOut') is not False)
            or fare.get('soldout') is not False
            or not isinstance(count, str) or not re.fullmatch(r'[1-9]\d*', count)):
        return Result('unverified-stock')
    ids = (fare.get('recommendId'), flight.get('flightId'))
    if not all(isinstance(x, str) and x.strip() for x in ids):
        return Result('missing-identifiers')
    return Result('selected', Selection(request.generation, request.session, t, *ids, now))
