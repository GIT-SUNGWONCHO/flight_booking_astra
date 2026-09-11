"""달력 새로고침만 진단한다. 날짜·좌석 선택, 무장, 주문 생성 없음."""
import argparse
import time
from pathlib import Path
from playwright.sync_api import sync_playwright
from browser_timing import BrowserTiming
from runtime import new_run,output_dir,atomic_json


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--port',type=int,choices=[9232,9233],required=True)
    ap.add_argument('--seconds',type=int,default=40)
    ap.add_argument('--reload',action='store_true')
    a=ap.parse_args()
    if not 1<=a.seconds<=90:raise ValueError('진단 시간은1~90초')
    identity=new_run();out=output_dir()
    report={'runId':identity,'port':a.port,'reload':a.reload,'ordersSent':0,'ok':False}
    with sync_playwright() as pw:
        b=pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}')
        pages=[p for p in b.contexts[0].pages if '/booking/calendar-fare-bonus' in p.url]
        if len(pages)!=1:raise ValueError('진단할 달력 탭이 정확히 하나여야 함')
        page=pages[0]
        state=page.evaluate('''() => ({armed:window.KE_HUD?.state.armed,playing:window.KE_REC?.state.playing,resuming:window.KE_REC?.state.playAfterReload})''')
        if any(state.values()):raise ValueError('실행/대기 중인 예매는 진단하지 않음')
        trace=BrowserTiming(page,out)
        report['startedAt']=time.time()*1000
        try:
            if a.reload:page.reload(wait_until='commit',timeout=15000)
            until=time.monotonic()+a.seconds
            while time.monotonic()<until:
                page.wait_for_timeout(250);trace.collect()
            report['final']=page.evaluate('''() => ({path:location.pathname,visibility:document.visibilityState,ready:document.readyState,cells:document.querySelectorAll('[id^="dep-fare-"]').length})''')
            report['ok']=report['final']['cells']>0
        finally:
            trace.close()
            report['ordersSent']=sum(e.get('kind')=='request' and e.get('path','').endswith('/inputTravellers') for e in trace.events)
            atomic_json(out/'calendar_diagnostic.json',report)
    print(out,flush=True)


if __name__=='__main__':main()
