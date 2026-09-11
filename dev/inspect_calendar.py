"""로그인된 계측 달력에서 P 렌더링 구조를 읽는다. 예약 동작 없음."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'dev-shots/calendar-structure'

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp('http://127.0.0.1:9233')
        page = next(p for p in b.contexts[0].pages if '/booking/calendar-fare-bonus' in p.url)
        page.evaluate("() => {if(window.KE_HUD){KE_HUD.state.armed=false;KE_HUD.save();} if(window.KE_REC){KE_REC.pause('structure');KE_REC.state.playAfterReload=false;KE_REC.save();}}")
        def response(r):
            if r.url.split('?')[0].endswith('/calendarFareMatrix'):
                try:
                    data = r.json()
                    # 달력 응답만 저장한다. 세션·회원 정보가 들어갈 수 있는 요청 헤더/본문은 저장하지 않는다.
                    (OUT/'calendar_response.json').write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
                    print('calendar keys:', list(data) if isinstance(data,dict) else type(data).__name__, flush=True)
                except Exception as e: print(type(e).__name__)
        page.on('response', response)
        page.reload(wait_until='domcontentloaded')
        page.wait_for_function("document.querySelectorAll('[id^=dep-fare-]').length > 10", timeout=30000)
        page.wait_for_timeout(1500)
        dom = page.evaluate("() => [...document.querySelectorAll('[id^=dep-fare-]')].map(e=>({id:e.id,html:e.outerHTML}))")
        (OUT/'calendar_cells.json').write_text(json.dumps(dom, ensure_ascii=False, indent=2), encoding='utf-8')
        print('cells',len(dom), json.dumps(dom[-3:], ensure_ascii=False), flush=True)
        sources = page.evaluate("[...document.scripts].map(s=>s.src).filter(Boolean)")
        (OUT/'script_urls.json').write_text(json.dumps(sources, indent=2), encoding='utf-8')
if __name__ == '__main__': main()
