"""독립 달력 계측기. 9233에서만 읽고 주문·좌석 선택·검색 화면 이동을 하지 않는다."""
from __future__ import annotations
import argparse
import json
import statistics
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from runtime import new_run, output_dir, atomic_json, heartbeat, measure_clock, resolve_time, runtime_hash
from test_calendar import plan, load_calendar, KST
from browser_identity import mark_context
from browser_timing import BrowserTiming

ROOT = Path(__file__).resolve().parent.parent
CAL = '/booking/calendar-fare-bonus'
API = '/api/ap/booking/avail/calendarFareMatrix'
JS = (ROOT / 'ke_award/calendar_probe.js').read_text(encoding='utf-8')


def summarize(events, opening, offset=0):
    responses = [e for e in events if e.get('kind') == 'response']
    valid = sorted((e for e in responses if e.get('state') == 'valid'), key=lambda e:e['sentAt'])
    positive = None
    transition = None
    for row in valid:
        if row['p']:
            positive = row
        elif positive is not None:
            transition = {'lastPresentRequestAt':positive['sentAt'], 'lastPresentReceivedAt':positive['receivedAt'],
                          'firstAbsentRequestAt':row['sentAt'], 'firstAbsentReceivedAt':row['receivedAt'],
                          'lastPresentId':positive.get('id'),'firstAbsentId':row.get('id'),
                          'overlappingRequests':positive['receivedAt']>row['sentAt']}
            break
    epoch = opening.timestamp()*1000-offset*1000
    ordered=sorted(valid,key=lambda e:e['receivedAt'])
    gaps=[(b['receivedAt']-a['receivedAt'])/1000 for a,b in zip(ordered,ordered[1:])]
    lags=[e['lagMs'] for e in events if e.get('kind')=='sent']
    missed=sum(e.get('missedSlots',0) for e in events if e.get('kind')=='timer-gap')
    if transition:
        transition['observedIntervalSinceOpen']=[(transition['lastPresentRequestAt']-epoch)/1000,
                                                (transition['firstAbsentReceivedAt']-epoch)/1000]
    # 응답 순서가 뒤집혀도 오래된 요청을 최신 상태로 보지 않는다.
    return {'validSamples':len(valid),'responses':len(responses),
            'errors':sum(e.get('state') not in ('valid','date-not-listed') for e in responses),
            'skippedRequests':sum(e.get('kind')=='skipped' for e in events),
            'missedTimerSlots':missed,'maxSendLagMs':max(lags) if lags else None,
            'responseGapMedian':statistics.median(gaps) if gaps else None,
            'responseGapMax':max(gaps) if gaps else None,
            'firstValidSinceOpen':(min(e['receivedAt'] for e in valid)-epoch)/1000 if valid else None,
            'firstRequestSinceOpen':(min(e['sentAt'] for e in responses)-epoch)/1000 if responses else None,
            'prestigeEverPresent':any(e['p'] for e in valid),
            'initiallyAbsent':not valid[0]['p'] if valid else None,
            'depletionObserved':transition is not None,'transition':transition,
            'interpretation':'달력 P 조건의 관측 전이. 실제 판매 완료 시각·좌석 수를 의미하지 않는다.'}


