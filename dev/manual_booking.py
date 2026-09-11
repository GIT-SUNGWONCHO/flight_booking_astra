"""사용자가 HUD 대기 시작으로 실행하는 예매. Python은 주입 유지·기록만 담당한다.

--rehearsal에서만 시험용 대기 시작 버튼을 클릭한다. 정상 운영에서는 무장/발사를 하지 않는다.
"""
from __future__ import annotations
import argparse
import json
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from urllib.parse import urlsplit
from runtime import new_run, output_dir, atomic_json, heartbeat, runtime_hash, build_hash, resolve_time, measure_clock
from test_calendar import plan, KST
from network_trace import NetworkTrace
from payment_window import inspect_new_payment_windows, payment_provider
from browser_identity import mark_context
from session_health import session_health
from browser_timing import BrowserTiming

ROOT=Path(__file__).resolve().parent.parent
CAL='/booking/calendar-fare-bonus'
CONFIGURE="""(c) => {
 if(!['npay','hyundai'].includes(c.paymentProvider)) throw Error('결제 제공자 미확정');
 const R=KE_REC,H=KE_HUD;
 R.pause('준비 초기화'); R.state.playAfterReload=false; R.loadBaked();
 for(const s of R.state.steps) if(s.sel==='#submit-contact') s.noRetry=true;
 const payment=R.state.steps.find(s=>s.text==='Npay');
 if(!payment) throw Error('결제 선택 단계 없음');
 payment.alt=[];
 if(c.paymentProvider==='hyundai') {payment.sel='';payment.text='한국발행 신용/체크카드';}
 R.state.cabin=c.cabin;R.state.expectDate=c.target.slice(5);R.state.allowPay=true;
 R.state.byCause={};R.state.problem=false;R.state.openReloads=0;R.reset();R.save();
 H.state.startAt='calendar';H.state.leadMs=2500;H.state.targetKst=c.openAt;
 H.state.armed=false;H.state.lastFire=null;H.save();H.render();
 return {armed:H.state.armed,cabin:R.state.cabin,date:R.state.expectDate,
   targetKst:H.state.targetKst,leadMs:H.state.leadMs,startAt:H.state.startAt,steps:R.state.steps.length,
   paymentProvider:c.paymentProvider};
}"""
STATE="""() => { const R=window.KE_REC,H=window.KE_HUD; if(!R||!H)return null;
 return {armed:H.state.armed,fire:H.state.lastFire,idx:R.state.idx,total:R.state.steps.length,
 playing:R.state.playing,resuming:R.state.playAfterReload,problem:R.state.problem,
 message:R.state.message,times:R.state.times,causes:R.state.byCause,navigation:R.state.navigation,
 availability:window.KE_PROBE?.availabilityState?.()||null,
 target:R.state.expectDate,cabin:R.state.cabin,startAt:H.state.startAt,leadMs:H.state.leadMs,
 clockOffsetMs:H.offset(),path:location.pathname,currency:document.querySelector('#currencyBtn')?.innerText||null}; }"""


def verify_calendar_route(response, origin, destination):
    """동일 POST 요청의 편도 노선과 응답 구조를 검증한다. 승객 원문은 반환하지 않는다."""
    url = urlsplit(response.url)
    request = response.request
    if (url.scheme != 'https' or url.hostname != 'www.koreanair.com'
            or url.path != '/api/ap/booking/avail/calendarFareMatrix'
            or request.method != 'POST'):
        raise ValueError('대한항공 달력 POST 응답이 아님')
    if not 200 <= response.status < 300:
        raise ValueError('달력 HTTP 응답 오류')
    try:
        payload = request.post_data_json
        data = response.json()
    except Exception:
        raise ValueError('달력 요청/응답 해석 실패') from None
    if not isinstance(payload, dict) or not isinstance(data, dict):
        raise ValueError('달력 요청/응답 구조 오류')
    if data.get('code') not in (None, ''):
        raise ValueError('달력 응답 본문 오류')
    airport = lambda value: 'ICN' if str(value or '').upper() == 'SEL' else str(value or '').upper()
    segments = payload.get('segmentList')
    if (not isinstance(segments, list) or len(segments) != 1 or not isinstance(segments[0], dict)
            or airport(segments[0].get('departureAirport')) != airport(origin)
            or airport(segments[0].get('arrivalAirport')) != airport(destination)):
        raise ValueError('실제 달력 요청과 확정 노선 불일치')
    bounds = data.get('boundFareCalendarList')
    if (not isinstance(bounds, list) or len(bounds) != 1 or not isinstance(bounds[0], dict)
            or not isinstance(bounds[0].get('fareCalendarList'), list)):
        raise ValueError('편도 달력 응답 구조 오류')
    bound = bounds[0]
    has_route = bool(bound.get('departureAirport') or bound.get('arrivalAirport'))
    if has_route and (airport(bound.get('departureAirport')) != airport(origin)
                      or airport(bound.get('arrivalAirport')) != airport(destination)):
        raise ValueError('실제 달력 응답과 확정 노선 불일치')
    return {'basis': '동일 달력 POST 요청의 segmentList + 단일 bound 응답',
            'origin': airport(segments[0]['departureAirport']),
            'destination': airport(segments[0]['arrivalAirport']),
            'httpStatus': response.status, 'responseRouteFieldsPresent': has_route}


