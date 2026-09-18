"""검증된 API 응답→저장 모델→새 결제 문서 연결. 자동 예매 실행기에는 미연결.

bind는 API 발사 전 같은 탭에서 호출한다. 쿠키/회원 저장값 불변은 클라이언트
문맥 확인이며 서버 로그인 유효성 증명이 아니다. 최종 결제나 동의 클릭은 없다.
"""
from dataclasses import dataclass, field
import hashlib
import json
import secrets
import time
import math

from bridge_guard import BridgeGuard
import state_bridge
import site_drive
import resume_gate
import order_evidence
import pipeline

ORIGIN='https://www.koreanair.com'
MARK='__astraBridgeDocument'
# 2026-09-14 bm_s/bm_sv는 달력 무조작 15초, 나머지 둘은 정상 UI 조회 중 회전 관측.
# 삭제/설정/전송 변경을 하지 않는다. 존재 여부는 비교하고 회전하는 값만 제외한다.
ROTATING_COOKIE_VALUES=frozenset(('bm_s','bm_sv',
    'QueueITAccepted-SDFrts345E-V3_awards','_ga_YSSH8WPXW5'))

# 자체 검증 오류만 기록한다. 라이브러리 예외 문자열/URL/응답 원문은 기록하지 않는다.
SAFE_ERRORS=frozenset(('invalid-or-used-binding','changed-context','session-changed',
    'document-changed','storage-changed',
    'missing-session-evidence','missing-context','invalid-time','stale-response',
    'unverified-response','fare-context-mismatch','unverified-order','unverified-fare',
    'missing-stores','unready-context','unsupported-search','search-target-mismatch',
    'unsupported-store-shape','pending-store','existing-order-store','invalid-json','invalid-object'))

def error_summary(exc):
    reason=str(exc) if type(exc) is ValueError and str(exc) in SAFE_ERRORS else 'unclassified'
    frames=[]; tb=exc.__traceback__
    while tb:
        code=tb.tb_frame.f_code
        if code.co_name in ('connect','build_patch','validate_prepared_storage','_session_stamp'):
            frames.append({'function':code.co_name,'line':tb.tb_lineno})
        tb=tb.tb_next
    return {'reason':reason,'valueError':type(exc) is ValueError,'frames':frames}


def _session_stamp(page):
    cookies=page.context.cookies([ORIGIN])
    rows=sorted((c['name'],None if c['name'] in ROTATING_COOKIE_VALUES else c['value'],
                 c['domain'],c['path']) for c in cookies)
    member=page.evaluate('()=>sessionStorage.getItem("loggedInUserInfo")')
    if not rows or member in (None,'null','{}'):
        raise ValueError('missing-session-evidence')
    return hashlib.sha256(json.dumps([rows,member],sort_keys=True).encode()).hexdigest()


@dataclass(repr=False)
class Binding:
    page: object=field(repr=False)
    document: str=field(repr=False)
    stamp: str=field(repr=False)
    storage: dict=field(repr=False)
    session: str=field(repr=False)
    subject: str=field(repr=False)
    created: float
    used: bool=False
    search_date: str|None=None


def bind(page, *, session, subject, now=None, search_date=None):
    if page.url!=site_drive.CALENDAR or not session or not subject:
        raise ValueError('invalid-start-context')
    stamp=_session_stamp(page)
    token=secrets.token_hex(24)
    storage=page.evaluate('''arg=>{
      window[arg.mark]=arg.token;
      return Object.fromEntries(arg.keys.map(k=>[k,sessionStorage.getItem(k)]));
    }''',{'mark':MARK,'token':token,'keys':list(state_bridge.KEYS)})
    return Binding(page,token,stamp,storage,session,subject,time.monotonic() if now is None else now,
                   search_date=search_date)


def preflight(binding, *, target, now=None):
    """주문 전 읽기 전용 점검. 상태 쓰기/이동/요청/시각 갱신을 하지 않는다.

    이 검사는 미래의 주문 응답·게이트 로딩 성공을 보장하지 않으며 주문 후 검사를 대체하지 않는다.
    """
    now=time.monotonic() if now is None else now
    if type(binding) is not Binding or binding.used:
        raise ValueError('invalid-or-used-binding')
    if (type(now) not in (int,float) or not math.isfinite(now)
            or not 0<=now-binding.created<=120 or binding.page.is_closed()
            or binding.page.url!=site_drive.CALENDAR):
        raise ValueError('changed-context')
    if _session_stamp(binding.page)!=binding.stamp:
        raise ValueError('session-changed')
    result=binding.page.evaluate('''arg=>{
      if(window[arg.mark]!==arg.token)return 'document-changed';
      for(const [k,v] of Object.entries(arg.before))
        if(sessionStorage.getItem(k)!==v)return 'storage-changed';
      return 'ready';
    }''',{'mark':MARK,'token':binding.document,'before':binding.storage})
    if result!='ready':
        raise ValueError(result if result in ('document-changed','storage-changed') else 'changed-context')
    state_bridge.validate_prepared_storage(binding.storage,target,binding.search_date)
    return True


def retained_report(binding, request, *, now=None):
    """시간이 지나도 조사 가능한 읽기 진단. 시각/세션 기준을 갱신하지 않는다."""
    report={'bindingPresent':type(binding) is Binding}
    if not report['bindingPresent']:return report
    now=time.monotonic() if now is None else now
    report['bindingUsed']=binding.used
    try:
        age=now-request.created
        report.update(ageSeconds=age,withinResumeWindow=math.isfinite(age) and 0<=age<=30)
        if binding.page.is_closed():
            report['documentMatches']=False
            return report
        current=binding.page.evaluate('''arg=>({
          documentMatches:location.href===arg.url&&window[arg.mark]===arg.token,
          storageMatches:Object.entries(arg.before).every(([k,v])=>sessionStorage.getItem(k)===v)
        })''',{'url':site_drive.CALENDAR,'mark':MARK,'token':binding.document,'before':binding.storage})
        report.update(current)
        report['sessionMatches']=_session_stamp(binding.page)==binding.stamp
    except Exception:
        report['readFailed']=True
    return report