def run(a):
    p = plan(a.day)
    if not p['enabled']:
        print(p['skipReason'],flush=True); return 0
    opening = resolve_time(a.at or p['openAt'])
    target = a.target or p['departureDate']
    if (a.at or a.target) and not a.rehearsal:
        raise ValueError('확정 일정을 바꾸려면 --rehearsal이 필요합니다')
    expected=p['observer']
    if not a.rehearsal and (a.worker or a.gap_ms!=expected['gapMs'] or a.max_inflight!=expected['maxInflight'] or a.ui_reload_ms!=expected['uiReloadMs']):
        raise ValueError('실전 계측 설정은 공통 캘린더 설정을 사용합니다. 변경 비교는 리허설에서만 허용합니다.')
    date.fromisoformat(target)
    if a.rehearsal:
        now=datetime.now(KST)
        if date.fromisoformat(target)>now.date()+timedelta(days=360 if now.hour>=9 else 359):
            raise ValueError('리허설은 이미 열린 출발일을 사용합니다')
    identity=new_run(); out=output_dir(); clock=measure_clock(); offset=clock['offset']
    events=[]; ui=[]
    report={'runId':identity,'plan':p,'target':target,'openAt':opening.isoformat(),'clock':clock,
            'rehearsal':a.rehearsal,'runtimeHash':runtime_hash(),'ok':False,'why':'준비 중',
            'requestGapMs':a.gap_ms,'maxInflight':a.max_inflight,'uiReloadGapMs':a.ui_reload_ms,'worker':a.worker,
            'maxStartDelayMs':expected['maxStartDelayMs'],
            'mapping':'fareFamilyList의 KEBONUSPR + 동일 위치 fareFamilyStatus != SOLDOUT'}
    def save():
        report.update(summarize(events,opening,offset)); report['events']=events;report['ui']=ui
        atomic_json(out/'calendar_observer.json',report)
    save()
    from playwright.sync_api import sync_playwright
    helper=None
    helper_url=None
    try:
        with sync_playwright() as pw:
            report['stage']='계측 Chrome 연결';save()
            b=pw.chromium.connect_over_cdp('http://127.0.0.1:9233')
            context=b.contexts[0]
            mark_context(context,9233)
            pages=[t for t in context.pages if CAL in t.url]
            if len(pages)!=1: raise ValueError('9233의 달력 탭이 정확히 하나여야 합니다')
            page=pages[0]
            page.evaluate("() => { if(window.KE_HUD){KE_HUD.state.armed=false;KE_HUD.save();} if(window.KE_REC){KE_REC.pause('observer');KE_REC.state.playAfterReload=false;KE_REC.state.allowPay=false;KE_REC.save();}}")
            latest_ui={'responseAt':0,'generation':0}
            def finished(req):
                if urlsplit(req.url).path!=API: return
                try:
                    r=req.response(); data=r.json()
                    segments=json.loads(req.post_data or '{}').get('segmentList') or []
                    projection=page.evaluate('(x)=>ASTRA_CALENDAR.project(x.data,x.target,x.origin,x.destination,x.segments)',
                        {'data':data,'target':target,'origin':p['origin'],'destination':p['destination'],'segments':segments})
                    latest_ui.update(responseAt=time.time()*1000, generation=latest_ui['generation']+1, api=projection)
                    ui.append({'kind':'ui-response','at':latest_ui['responseAt'],'generation':latest_ui['generation'],
                               'status':r.status,'timing':req.timing,**projection})
                except Exception as e:
                    ui.append({'kind':'ui-response-error','at':time.time()*1000,'error':type(e).__name__})
            context.add_init_script(JS)
            page.on('requestfinished',finished)
            page.on('pageerror',lambda e:ui.append({'kind':'pageerror','at':time.time()*1000,'error':type(e).__name__}))
            page.on('crash',lambda _:ui.append({'kind':'crash','at':time.time()*1000}))
            with page.expect_response('**'+API,timeout=30000) as captured:
                report['stage']='실제 달력 요청 포착';save()
                page.reload(wait_until='domcontentloaded',timeout=30000)
            response=captured.value; source=response.request
            if source.method!='POST': raise ValueError('달력 POST 요청 없음')
            headers={k:v for k,v in source.headers.items() if not k.startswith('sec-')
                     and k not in ('referer','user-agent','cookie','host','content-length')}
            request={'url':source.url,'method':source.method,'body':source.post_data,'headers':headers}
            # 원본 요청에는 승객 정보가 있다. 메모리에서만 사용하고 보고서/예외에 출력하지 않는다.
            payload=json.loads(source.post_data)
            seg=payload.get('segmentList') or []
            if len(seg)!=1: raise ValueError('편도 달력 요청이 아닙니다')
            report['capturedRequest']={'path':API,'anchorDate':seg[0].get('departureDate'),
                'targetDateApplied':target,'headerNames':list(headers),'travelerCount':len(payload.get('travelers',[]))}
            page.wait_for_function("document.querySelectorAll('[id^=dep-fare-]').length > 0",timeout=20000)
            helper=context.new_page()
            helper_url='https://www.koreanair.com/__astra_observer__?run='+identity
            helper.route(helper_url,lambda route:route.fulfill(status=200,content_type='text/html',
                body='<!doctype html><meta charset=utf-8><title>ASTRA 9233 · 달력 응답 계측</title><p>계측 전용 보조 탭. 예매하지 않습니다.</p>'))
            helper.goto(helper_url,wait_until='domcontentloaded'); helper.evaluate(JS)
            timing=None
            try:timing=BrowserTiming(helper,out)
            except Exception as e:report['timingError']=type(e).__name__
            report['stage']='목표 날짜 적용·계측 대기 구성';save()
            start=opening.timestamp()*1000-offset*1000
            if time.time()*1000>start+1000: raise ValueError('계측 시작 마감 초과')
            helper.evaluate('async (x)=>{window.sampler=x.worker?await ASTRA_CALENDAR.startWorker(x.config,x.source,x.script):ASTRA_CALENDAR.start(x.config,x.source)}',
                {'config':{'origin':p['origin'],'destination':p['destination'],'target':target,
                    'startAt':start,'endAt':start+a.seconds*1000,'gapMs':a.gap_ms,'maxInflight':a.max_inflight,
                    'maxStartDelayMs':expected['maxStartDelayMs']},
                    'source':request,'worker':a.worker,'script':JS})
            page.bring_to_front()
            report.update(why='대기 중',prepared=True);save()
            print(f'{identity} 계측 준비: {p["origin"]}→{p["destination"]} {target}, {opening.isoformat()}',flush=True)
            next_reload=start-p['leadMs'] if a.ui_reload_ms else float('inf');last_key=None;last_save=0;reload_pending=False;reload_deadline=0
            while time.time()*1000<start+a.seconds*1000+6500:
                report['stage']='계측 대기/수집'
                now=time.time()*1000
                batch=helper.evaluate('sampler.drain()');events.extend(batch)
                for e in batch:
                    if e.get('kind')=='response':
                        print(f"표본 {e['id']}: {e['state']} P={e.get('p')} +{(e['receivedAt']-start)/1000:.3f}s",flush=True)
                        try:
                            page.evaluate("""x=>{
                              let badge=document.getElementById('astra-observer-status');
                              if(!badge){badge=document.createElement('div');badge.id='astra-observer-status';
                                badge.style.cssText='position:fixed;bottom:30px;left:5px;z-index:2147483646;background:#064e3b;color:white;padding:8px;font:13px sans-serif;pointer-events:none';document.body.appendChild(badge);}
                              badge.textContent='계측 9233 · '+x.target+' · 서버 P: '+x.value+' · 달력 화면은 측정 종료 후 대조';
                            }""",{'target':target,'value':('있음' if e['p'] else '없음') if e.get('state')=='valid' else e['state']})
                        except Exception:pass
                # 로딩 중 연타하지 않는다. 보조 탭의 1초 요청은 화면 재로딩과 독립적이다.
                if now>=next_reload and not reload_pending and now<start+a.seconds*1000:
                    latest_ui['responseAt']=0;reload_pending=True;reload_deadline=now+15000
                    ui.append({'kind':'reload','at':now})
                    page.reload(wait_until='commit',timeout=10000)
                    next_reload=now+a.ui_reload_ms
                try:
                    sample=page.evaluate('(target)=>ASTRA_CALENDAR.dom(target)',target)
                    if latest_ui['responseAt'] and sample.get('documentReady')=='complete': reload_pending=False
                    if reload_pending and now>reload_deadline:
                        ui.append({'kind':'ui-load-timeout','at':now});reload_pending=False
                    api=latest_ui.get('api',{})
                    eligible=bool(latest_ui['responseAt'] and api.get('state')=='valid'
                                  and sample.get('state')=='observed' and not reload_pending
                                  and sample.get('p')==api.get('p') and now-latest_ui['responseAt']>250)
                    key=(sample.get('state'),sample.get('p'),sample.get('disabled'),latest_ui['generation'],eligible)
                    if key!=last_key:
                        ui.append({'kind':'dom','at':now,'generation':latest_ui['generation'],
                            'uiResponseAt':latest_ui['responseAt'],'eligible':eligible,**sample})
                        last_key=key
                except Exception: pass  # 페이지 이동 중은 reload/응답/마감 이벤트로 식별한다.
                if now-last_save>1000:
                    if timing:
                        try:timing.save()
                        except Exception as e:report['timingError']=type(e).__name__
                    heartbeat('calendar-observer','observing' if now>=start else 'ready',target=target,openAt=opening.isoformat())
                    save();last_save=now
                page.wait_for_timeout(100)
            helper.evaluate('sampler.stop()');events.extend(helper.evaluate('sampler.drain()'))
            if timing:
                try:timing.close()
                except Exception:pass
            if not a.ui_reload_ms:
                report['stage']='계측 종료 후 실제 달력 대조';save()
                ui_timing=None
                try:
                    try:ui_timing=BrowserTiming(page,out/'ui-timing')
                    except Exception as e:report['uiTimingError']=type(e).__name__
                    previous_generation=latest_ui['generation']
                    page.reload(wait_until='domcontentloaded',timeout=15000)
                    until=time.monotonic()+15
                    while time.monotonic()<until and latest_ui['generation']==previous_generation:page.wait_for_timeout(100)
                    page.wait_for_timeout(500)
                    sample=page.evaluate('(target)=>ASTRA_CALENDAR.dom(target)',target)
                    report['postMeasurementUi']={'at':time.time()*1000,'api':latest_ui.get('api'),
                        'freshResponse':latest_ui['generation']>previous_generation,'dom':sample,
                        'page':page.evaluate('() => ({path:location.pathname,ready:document.readyState,visibility:document.visibilityState})')}
                except Exception as e:report['postMeasurementUi']={'error':type(e).__name__}
                finally:
                    if ui_timing:
                        try:ui_timing.close()
                        except Exception:pass
            report.update(why='측정 종료',ok=any(e.get('state')=='valid' for e in events))
            # UI의 P 소멸도 갱신된 유효 세대에 한해 별도로 판정한다.
            present=False;report['uiDepletionObserved']=False
            for e in ui:
                if e.get('kind')=='dom' and e.get('eligible'):
                    if e.get('p'):present=True
                    elif present:report['uiDepletionObserved']=True;break
            report['liveOpeningVerified']=False  # 리허설만으로 09시 실검증 완료를 선언하지 않는다.
            save();helper.close();helper=None
    except Exception as e:
        report.update(ok=False,why=str(e) if isinstance(e,ValueError) else type(e).__name__)
        # Playwright 예외 전문은 요청 헤더·쿠키를 포함할 수 있어 저장하지 않는다.
        save();print('계측 실패: '+report['why']+' / '+str(out),flush=True)
        # CDP가 끊겨도 보조 탭의 예약된 요청이 남지 않도록 이 실행의 탭만 정리한다.
        if helper_url:
            try:
                with sync_playwright() as recovery:
                    browser=recovery.chromium.connect_over_cdp('http://127.0.0.1:9233',timeout=5000)
                    for tab in browser.contexts[0].pages:
                        if tab.url==helper_url:tab.close()
            except Exception:pass
        return 2
    print(str(out/'calendar_observer.json'),flush=True)
    return 0 if report['ok'] else 3


if __name__=='__main__':
    ap=argparse.ArgumentParser()
    ap.add_argument('--day',required=True)
    ap.add_argument('--at',default='')
    ap.add_argument('--target',default='')
    ap.add_argument('--rehearsal',action='store_true')
    ap.add_argument('--seconds',type=int,default=90)
    defaults=load_calendar()['observer']
    ap.add_argument('--gap-ms',type=int,default=defaults['gapMs'])
    ap.add_argument('--max-inflight',type=int,default=defaults['maxInflight'])
    ap.add_argument('--ui-reload-ms',type=int,default=defaults['uiReloadMs'],help='계측 중 화면 재로딩. 기본0: 계측 후에만 대조')
    ap.add_argument('--worker',action='store_true',help='화면 렌더링과 계측 타이머를 별도 Web Worker로 분리')
    raise SystemExit(run(ap.parse_args()))