def run(a):
    p=plan(a.day)
    if not p['enabled']: print(p['skipReason'],flush=True);return 0
    provider=payment_provider(p['origin'],p['destination'])
    if (a.at or a.target) and not a.rehearsal: raise ValueError('일정 변경은 리허설에서만 허용')
    opening=resolve_time(a.at or p['openAt'])
    target=a.target or p['departureDate'];date.fromisoformat(target)
    if a.rehearsal:
        now=datetime.now(KST)
        if date.fromisoformat(target)>now.date()+timedelta(days=360 if now.hour>=9 else 359):
            raise ValueError('미개방일은 리허설로 주문하지 않습니다')
    identity=new_run();out=output_dir()
    report={'runId':identity,'plan':p,'target':target,'openAt':opening.isoformat(),
        'rehearsal':a.rehearsal,'runtimeHash':runtime_hash(),'buildHash':build_hash(),
        'ok':False,'why':'준비 중','events':[],'paymentApprovalClicked':False,
        'paymentProvider':provider,
        'completionCriterion':'npay-checkout' if provider=='npay' else 'card-authentication-method-selection',
        'seatRelease':{'verified':False,'reason':'서버 좌석 해제 응답을 관측하지 않음'}}
    def save(): atomic_json(out/'manual_booking.json',report)
    save()
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as pw:
            b=pw.chromium.connect_over_cdp('http://127.0.0.1:9232')
            context=b.contexts[0]
            original_pages=set(context.pages)
            pages=[t for t in context.pages if CAL in t.url]
            if len(pages)!=1:raise ValueError('9232 달력 탭이 정확히 하나여야 합니다')
            page=pages[0];mark_context(context,9232)
            # 남은 대기 상태는 새 빌드를 읽기 전에 해제한다.
            page.evaluate("() => {if(window.KE_HUD){KE_HUD.state.armed=false;KE_HUD.save();}if(window.KE_REC){KE_REC.pause('prepare');KE_REC.state.playAfterReload=false;KE_REC.save();}}")
            js=(ROOT/'userscript/ke-award-macro.user.js').read_text(encoding='utf-8')
            context.add_init_script("if(location.hostname==='www.koreanair.com'){"+js+'}')
            trace=NetworkTrace(page,out,target[5:])
            timing=None
            try:timing=BrowserTiming(page,out)
            except Exception as e:report['timingError']=type(e).__name__
            page.on('pageerror',lambda e:report['events'].append({'kind':'pageerror','at':time.time(),'error':type(e).__name__}))
            page.on('crash',lambda _:report['events'].append({'kind':'crash','at':time.time()}))
            with page.expect_response('**/calendarFareMatrix',timeout=30000) as calendar_response:
                page.reload(wait_until='domcontentloaded',timeout=30000)
            report['routeEvidence']=verify_calendar_route(calendar_response.value,p['origin'],p['destination'])
            report['routeVerified']=True
            save()
            page.wait_for_function('window.KE_HUD && window.KE_REC',timeout=15000)
            page.wait_for_function("document.querySelectorAll('[id^=dep-fare-]').length > 0",timeout=30000)
            report['configuration']=page.evaluate(CONFIGURE,{'target':target,
                'paymentProvider':provider,
                'cabin':'일반석' if a.rehearsal else p['cabin'],
                'openAt':opening.strftime('%Y-%m-%d %H:%M:%S')})
            clock=measure_clock();report['clock']=clock
            if clock['ok']:
                page.evaluate('(c)=>KE_HUD.useMeasuredClock(c.offset*1000,c.uncertainty*1000)',clock)
            report['prepared']=True;report['why']='사용자의 대기 시작을 기다립니다';save()
            report['session']=session_health(context,opening.timestamp());save()
            page.bring_to_front()
            print(f'{identity} 준비: {p["origin"]}→{p["destination"]} {target} / 대기 미설정',flush=True)
            if a.configure_only:
                page.evaluate("() => {const e=document.querySelector('#ke-arm');if(e){e.disabled=true;e.textContent='미리보기 · 당일 08:20 준비 후 대기 가능';}}")
                report.update(why='미리보기 설정; 실전 주입 유지 프로그램 미실행',preview=True)
                if timing:
                    try:timing.close()
                    except Exception:pass
                save();print(str(out/'manual_booking.json'),flush=True);return 0
            if a.rehearsal:
                # 운영과 동일한 HUD 버튼을 통해서만 대기한다. fire() 직접 호출 금지.
                page.locator('#ke-arm').click(timeout=5000)
                print('리허설: HUD 대기 시작 클릭',flush=True)
            previous=None;last_save=0;fired=None;finished_at=None;last_clock=time.monotonic();last_session=0
            end=opening.timestamp()+240
            while time.time()<end:
                page.wait_for_timeout(100)
                try: state=page.evaluate(STATE)
                except Exception: continue
                if not state:continue
                if not fired and time.monotonic()-last_session>30:
                    report['session']=session_health(context,opening.timestamp());last_session=time.monotonic()
                    if report['session'].get('known') and not report['session'].get('coversOpen'):
                        print('주의: 현재 로그인 토큰이 09시 이후까지 유지되지 않습니다. 로그인 상태 재확인이 필요합니다.',flush=True)
                if not fired and time.monotonic()-last_clock>900:
                    # 08:20 측정값을 09시까지 방치하지 않는다. 발사 전 공통 NTP 기준 갱신.
                    import os
                    os.environ.pop('KE_CLOCK',None)
                    clock=measure_clock();report['clock']=clock;last_clock=time.monotonic()
                    if clock['ok']:page.evaluate('(c)=>KE_HUD.useMeasuredClock(c.offset*1000,c.uncertainty*1000)',clock)
                if state.get('fire'):
                    fired=state['fire'];report['fire']=fired
                key=(state['armed'],state['idx'],state['playing'],state['problem'],state['path'],state['currency'])
                if key!=previous:
                    report['events'].append({'kind':'state','at':time.time(),**state});previous=key
                    print(f"{state['idx']}/{state['total']} armed={state['armed']} {state['path']} {state['currency'] or ''}",flush=True)
                if fired:
                    observed=inspect_new_payment_windows(context.pages,original_pages,provider)
                    report['paymentWindow']=observed
                    report['orderEvidence']=trace.order_evidence
                    if observed.get('ready'):
                        report.update(ok=True,why=f'목표 결제창 표시({provider}); 최종 승인하지 않음',paymentWindow=observed,
                            secondsFromFire=time.time()-fired['localAt']/1000,finalState=state)
                        save();break
                    if not state['playing'] and not state['resuming']:
                        if finished_at is None:finished_at=time.time()
                        if state['problem'] or time.time()-finished_at>60:
                            why=('결제 로그인 필요' if observed.get('loginRequired')
                                 else f'매크로 중단 또는 목표 결제창({provider}) 미도달')
                            report.update(why=why,finalState=state);save();break
                if time.time()-last_save>1:
                    if timing:
                        try:timing.save()
                        except Exception as e:report['timingError']=type(e).__name__
                    heartbeat('manual-booking','running' if fired else ('armed-by-user' if state['armed'] else 'ready-unarmed'),
                        target=target,openAt=opening.isoformat())
                    save();last_save=time.time()
            else:report['why']='사용자 미무장 또는 실행 마감 초과'
            report['endedAt']=datetime.now(KST).isoformat();save()
            if timing:
                try:timing.close()
                except Exception:pass
            if a.rehearsal or fired:
                page.evaluate("() => {KE_HUD.state.armed=false;KE_HUD.save();KE_REC.pause('실행 종료');KE_REC.state.playAfterReload=false;KE_REC.save();}")
            print(str(out/'manual_booking.json'),flush=True)
    except Exception as e:
        report.update(ok=False,why=str(e) if isinstance(e,ValueError) else type(e).__name__);save()
        print('예매 준비/기록 실패: '+type(e).__name__+' / '+str(out),flush=True)
        return 2
    return 0 if report['ok'] else 3


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--day',required=True)
    ap.add_argument('--at',default='')
    ap.add_argument('--target',default='')
    ap.add_argument('--rehearsal',action='store_true')
    ap.add_argument('--configure-only',action='store_true',help='목표 표시만 하고 종료. 대기 버튼 비활성화')
    raise SystemExit(run(ap.parse_args()))
