"""메모리에서만 여정·금액을 대조하고 결과 불리언만 저장한다. 주문 실행 기능 없음."""
from decimal import Decimal, InvalidOperation


def money(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except InvalidOperation:
        return None


def order_amount_diagnostic(body, quote):
    """주문 판정 실패 원인만 보존한다. 원문·예약번호·토큰은 반환하지 않는다."""
    import json
    try:
        data = json.loads(body) if isinstance(body, str) else None
    except (ValueError, TypeError):
        data = None
    if not isinstance(data, dict):
        return {'responseObject': False}
    fare = data.get('pnrFareInfo')
    result = {'responseObject': True,
              'referencePresent': isinstance(data.get('pnr'), str) and bool(data['pnr'].strip()),
              'fareObject': isinstance(fare, dict)}
    if isinstance(fare, dict):
        result['currencyMatches'] = fare.get('currency') == quote.target.currency
        result['values'] = {}
        for key, expected in (('amount', quote.amount), ('totalAmount', quote.total_amount),
                              ('mileage', quote.mileage)):
            actual = money(fare.get(key))
            result['values'][key] = {'fare': str(expected),
                                    'order': str(actual) if actual is not None else None,
                                    'matches': actual == expected}
    return result


def itinerary(data):
    bounds = data.get('boundList') if isinstance(data, dict) else None
    if not isinstance(bounds, list) or not bounds:
        return None
    result = []
    for bound in bounds:
        if not isinstance(bound, dict):
            return None
        segments = bound.get('segmentList')
        if not isinstance(segments, list) or not segments:
            return None
        for segment in segments:
            if not isinstance(segment, dict):
                return None
            values = tuple(segment.get(k) for k in
                           ('departureAirport','arrivalAirport','flightNumber','cabinClass','fareFamily'))
            date = segment.get('departureDateTime') or (bound.get('departureDateTime') if len(segments)==1 else None)
            if not all(isinstance(v,str) and v for v in values) or not isinstance(date,str) or len(date)<8:
                return None
            result.append((*values, date))
    return result


class FlowEvidence:
    def __init__(self, target=None):
        self.target = target or {}
        self.fare = None
        self.order = None

    def begin(self, path):
        if path.endswith('/fareInformation'):
            self.fare = self.order = None
        elif path.endswith('/inputTravellers'):
            self.order = None

    def observe(self, path, phase, data, request_id, http_status=None):
        if not isinstance(data, dict):
            if phase=='response' and path.endswith('/fareInformation'):
                self.fare = self.order = None
            elif phase=='response' and path.endswith('/inputTravellers'):
                self.order = None
            return {'businessMeaning':'unverified'}
        result = {'businessMeaning':'unverified', 'seatHoldTime':None}
        if phase == 'response':
            result['errorFieldPresent'] = any(data.get(k) for k in ('error','errors','errorCode','errorList','code'))
        if phase == 'response' and path.endswith(('/fareInformation','/inputTravellers')):
            journey = itinerary(data)
            fare = data.get('pnrFareInfo')
            fare = fare if isinstance(fare,dict) else {}
            current = {'requestId':request_id,'itinerary':journey,'amount':money(fare.get('amount')),
                       'totalAmount':money(fare.get('totalAmount')),'mileage':money(fare.get('mileage')),
                       'currency':fare.get('currency'),'pnr':data.get('pnr')}
            result['itineraryFieldsComplete'] = journey is not None
            result['targetRouteDateMatch'] = (len(journey)==1 and journey[0][0]==self.target.get('origin')
                and journey[0][1]==self.target.get('destination')
                and journey[0][-1][:8]==self.target.get('date','').replace('-','')) if journey and self.target else None
            result['targetCurrencyMatch'] = fare.get('currency')==self.target.get('currency') if self.target and fare.get('currency') else None
            result['targetCabinVerified'] = False
            if path.endswith('/inputTravellers'):
                for key in ('itinerary','amount','totalAmount','mileage','currency'):
                    result[key+'MatchesFare'] = (current[key]==self.fare[key]) if self.fare and current[key] is not None and self.fare[key] is not None else None
                result['fareRequestId'] = self.fare['requestId'] if self.fare else None
                self.order = current if http_status==200 and not result['errorFieldPresent'] else None
            else:
                self.fare = current if http_status==200 and not result['errorFieldPresent'] else None
                self.order = None  # 새 운임 뒤 과거 주문을 결제 연결 증거로 쓰지 않는다.
        if phase == 'request' and path.endswith('/NaverPay'):
            result['orderRequestId'] = self.order['requestId'] if self.order else None
            result['sameOrderReference'] = bool(self.order['pnr']==data.get('reservationRecLoc')) if self.order and isinstance(self.order['pnr'],str) and self.order['pnr'] else None
            for key in ('amount','totalAmount'):
                result['paymentAmountMatchesOrder'+key[0].upper()+key[1:]] = (money(data.get('amount'))==self.order[key]) if self.order and self.order[key] is not None and money(data.get('amount')) is not None else None
            result['currencyMatchesOrder'] = data.get('currency')==self.order['currency'] if self.order and self.order['currency'] else None
        return result
