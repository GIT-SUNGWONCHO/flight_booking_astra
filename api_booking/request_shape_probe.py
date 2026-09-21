"""조사 도구: 조회(awardAvailability) 요청 본문을 좁히면 응답이 빨라지는가. 실전 코드가 아니다.

왜
  09:00 경쟁에서 우리 바닥은 +2.3초이고 그 대부분이 조회 1.4~1.67초, 운임 1.38~1.48초다.
  경쟁자는 +0.8~1.1초에 주문한다. 단계를 줄일 수 없다면(9/19 §8, 9/20 §9.3) 남은 길은
  **각 단계를 빠르게 만드는 것**이다. 서버가 넓게 찾고 있다면 본문을 좁혀 시간이 줄 수 있다.

  구체적 의심 두 가지
    - 출발지가 도시코드 `SEL`(인천+김포)이라 서버가 두 공항을 본다 → `ICN` 으로 좁히면?
    - 준비 과정에서 '가까운 날짜 함께 조회'를 켠다 → 본문에 그런 표시가 있으면 끄면?

무엇을
  캡처한 조회 요청에서 **필드를 하나씩 빼거나 좁힌** 변형을 만들고, 기준(원본)과 번갈아
  여러 번 보내 소요 시간을 비교한다. 목표 편·등급이 응답에 그대로 있는 변형만 유효하다.
  **주문·운임은 보내지 않는다.** 조회만 한다.

  python api_booking/request_shape_probe.py --target 2026-12-17 --rounds 5
"""
from __future__ import annotations
import argparse
import json
import statistics
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'dev'))
import award_observer as ao  # noqa: E402
import seat_watch as sw  # noqa: E402
from availability import Target  # noqa: E402

OUT = ROOT / 'dev-shots' / 'research'
KEEP_TOP = ('segmentList', 'travelers', 'travellers')
KEEP_SEG = ('departureDate', 'departureAirport', 'arrivalAirport')


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] [shape] {msg}', flush=True)


def build_variants(body, *, origin_exact=None, family=None, limit=12):
    """(이름, 본문) 목록. 기준이 항상 첫 번째다. 값은 기록하지 않고 이름만 남긴다."""
    out = [('baseline', deepcopy(body))]
    seg = body.get('segmentList', [{}])[0]
    if origin_exact and seg.get('departureAirport') != origin_exact:
        narrowed = deepcopy(body)
        narrowed['segmentList'][0]['departureAirport'] = origin_exact
        out.append((f'origin={origin_exact}', narrowed))
    families = body.get('commercialFareFamilies')
    # 등급 목록을 목표 하나로 줄이면 서버가 볼 운임이 줄어든다.
    if family and isinstance(families, list) and len(families) > 1 and family in families:
        only = deepcopy(body)
        only['commercialFareFamilies'] = [family]
        out.append((f'families=[{family}]', only))
    for key, value in sorted(body.items()):
        if key in KEEP_TOP:
            continue
        if value is True:
            flipped = deepcopy(body)
            flipped[key] = False
            out.append((f'false:{key}', flipped))
        dropped = deepcopy(body)
        dropped.pop(key)
        out.append((f'drop:{key}', dropped))
    for key, value in sorted(seg.items()):
        if key in KEEP_SEG:
            continue
        if value is True:
            flipped = deepcopy(body)
            flipped['segmentList'][0][key] = False
            out.append((f'false:seg.{key}', flipped))
        dropped = deepcopy(body)
        dropped['segmentList'][0].pop(key)
        out.append((f'drop:seg.{key}', dropped))
    return out[:max(1, limit)]


def summarize(rows):
    """변형별 중앙값과 기준 대비 차이. 목표가 보인 표본만 센다."""
    by = {}
    for r in rows:
        by.setdefault(r['variant'], []).append(r)
    out = {}
    for name, group in by.items():
        good = [r for r in group if r['state'] == 'ok']
        item = {'n': len(group), 'targetSeen': len(good),
                'errors': sorted({r['state'] for r in group if r['state'] != 'ok'})}
        if good:
            item['medianMs'] = round(statistics.median(r['elapsedMs'] for r in good))
            item['minMs'] = min(r['elapsedMs'] for r in good)
            item['maxMs'] = max(r['elapsedMs'] for r in good)
            item['seats'] = sorted({str(r['seat']) for r in good})
        out[name] = item
    base = out.get('baseline', {}).get('medianMs')
    for name, item in out.items():
        if base and 'medianMs' in item:
            item['deltaMs'] = item['medianMs'] - base
    return out


