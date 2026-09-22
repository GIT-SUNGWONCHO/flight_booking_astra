"""사람이 직접 예약 화면을 조작하는 동안 API 호출을 기록한다.

왜
  모바일 화면폭에서는 주문 단계가 두 화면으로 갈라진다(사용자 관측, 2026-09-22).
  그때 예약 생성 호출이 하나 더 나가는지, 아니면 화면만 갈라지고 호출은 같은지를
  봐야 한다. 자동 조작은 모바일 레이아웃에서 버튼을 못 찾아 실패했다. 그래서
  사람이 몰고 우리는 기록만 한다.

안전
  모든 `/api/` POST 를 한 번 거쳐, 경로에 traveller/order/reserv/pnr 이 있으면
  **대소문자를 무시하고 네트워크에서 차단**한다. 끝까지 눌러도 예약이 생기지 않는다.
  본문·토큰은 저장하지 않고 경로·크기·시각만 남긴다.

사용
  .venv/Scripts/python.exe dev/flow_trace.py --port 9232 --minutes 12
  그 다음 브라우저 창을 폰 너비(약 400px)로 줄이고 평소처럼 예약을 진행한다.
"""
from __future__ import annotations
import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'dev-shots' / 'research'

# 이 낱말이 경로에 있으면 POST 를 보내지 않는다. 예약 생성 방지.
DANGER = re.compile(r'traveller|traveler|order|reserv|pnr', re.I)
# 낱말별 글로브로 거르지 않는다. Playwright 의 경로 글로브는 대소문자를 구분해서
# createOrder 같은 낙타등 경로를 그냥 통과시킨다. 모르는 경로를 찾으러 가는 기록이라
# 모든 /api/ 를 일단 받아 DANGER 로 판정한다.
API_ROUTE = '**/api/**'


def path_of(url):
    return url.split('koreanair.com', 1)[1].split('?')[0] if 'koreanair.com' in url else url


def is_dangerous(path):
    return bool(DANGER.search(path))


def summarize(calls, blocked):
    """POST 만 추려 화면 전환과 함께 보기 좋게 만든다."""
    posts = [c for c in calls if c['method'] == 'POST']
    return {'total': len(calls), 'posts': len(posts), 'blocked': len(blocked),
            'postPaths': sorted({c['path'] for c in posts}),
            'blockedPaths': sorted({c['path'] for c in blocked})}


def run(a):
    calls, blocked, urls = [], [], []
    t0 = time.monotonic()

    def rel():
        return round((time.monotonic() - t0) * 1000)

    def log(msg):
        print(f'[{rel():>7}ms] {msg}', flush=True)

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
        ctx = b.contexts[0]

        def guard(route):
            req = route.request
            p = path_of(req.url)
            if req.method == 'POST' and is_dangerous(p):
                blocked.append({'at': rel(), 'path': p, 'bytes': len(req.post_data or '')})
                log(f'  ✂ 차단(예약 생성 안 됨) POST {p} · {len(req.post_data or "")}바이트')
                route.abort()
                return
            route.continue_()

        def rec(req):
            if 'koreanair.com' not in req.url:
                return
            p = path_of(req.url)
            if '/api/' not in p:
                return
            calls.append({'at': rel(), 'method': req.method, 'path': p,
                          'bytes': len(req.post_data or '') if req.method == 'POST' else 0})
            if req.method == 'POST':
                log(f'  POST {p} · {len(req.post_data or "")}바이트')

        def live_page():
            """사람이 모는 탭을 따라간다. 탭을 새로 열거나 닫아도 기록이 끊기지 않게."""
            alive = [pg for pg in ctx.pages if not pg.is_closed()]
            for pg in alive:
                if 'koreanair.com' in pg.url:
                    return pg
            return alive[0] if alive else None

        ctx.on('request', rec)
        ctx.route(API_ROUTE, guard)
        page = live_page()
        if page is None:
            print('열린 탭이 없다. 브라우저에서 예매 화면을 먼저 열어라.', file=sys.stderr)
            return 2
        log(f'기록 시작 · 포트 {a.port} · {page.url[:70]}')
        print('=' * 72)
        print('  이제 브라우저 창을 폰 너비(약 400px)로 줄이고 평소처럼 예약을 진행하세요.')
        print('  결제 직전 버튼까지 눌러도 됩니다 - 예약 생성 요청은 차단됩니다.')
        print(f'  {a.minutes}분 뒤 자동으로 끝나고 요약이 나옵니다. (Ctrl+C 로 즉시 종료)')
        print('=' * 72, flush=True)

        end = time.monotonic() + a.minutes * 60
        try:
            while time.monotonic() < end:
                page = live_page()
                if page is None:
                    log('열린 탭이 없다. 기록을 끝낸다.')
                    break
                try:
                    page.wait_for_timeout(2000)      # time.sleep 금지(CDP 정지)
                    now = page.url
                except Exception:
                    continue                         # 탭이 닫혔다. 다음 회차에 다시 고른다
                if not urls or urls[-1][1] != now:
                    urls.append((rel(), now))
                    log(f'화면 이동 → {now[-60:]}')
        except KeyboardInterrupt:
            log('사용자 종료')

    report = {'tool': 'flow_trace', 'port': a.port,
              'startedAt': datetime.now().isoformat(timespec='seconds'),
              'urls': [{'at': t, 'url': u} for t, u in urls],
              'calls': calls, 'blocked': blocked, 'summary': summarize(calls, blocked)}
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'flow-trace-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')

    print('\n===== 화면 이동 =====')
    for t, u in urls:
        print(f'  {t:>8}ms  {u[-70:]}')
    print('\n===== POST 만 (순서대로) =====')
    for c in calls:
        if c['method'] == 'POST':
            print(f"  {c['at']:>8}ms  {c['bytes']:>6}B  {c['path']}")
    print('\n===== 차단된 예약 생성 POST =====')
    for c in blocked:
        print(f"  {c['at']:>8}ms  {c['bytes']:>6}B  {c['path']}")
    print(f"\n요약: {json.dumps(report['summary'], ensure_ascii=False)}")
    print(f'결과 파일: {path}')
    return 0


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--port', type=int, default=9232, choices=(9232, 9233, 9242))
    p.add_argument('--minutes', type=float, default=12.0)
    sys.exit(run(p.parse_args()))
