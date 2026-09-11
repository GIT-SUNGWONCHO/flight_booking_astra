"""실제 타이머·fetch에서 미개방→P→소멸 전이, 오류와 표본 순서 판정 시험."""
import json
import sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'dev'))
from calendar_observer import summarize
from test_calendar import plan,KST,rehearsal_plan
from playwright.sync_api import sync_playwright

for day in ['2026-09-12','2026-09-13','2026-09-19','2026-09-20']:
    assert not plan(day)['enabled']
assert plan('2026-09-10')['origin']=='ICN'
assert plan('2026-09-15')['origin']=='CDG'
assert plan('2026-09-14')['departureDate']=='2027-09-09'
assert rehearsal_plan('2026-09-11',datetime(2026,9,11,8,20,tzinfo=KST))['rehearsalDepartureDate']=='2027-09-04'
try:plan('2026-09-26');raise AssertionError('범위 밖 허용')
except ValueError:pass

with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True)
    p=b.new_page()
    count=[0]
    def serve(route):
        if route.request.url.endswith('/calendarFareMatrix'):
            count[0]+=1
            body=route.request.post_data_json
            assert body['segmentList'][0]['departureDate']=='20270905'
            rows=[] if count[0]==1 else [{'departureDate':'20270905','emptyFare':False,
                'fareFamilyList':['KEBONUSEY','KEBONUSPR'],'fareFamilyStatus':['','' if count[0]==2 else 'SOLDOUT']}]
            data={'boundFareCalendarList':[{'departureDate':'20270905','fareCalendarList':rows}]}
            if count[0]==4:data={'code':503}
            route.fulfill(status=200,content_type='application/json',body=json.dumps(data))
        else:route.fulfill(status=200,content_type='text/html',body='<html><body>fixture</body></html>')
    p.route('**/*',serve);p.goto('https://www.koreanair.com/__fixture__')
    p.evaluate((ROOT/'ke_award/calendar_probe.js').read_text(encoding='utf-8'))
    start=p.evaluate('Date.now()+100')
    p.evaluate('(x)=>window.s=ASTRA_CALENDAR.start(x.config,x.source)',{
        'config':{'target':'2027-09-05','origin':'ICN','destination':'CDG','startAt':start,'endAt':start+3100},
        'source':{'url':'https://www.koreanair.com/api/ap/booking/avail/calendarFareMatrix','method':'POST',
            'headers':{'content-type':'application/json'},'body':json.dumps({'segmentList':[{'departureDate':'20270904','departureAirport':'SEL','arrivalAirport':'CDG'}]})}})
    p.wait_for_timeout(3400);events=p.evaluate('s.drain()');p.evaluate('s.stop()')
    rows=[e for e in events if e['kind']=='response']
    assert [e['state'] for e in rows]==['date-not-listed','valid','valid','application-error'],rows
    assert [e['p'] for e in rows]==[None,True,False,None]
    result=summarize(events,datetime.fromtimestamp(start/1000,KST))
    assert result['depletionObserved'] and result['errors']==1 and result['validSamples']==2,result
    assert 0.9<(rows[1]['sentAt']-rows[0]['sentAt'])/1000<1.15
    assert not summarize([rows[2]],datetime.now(KST))['depletionObserved']
    assert summarize(list(reversed(rows)),datetime.now(KST))['depletionObserved']
    count[0]=0
    start=p.evaluate('Date.now()+100')
    p.evaluate('async (x)=>window.s=await ASTRA_CALENDAR.startWorker(x.config,x.source,x.script)',{
        'script':(ROOT/'ke_award/calendar_probe.js').read_text(encoding='utf-8'),
        'config':{'target':'2027-09-05','origin':'ICN','destination':'CDG','startAt':start,'endAt':start+3100},
        'source':{'url':'https://www.koreanair.com/api/ap/booking/avail/calendarFareMatrix','method':'POST',
            'headers':{'content-type':'application/json'},'body':json.dumps({'segmentList':[{'departureDate':'20270904','departureAirport':'SEL','arrivalAirport':'CDG'}]})}})
    p.wait_for_timeout(3500);worker_events=p.evaluate('s.drain()');p.evaluate('s.stop()')
    worker_rows=[e for e in worker_events if e['kind']=='response']
    assert [e['p'] for e in worker_rows]==[None,True,False,None],worker_events
    p.evaluate("""() => {
      let n=0,active=0;window.peak=0;
      window.fetch=async ()=>{const id=++n;active++;window.peak=Math.max(window.peak,active);
        await new Promise(r=>setTimeout(r,id<=2?2050:50));active--;
        return new Response(JSON.stringify({boundFareCalendarList:[{departureAirport:'SEL',arrivalAirport:'CDG',fareCalendarList:[
          {departureDate:'20270905',emptyFare:false,fareFamilyList:['KEBONUSPR'],fareFamilyStatus:['']}]}]}),{status:200});};
      const start=Date.now()+100;
      window.s=ASTRA_CALENDAR.start({target:'2027-09-05',origin:'ICN',destination:'CDG',startAt:start,endAt:start+3100},
        {url:location.origin+'/api/ap/booking/avail/calendarFareMatrix',method:'POST',headers:{},
         body:JSON.stringify({segmentList:[{departureDate:'20270904',departureAirport:'SEL',arrivalAirport:'CDG'}]})});
    }""")
    p.wait_for_timeout(3400);queued=p.evaluate('s.drain()');p.evaluate('s.stop()')
    assert not any(e['kind']=='skipped' for e in queued),queued
    assert any(e['kind']=='queued' and e['waitMs']>=30 for e in queued),queued
    assert p.evaluate('peak')==2
    assert len([e for e in queued if e['kind']=='response'])==4
    # 실제 화면 P는 API 재호출만으로 변하지 않는다.
    p.set_content('<table><tr><td id="dep-fare-0">09월 05일<span class="-legend-prestige">프레스티지</span></td></tr></table>')
    assert p.evaluate("ASTRA_CALENDAR.dom('2027-09-05')")["p"] is True
    p.evaluate("document.querySelector('span').remove()")
    assert p.evaluate("ASTRA_CALENDAR.dom('2027-09-05')")["p"] is False
    b.close()
print('공통 일정·실제 타이머 1초·미개방/P/소멸/503·역순 응답·DOM 분리 시험 통과')
