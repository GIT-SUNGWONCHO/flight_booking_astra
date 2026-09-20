"""사용자가 9/14 허용한 주문 증거만 저장. 원문 응답·승객정보·세션·결제 토큰 제외."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
from evidence import money, error_detail
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
        'serverOrderCreatedAt':None, # 서버 생성 시각 원문은 저장하지 않는다(필드 계약 미확인)
        'serverCreated':server_created(body, response.get('startedAt')),
        'orderRequestStartedAt':iso(response.get('startedAt')),
        'orderResponseReceivedAt':iso(received_at),
        'passengerFingerprint':text_field(passenger_fingerprint,r'[a-f0-9]{64}'),
        'diagnostic':{'state':'received','responseObject':type(body) is dict,
            'httpStatus':response.get('status') if type(response.get('status')) is int and 100<=response['status']<=599 else None,
            'currencyMatches':fare.get('currency')==quote.target.currency,'values':comparisons,
            # 예약번호가 없을 때만 업무 오류 코드·메시지(정제)를 남긴다(2026-09-19 승인).
            'error':error_detail(data) if not data.get('pnr') else None}}

def server_created(body, started_at):
    """주문 응답의 생성 시각 **정밀도와 우리 송신 대비 간격**만 뽑는다(원문 저장 없음).

    9/13 관측: `createDateTime`·`createDateTimeOfKST` 가 있고 한국시각 필드의 초가 00 이었다.
    초 단위 값이 있으면 '주문 송신 → 서버가 예약을 만든 시각'을 직접 잴 수 있으므로 정밀도를 남긴다.
    파싱 실패·필드 없음은 None 이며 판정에 쓰지 않는다.
    """
    if type(body) is not dict:
        return None
    out={}
    for key in ('createDateTime','createDateTimeOfKST'):
        raw=body.get(key)
        if not isinstance(raw,str) or not raw.strip():
            continue
        digits=re.sub(r'\D','',raw)
        row={'length':len(raw),'digits':len(digits)}
        if len(digits)>=14:
            row['precision']='minute' if digits[12:14]=='00' else 'second'
            if len(digits)>14:
                row['precision']='sub-second'
            try:
                moment=datetime.strptime(digits[:14],'%Y%m%d%H%M%S')
                if type(started_at) in (int,float) and math.isfinite(started_at):
                    # 서버 시각과 로컬 시각의 차이는 별도 측정 대상이다. 여기서는 초 단위 간격만 남긴다.
                    row['secondsFromRequest']=round(moment.timestamp()-float(started_at),1)
            except ValueError:
                row['precision']='unparsed'
        else:
            row['precision']='unknown-format'
        out[key]=row
    return out or None


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
