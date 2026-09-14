"""검증된 응답으로 사이트 저장 모델을 구성하는 후보. 운영 실행기에 아직 연결하지 않음.

9/10 저장 소스의 sessionStorage rehydrate 계약을 따른다. 같은 날짜·노선의 준비만
허용하며, 타 날짜 캡처를 목표 날짜로 바꾸거나 동의/인증 상태를 만들지 않는다.
Python 단계는 메모리에서 처리하며 repr에 원문을 넣지 않는다. 격리 브라우저에
적용할 때는 응답을 sessionStorage에 저장한다. 일반 파일·로그로 출력하지 않는다.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
import json
import math

import travellers

KEYS = ('fareInformation', 'inputTravellers', 'resvStatus', 'keAirSearchCriteria')


@dataclass(frozen=True, repr=False)
class StoragePatch:
    before: dict = field(repr=False)
    after: dict = field(repr=False)
    # 성공/좌석 확보 플래그가 아니다. 로컬 후보임을 유지한다.
    offline_only: bool = True


def _object(raw):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ValueError('invalid-json') from None
    if type(value) is not dict:
        raise ValueError('invalid-object')
    return value


def validate_prepared_storage(storage, target):
    """주문 전에도 확인 가능한 준비 상태. 불일치를 주문 뒤에 처음 발견하지 않는다."""
    if type(storage) is not dict or set(storage) != set(KEYS):
        raise ValueError('missing-stores')
    states = {k: _object(storage[k]) for k in KEYS}
    status = states['resvStatus']
    if (status.get('processType') != 'RT' or status.get('flowType') != 'NR'
            or status.get('isLoginExpired') is not False
            or status.get('isSessionExpired') is not False
            or status.get('isPNRConfirmed') is not False
            or status.get('resData') is not None
            or status.get('agreeStatus') is not None):
        raise ValueError('unready-context')
    bounds = states['keAirSearchCriteria'].get('bounds')
    if type(bounds) is not list or len(bounds) != 1 or type(bounds[0]) is not dict:
        raise ValueError('unsupported-search')
    bound = bounds[0]
    date = bound.get('departureDateTime')
    try:
        parsed = datetime.fromisoformat(date) if isinstance(date, str) else None
        # 현재 승인된 Windows/KST 시험: 달력의 KST 자정은 UTC 전날 15시로도 저장된다.
        if parsed is not None and parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone(timedelta(hours=9)))
    except ValueError:
        parsed = None
    # 실측: 서울/모든 공항은 sel + CTY + KR로 저장된다. 이것은 ICN 선택 증거가
    # 아니라 넓은 검색 조건이다. 실제 공항은 아래 응답 판정에서 계속 ICN과 정확히 대조한다.
    origin_matches=(bound.get('originLocationCode') == target.origin or
        (target.origin=='ICN' and bound.get('originLocationCode')=='sel'
         and bound.get('originLocationAirportType')=='CTY'
         and bound.get('originLocationCountryCode')=='KR'))
    if (parsed is None or parsed.date().isoformat() != target.date
            or not origin_matches
            or bound.get('destinationLocationCode') != target.destination):
        raise ValueError('search-target-mismatch')
    for name in KEYS[:2]:
        if set(states[name]) != {'model', 'errors', 'isPending', 'isFailure'}:
            raise ValueError('unsupported-store-shape')
        if states[name]['isPending'] is not False:
            raise ValueError('pending-store')
    if states['inputTravellers']['model'] is not None:
        raise ValueError('existing-order-store')
    return states


def build_patch(storage, *, request, quote, fare_response, order_response, now,
                allow_observed_amount_layout=False):
    """응답 계약을 재검증하고 두 저장 모델만 반환한다. 브라우저·서버에 접근하지 않는다."""
    if type(request) is not travellers.Request or type(quote) is not travellers.Quote:
        raise ValueError('missing-context')
    if not all(type(v) in (int, float) and math.isfinite(v)
               for v in (now, request.created, quote.received)):
        raise ValueError('invalid-time')
    if not quote.received <= request.created <= now or now - request.created > 30:
        raise ValueError('stale-response')
    target=quote.target
    validate_prepared_storage(storage,target)
    responses = []
    for response in (fare_response, order_response):
        if (type(response) is not dict or response.get('ok') is not True
                or type(response.get('status')) is not int or response['status'] != 200):
            raise ValueError('unverified-response')
        responses.append(_object(response.get('body')))
    fare, order = responses
    if fare.get('pageTicket') != quote.page_ticket:
        raise ValueError('fare-context-mismatch')
    verdict = travellers.judge(request, 200, order, quote=quote, target=target,
        session=quote.session, subject=request.subject, now=now,
        allow_observed_amount_layout=allow_observed_amount_layout)
    if (verdict.state != 'order-recorded' or verdict.order.segment_status != 'HK'
            or not verdict.order.payment_amounts_matched):
        raise ValueError('unverified-order')
    # 동일한 검증기로 운임 응답의 여정·금액도 대조한다. PNR은 이 로컬 비교에만 붙이며
    # 원본 운임 응답이나 사이트에 전달할 모델을 변경하지 않는다.
    fare_check = travellers.judge(request, 200, {**fare, 'pnr': verdict.order.reference},
        quote=quote, target=target, session=quote.session, subject=request.subject, now=now)
    if fare_check.state != 'order-recorded' or not fare_check.order.amounts_matched:
        raise ValueError('unverified-fare')
    after = {name: json.dumps({'model': payload, 'errors': None,
                              'isPending': False, 'isFailure': False})
             for name, payload in zip(KEYS[:2], responses)}
    return StoragePatch(dict(storage), after)


def fixture_apply(page, patch):
    """격리 테스트 도메인 전용 적용기. 대한항공 실사이트에는 적용할 수 없다.

    운영 연결 전에는 현재 문서/회원 세션 결속·이동 중 재주문 차단·앱 재수화를
    별도로 검증해야 한다. 이 함수의 통과는 정상 사이트 인계 성공이 아니다.
    """
    if type(patch) is not StoragePatch:
        raise ValueError('invalid-patch')
    return page.evaluate('''arg => {
      if(location.origin !== 'https://bridge.invalid') return {state:'fixture-only'};
      for(const [k,v] of Object.entries(arg.before))
        if(sessionStorage.getItem(k)!==v) return {state:'context-changed'};
      try {
        for(const [k,v] of Object.entries(arg.after)) sessionStorage.setItem(k,v);
        return {state:'models-written'};
      } catch(e) {
        let restored=true;
        for(const k of Object.keys(arg.after)) {
          try {sessionStorage.setItem(k,arg.before[k]);}catch(_){restored=false;}
        }
        return {state:restored?'write-failed-restored':'write-failed-unrestored'};
      }
    }''', {'before':patch.before, 'after':patch.after})
