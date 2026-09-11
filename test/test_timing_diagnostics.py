"""긴 작업 진단과 중단 후 만료 요청 방지를 실제 브라우저 타이머로 검증."""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'dev'))
from browser_timing import BrowserTiming,safe_path
from playwright.sync_api import sync_playwright

assert safe_path('https://www.koreanair.com/api/test?token=secret')=='/api/test'
assert safe_path('https://elsewhere.test/api/test') is None
with sync_playwright() as pw, TemporaryDirectory() as tmp:
    b=pw.chromium.launch(headless=True)
    page=b.new_page()
    page.route('**/*',lambda r:r.fulfill(status=200,content_type='text/html',body='<button>test</button>'))
    page.goto('https://www.koreanair.com/booking/calendar-fare-bonus')
    trace=BrowserTiming(page,Path(tmp))
    page.evaluate('''() => setTimeout(()=>{const end=performance.now()+650;while(performance.now()<end){};},20)''')
    page.wait_for_timeout(1100)
    trace.close()
    assert any(e['kind']=='long-task' and e['duration']>=600 for e in trace.events),trace.events
    assert any(e['kind']=='timer-lag' and e['lagMs']>=300 for e in trace.events),trace.events
    page.evaluate((ROOT/'ke_award/calendar_probe.js').read_text(encoding='utf-8'))
    page.evaluate('''() => {
      window.sent=[];
      window.fetch=async()=>{sent.push(Date.now());return new Response(JSON.stringify({code:503}));};
      const now=Date.now();
      window.s=ASTRA_CALENDAR.start({target:'2027-09-06',origin:'ICN',destination:'CDG',startAt:now+50,endAt:now+1800,gapMs:1000},
        {url:location.origin+'/api/ap/booking/avail/calendarFareMatrix',method:'POST',headers:{},body:JSON.stringify({segmentList:[{departureAirport:'ICN',arrivalAirport:'CDG'}]})});
      const end=performance.now()+1200;while(performance.now()<end){};
    }''')
    page.wait_for_timeout(800)
    events=page.evaluate('s.drain()');page.evaluate('s.stop()')
    assert not page.evaluate('sent.length'),events
    assert any(e['kind']=='skipped' and e['reason']=='stale-slot' for e in events),events
    b.close()
print('긴 작업·타이머 지연 관측, URL 쿼리 제외, 만료 슬롯 송신 방지 통과')
