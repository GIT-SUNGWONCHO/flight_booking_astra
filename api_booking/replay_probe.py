"""A7 재전송 검증: 캡처한 3구간을 연속 재전송해 T0→pnr 을 실측한다.

**이 스크립트는 실제 주문 요청을 보낸다.** 결제하지 않으므로 예약은 되지 않고
임시 좌석 보유는 시간이 지나면 풀리지만(FACTS §11), 실행 전에 사용자가 판단한다.
그래서 기본은 조회 2구간까지만 하고, 주문 재전송은 --send-order 를 줘야 한다.

만드는 보유는 최대 2건이다.
  1) 사이트 흐름을 몰아 inputTravellers 를 캡처할 때 사이트가 만드는 것
  2) --send-order 로 그 요청을 재전송할 때 만들어지는 것

결제·최종 승인은 하지 않는다. 캡처한 헤더·본문은 메모리에만 두고 저장하지 않는다.
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import transport  # noqa: E402

CDP = 'http://127.0.0.1:9232'
AVAIL = '/api/ap/booking/avail/awardAvailability'
FARE = '/api/ap/booking/avail/fareInformation'
ORDER = '/api/ap/booking/traveller/inputTravellers'


def click_text(page, pattern, wait=8000):
    box = page.evaluate("""(pat) => { let hit=null; const re=new RegExp(pat);
      const deep=(root,d)=>{ if(!root||d>10||hit) return;
        for(const x of root.querySelectorAll('button,[role=button],kds-button')){
          const s=(x.textContent||'').trim(); const r=x.getBoundingClientRect();
          if(re.test(s)&&r.width>1&&r.height>1){hit={x:r.left+r.width/2,y:r.top+r.height/2};return;} }
        for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return hit; }""", pattern)
    if box:
        page.mouse.click(box['x'], box['y'])
        page.wait_for_timeout(wait)
    return bool(box)


def click_id(page, eid, wait=7000):
    for _ in range(3):
        box = page.evaluate("""(eid) => { let hit=null;
          const deep=(root,d)=>{ if(!root||d>10||hit) return;
            const e=root.getElementById ? root.getElementById(eid) : null;
            if(e){ const b=e.getBoundingClientRect();
              if(b.width>1&&b.height>1){hit={x:b.left+b.width/2,y:b.top+b.height/2};return;} }
            for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
          deep(document,0); return hit; }""", eid)
        if not box:
            return False
        if box['y'] < 100 or box['y'] > 800:
            page.evaluate("(b)=>window.scrollBy(0,b.y-450)", box)
            page.wait_for_timeout(800)
            continue
        page.mouse.click(box['x'], box['y'])
        page.wait_for_timeout(wait)
        return True
    return False


def select_fare(page):
    box = page.evaluate("""() => { let hit=null;
      const deep=(root,d)=>{ if(!root||d>10||hit) return;
        for(const x of root.querySelectorAll('*')){
          const s=((x.getAttribute&&x.getAttribute('aria-label'))||x.textContent||'')
                    .trim().replace(/\\s+/g,' ');
          const r=x.getBoundingClientRect(); if(r.width<2||r.height<2) continue;
          if(/^항공편명 KE\\d+ 일반석 [\\d,]+ 마일$/.test(s)){
            hit={x:r.left+r.width/2,y:r.top+r.height/2}; return; } }
        for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return hit; }""")
    if box:
        page.mouse.click(box['x'], box['y'])
        page.wait_for_timeout(3000)
    return bool(box)


def main():
    ap = argparse.ArgumentParser(description='A7 재전송 검증. 기본은 조회 2구간만.')
    ap.add_argument('--send-order', action='store_true',
                    help='inputTravellers 도 재전송한다. 임시 좌석 보유가 하나 더 생긴다')
    a = ap.parse_args()

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(CDP, timeout=15000)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        # 후킹은 이 연결이 살아 있는 동안만 유지된다(2026-09-12 실측).
        transport.arm(ctx)
        print('시작 URL:', page.url[:80], flush=True)

        if 'select-award-flight' in page.url:
            select_fare(page)
            click_text(page, '^다음$', 11000)
        if 'payment/gate' in page.url:
            click_id(page, 'submit-passenger-ADT-0')
            click_id(page, 'submit-contact')

        caps = transport.captured(page)
        print('캡처:', [(c['path'].split('/')[-1], c['bodyBytes'], c['complete'])
                      for c in caps], flush=True)
        have = {c['path'] for c in caps}

        order_ready = ORDER in have
        chain = [AVAIL, FARE] + ([ORDER] if (a.send_order and order_ready) else [])
        if a.send_order and not order_ready:
            print('inputTravellers 미캡처 - 주문 재전송을 건너뛴다', flush=True)

        print('\n=== 연속 재전송 ===', flush=True)
        started = time.monotonic()
        last = None
        for path in chain:
            if path not in have:
                print(f'  {path.split("/")[-1]:20} 미캡처, 건너뜀', flush=True)
                continue
            r = transport.send_captured(page, path)
            last = r
            print(f'  {path.split("/")[-1]:20} status={r.get("status")} '
                  f'{r.get("elapsedMs", 0):.0f}ms', flush=True)
        print(f'  ---- 합계 {time.monotonic() - started:.3f}초 ----', flush=True)

        if last is not None and chain and chain[-1] == ORDER:
            try:
                payload = json.loads(last.get('body') or '{}')
                print('  주문 응답 pnr 있음:', bool(payload.get('pnr')),
                      '| 키:', sorted(payload), flush=True)
                # 보유 기한·서버 시각은 FACTS §11 의 미확인 항목이다. 값을 남기되
                # 예약번호 원문은 출력하지 않는다.
                for key in ('availableOnHold', 'createDateTime', 'createDateTimeOfKST'):
                    if key in payload:
                        print(f'  {key}: {payload[key]!r}', flush=True)
                seg = (payload.get('boundList') or [{}])[0]
                seg = (seg.get('segmentList') or [{}])[0]
                print('  구간:', {k: seg.get(k) for k in
                                ('departureAirport', 'arrivalAirport', 'flightNumber',
                                 'fareFamily', 'departureDateTime', 'status')}, flush=True)
            except ValueError:
                print('  주문 응답 파싱 불가:', (last.get('body') or '')[:200], flush=True)
        return 0


if __name__ == '__main__':
    raise SystemExit(main())
