"""사용자가 9/14 허용한 주문 증거만 저장. 원문 응답·승객정보·세션·결제 토큰 제외."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from evidence import money
import permit

STATES=frozenset(('received','order-recorded','amount-mismatch','currency-mismatch',
    'invalid-amounts','target-mismatch','invalid-itinerary','invalid-date','missing-reference',
    'business-error','http-error','order-unknown','context-mismatch','missing-context',
    'invalid-time','not-ready','segment-status-unverified','order-judge-exception'))

def text_field(value, pattern):
    return value if type(value) is str and re.fullmatch(pattern,value) else None

def numeric(value):
    value=money(str(value)) if type(value).__module__=='decimal' else money(value)
    return str(value) if value is not None else None

def iso(value):
    try:
        if type(value) not in (int,float):return None
        return datetime.fromtimestamp(value,timezone.utc).isoformat()
    except (ValueError,OverflowError,OSError):return None

def receipt(*,run_id,target,response,quote,passenger_fingerprint,received_at):
    if not text_field(run_id,r'[a-f0-9]{12}'):
        raise ValueError('invalid-run-id')
    try:
        body=json.loads(response.get('body',''))
    except (ValueError,TypeError):body=None
    data=body if type(body) is dict else {}
    fare=data.get('pnrFareInfo');fare=fare if type(fare) is dict else {}
    status=None
    bounds=data.get('boundList')
    if type(bounds) is list and len(bounds)==1 and type(bounds[0]) is dict:
        legs=bounds[0].get('segmentList')
        if type(legs) is list and len(legs)==1 and type(legs[0]) is dict:
            status=text_field(legs[0].get('status'),r'[A-Z]{1,4}')
    comparisons={}
    for key,attr in (('amount','amount'),('totalAmount','total_amount'),('mileage','mileage')):
        expected=numeric(getattr(quote,attr,None));actual=numeric(fare.get(key))
        comparisons[key]={'fare':expected,'order':actual,
            'matches':money(expected)==money(actual) if expected is not None and actual is not None else None}
    order_id=data.get('orderId')
    if type(order_id) is int and order_id>=0:order_id=str(order_id)
    return {'runId':run_id,
        'pnr':text_field(data.get('pnr'),r'[A-Za-z0-9_-]{1,128}'),
        'orderId':text_field(order_id,r'[A-Za-z0-9_-]{1,128}'),
        'target':{'date':text_field(target.date,r'\d{4}-\d{2}-\d{2}'),
            'origin':text_field(target.origin,r'[A-Z]{3}'),
            'destination':text_field(target.destination,r'[A-Z]{3}'),
            'carrier':text_field(target.carrier,r'[A-Z0-9]{2}'),
            'flight':text_field(target.flight,r'\d{1,4}'),
            'family':text_field(target.family,r'[A-Z0-9]{1,24}')},
        'segmentStatus':status,'currency':text_field(fare.get('currency'),r'[A-Z]{3}'),
        'totalAmount':numeric(fare.get('totalAmount')),'mileage':numeric(fare.get('mileage')),
        'serverOrderCreatedAt':None, # 서버 생성 시각의 필드 계약은 미확인
        'orderRequestStartedAt':iso(response.get('startedAt')),
        'orderResponseReceivedAt':iso(received_at),
        'passengerFingerprint':text_field(passenger_fingerprint,r'[a-f0-9]{64}'),
        'diagnostic':{'state':'received','responseObject':type(body) is dict,
            'httpStatus':response.get('status') if type(response.get('status')) is int and 100<=response['status']<=599 else None,
            'currencyMatches':fare.get('currency')==quote.target.currency,'values':comparisons}}

def save_received(root, **kwargs):
    record=receipt(**kwargs)
    path=Path(root)/'order-evidence'/record['runId']/'received.json'
    if path.exists():raise ValueError('receipt-already-exists')
    permit.durable_json(path,record)
    return path

def save_judgment(root,run_id,state):
    if not text_field(run_id,r'[a-f0-9]{12}'):raise ValueError('invalid-run-id')
    # 원래 수신 기록을 덮어쓰지 않아 판정 실패나 코드 수정 후에도 대조할 수 있다.
    permit.durable_json(Path(root)/'order-evidence'/run_id/'judgment.json',
        {'runId':run_id,'diagnostic':{'state':state if state in STATES else 'unclassified'}})

def load_received(root,run_id):
    """분석용 로드만 제공. 실행/주문/인계 함수에 연결하지 않는다."""
    if not text_field(run_id,r'[a-f0-9]{12}'):raise ValueError('invalid-run-id')
    result=json.loads((Path(root)/'order-evidence'/run_id/'received.json').read_text(encoding='utf-8'))
    if type(result) is not dict or result.get('runId')!=run_id:raise ValueError('invalid-record')
    return result