def run(a):
    target = Target(a.target, a.origin, a.destination, a.family, a.carrier, a.flight)
    report = {'tool': 'request_shape_probe', 'startedAt': datetime.now().isoformat(timespec='seconds'),
              'target': {'date': a.target, 'route': f'{a.origin}-{a.destination}',
                         'flight': a.flight, 'family': a.family},
              'rounds': a.rounds, 'gapSeconds': a.gap, 'rows': []}
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
        ctx = browser.contexts[0]
        pages = [p for p in ctx.pages if ao.CAL in p.url]
        if len(pages) != 1:
            log(f'{a.port} 의 달력 탭이 정확히 하나여야 합니다(현재 {len(pages)}개)')
            return finish(report, 2)
        req, captured_on = ao.capture(pages[0], a.target, log)
        if not req:
            log('조회 요청을 캡처하지 못했습니다')
            return finish(report, 2)
        req.setdefault('method', 'POST')
        body = json.loads(req['body'])
        body['segmentList'][0].update(departureDate=a.target.replace('-', ''),
                                      departureAirport=a.origin, arrivalAirport=a.destination)
        seg = body['segmentList'][0]
        families = body.get('commercialFareFamilies')
        report['capture'] = {'capturedOn': captured_on, 'topKeys': sorted(body),
                             'segmentKeys': sorted(seg), 'headerNames': sorted(req['headers']),
                             'families': sorted(families) if isinstance(families, list) else None,
                             'travelers': len(body.get('travelers') or [])}
        log(f'본문 필드: 최상위 {sorted(body)} / 구간 {sorted(seg)} / '
            f'등급 {report["capture"]["families"]} / 승객 {report["capture"]["travelers"]}명')
        variants = build_variants(body, origin_exact=a.origin_exact, family=a.family,
                                  limit=a.max_variants)
        report['variants'] = [name for name, _ in variants]
        log(f'변형 {len(variants)}개: {report["variants"]}')

        helper = ctx.new_page()
        url = 'https://www.koreanair.com/__astra_shape_probe__'
        helper.route(url, lambda route: route.fulfill(
            status=200, content_type='text/html',
            body='<!doctype html><meta charset=utf-8><title>ASTRA 본문 시험</title>'
                 '<p>조회만 보냅니다(운임·주문 없음).</p>'))
        helper.goto(url, wait_until='domcontentloaded')
        try:
            for rnd in range(a.rounds):
                for name, variant in variants:
                    payload = dict(req, body=json.dumps(variant, ensure_ascii=False))
                    result = helper.evaluate(sw.SEND, payload)
                    if result.get('status') == 200:
                        state, seat = sw.read_seat(result.get('body'), target)
                    else:
                        state, seat = (f'http-{result.get("status")}' if result.get('status')
                                       else 'fetch-error'), None
                    row = {'round': rnd + 1, 'variant': name, 'state': state, 'seat': seat,
                           'elapsedMs': round(result.get('receivedAt', 0) - result.get('sentAt', 0))}
                    report['rows'].append(row)
                    log(f'{rnd + 1}/{a.rounds} {name}: {row["elapsedMs"]}ms {state} 좌석={seat}')
                    helper.wait_for_timeout(max(0, int(a.gap * 1000)))
        finally:
            helper.close()
    report['summary'] = summarize(report['rows'])
    base = report['summary'].get('baseline', {}).get('medianMs')
    log(f'기준 중앙값 {base}ms')
    for name, item in sorted(report['summary'].items(), key=lambda kv: kv[1].get('medianMs', 10 ** 9)):
        log(f'  {name}: {item.get("medianMs")}ms (차이 {item.get("deltaMs")}) '
            f'목표 {item["targetSeen"]}/{item["n"]} {item["errors"] or ""}')
    return finish(report, 0)


def finish(report, code):
    report['finishedAt'] = datetime.now().isoformat(timespec='seconds')
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'request-shape-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'결과: {path}')
    return code


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--port', type=int, default=9233, choices=(9232, 9233))
    p.add_argument('--target', required=True, help='이미 열린 출발일 YYYY-MM-DD')
    p.add_argument('--origin', default='SEL')
    p.add_argument('--destination', default='CDG')
    p.add_argument('--carrier', default='KE')
    p.add_argument('--flight', default='901')
    p.add_argument('--family', default='KEBONUSEY', choices=('KEBONUSEY', 'KEBONUSPR'))
    p.add_argument('--origin-exact', default='ICN', help='도시코드를 좁혀 볼 공항코드(빈 값이면 생략)')
    p.add_argument('--rounds', type=int, default=5)
    p.add_argument('--gap', type=float, default=1.2, help='요청 간격(초). 겹쳐 보내지 않는다')
    p.add_argument('--max-variants', type=int, default=12)
    raise SystemExit(run(p.parse_args()))