@dataclass(repr=False)
class Connection:
    guard: object=field(repr=False)
    result: dict
    # 사용자가 실제 주문/화면을 확인하는 동안 감시를 유지한다. 명시 종료 시에만 해제.
    def close(self):
        self.guard.stop()


APPLY='''arg=>{
 if(location.href!==arg.calendar||window[arg.mark]!==arg.token)return {state:'document-changed'};
 for(const [k,v] of Object.entries(arg.before))
   if(sessionStorage.getItem(k)!==v)return {state:'storage-changed'};
 try{
   for(const [k,v] of Object.entries(arg.after))sessionStorage.setItem(k,v);
   return {state:'models-written'};
 }catch(e){
   let restored=true;
   for(const k of Object.keys(arg.after)){
     try{sessionStorage.setItem(k,arg.before[k]);}catch(_){restored=false;}
   }
   return {state:restored?'write-failed-restored':'write-failed-unrestored'};
 }
}'''


def connect(binding, *, request, quote, fare_response, order_response, now=None,
            timeout_ms=10000, allow_observed_amount_layout=False, passenger_fingerprint=None):
    """동일 실행 응답만 적용하고 감시를 반환한다. 예외/실패 때도 감시를 자동 해제하지 않음.

    결과 matched는 참조 대조만 의미한다. 화면 문구는 별도 hits로 반환하며 금액·편명·
    회원 전체 검증과 Npay 도착은 후속 검증이다. 이 단계에서는 결제 버튼을 누르지 않는다.
    """
    now=time.monotonic() if now is None else now
    if type(binding) is not Binding or binding.used:
        raise ValueError('invalid-or-used-binding')
    page=binding.page
    if (page.is_closed() or page.url!=site_drive.CALENDAR
            or quote.session!=binding.session or request.subject!=binding.subject
            or not binding.created<=quote.received<=now or now-binding.created>120):
        raise ValueError('changed-context')
    if _session_stamp(page)!=binding.stamp:
        raise ValueError('session-changed')
    patch=state_bridge.build_patch(binding.storage,request=request,quote=quote,
        fare_response=fare_response,order_response=order_response,now=now,
        allow_observed_amount_layout=allow_observed_amount_layout,search_date=binding.search_date)
    reference=json.loads(order_response['body'])['pnr']
    model=json.loads(order_response['body'])
    request_fingerprint=pipeline.traveller_digest(request.body_json)
    if not request_fingerprint or passenger_fingerprint!=request_fingerprint:
        raise ValueError('unverified-order')
    evidence=order_evidence.receipt(run_id='000000000000',target=quote.target,
        response=order_response,quote=quote,received_at=0,
        passenger_fingerprint=passenger_fingerprint)
    if not resume_gate.validate_model(model,evidence):
        raise ValueError('unverified-order')
    member=page.evaluate('()=>sessionStorage.getItem("loggedInUserInfo")')
    guard=resume_gate.StoredOrderWatch(page.context,page=page,saved=evidence,member=member,
                                      existing_document=False)
    guard.start()
    binding.used=True
    connection=Connection(guard,{'stage':'not-applied','matched':False})
    try:
        applied=page.evaluate(APPLY,{'calendar':site_drive.CALENDAR,'mark':MARK,
            'token':binding.document,'before':patch.before,'after':patch.after})
        if applied.get('state')!='models-written':
            connection.result={'stage':applied.get('state','write-failed'),'matched':False}
            return connection
        page.goto(site_drive.GATE,wait_until='domcontentloaded',timeout=timeout_ms)
        result=guard.inspect_gate(reference=reference,ordered_at=now,
            expected_url=site_drive.GATE,timeout_ms=timeout_ms)
        if result['matched']:
            display_deadline=time.monotonic()+timeout_ms/1000
            while True:
                summary=site_drive.read_gate_summary(page)
                displayed,hits=site_drive._display_hints(summary,date=quote.target.date,
                                                mileage=f'{int(quote.mileage):,}')
                current=guard.judge(reference,now)
                if displayed or not current.same_reference or time.monotonic()>=display_deadline:break
                page.wait_for_timeout(100)
            result['displayHints']=hits
            # 주문 전 쿠키 비교 유지. 이동 후에는 원래 회원/로그인/주문 모델을 매번 검증.
            result['cookieStampUnchanged']=_session_stamp(page)==binding.stamp
            result['sessionUnchanged']=guard.judge(reference,now).same_reference
            result['sessionVerification']='member-status-order-model'
            # 후속 표시 조사 도중의 재주문 요청도 다시 판정한다.
            latest=guard.inspect_gate(reference=reference,ordered_at=now,
                expected_url=site_drive.GATE,timeout_ms=0)
            if not latest['matched']:result.update(latest)
            if latest['matched'] and not result['sessionUnchanged']:
                result.update(stage='session-changed',matched=False)
            elif result['matched'] and not displayed:
                result.update(stage='gate-display-unready',matched=False)
        result['paymentWindowReached']=False
        connection.result=result
    except Exception as exc:
        # 예외 원문에는 토큰/예약번호가 들어갈 수 있다.
        connection.result={'stage':'bridge-error','matched':False,'paymentWindowReached':False,
                           'diagnostic':error_summary(exc)}
    return connection
