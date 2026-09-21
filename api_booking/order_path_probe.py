"""조사 도구: 운임(fareInformation) 없이 조회 → 주문이 되는지 본다. 실전 코드가 아니다.

왜
  9/20 Δ 실험 역산으로 경쟁자의 주문 송신이 +0.6~1.35초로 나왔다. 우리 경로(조회 1.6초 + 운임 1.6초)
  로는 +2.35초가 최선이므로, 경쟁자가 **운임 단계를 건너뛰는지** 확인한다. 통과하면 1.6초를 줄인다.

무엇을
  캡처 통과로 조회·운임·주문 세 요청을 잡고, 달력으로 돌아온 뒤 **조회 → (운임 생략) → 주문**을 보낸다.
  주문 1건이 실제로 만들어질 수 있다(이미 열린 날짜·일반석에서만 쓴다). 재전송은 하지 않는다.
  금액·마일리지 검증을 거치지 않으므로 실전에는 쓰지 않는다.

  python api_booking/order_path_probe.py --date 2026-12-17 --capture-iso 2026-12-16 --capture-date "12월 16일"
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'dev'))
import availability  # noqa: E402
import fare  # noqa: E402
import site_drive  # noqa: E402
import transport  # noqa: E402
from availability import Target  # noqa: E402
from evidence import error_detail  # noqa: E402

AVAIL, FARE, ORDER = availability.PATH, fare.PATH, '/api/ap/booking/traveller/inputTravellers'
OUT = ROOT / 'dev-shots' / 'research'


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S.%f")[:-3]}] {msg}', flush=True)


def judge_order(body):
    """예약이 만들어졌는지와 오류 코드만 본다. 식별자 원문은 남기지 않는다."""
    try:
        data = json.loads(body or 'null')
    except (ValueError, TypeError):
        return {'state': 'invalid-json'}
    if not isinstance(data, dict):
        return {'state': 'invalid-schema'}
    pnr = data.get('pnr')
    out = {'state': 'order-created' if isinstance(pnr, str) and pnr.strip() else 'no-reference',
           'referencePresent': bool(isinstance(pnr, str) and pnr.strip()),
           'keys': sorted(data)[:12], 'error': error_detail(data)}
    bounds = data.get('boundList')
    if isinstance(bounds, list) and bounds and isinstance(bounds[0], dict):
        segments = bounds[0].get('segmentList') or [{}]
        leg = segments[0] if isinstance(segments[0], dict) else {}
        out['segment'] = {k: leg.get(k) for k in ('flightNumber', 'fareFamily', 'cabinClass',
                                                  'segmentStatus')}
        out['segmentDate'] = str(leg.get('departureDateTime') or '')[:8]
    fare_info = data.get('pnrFareInfo')
    if isinstance(fare_info, dict):
        out['fareInfoPresent'] = True
        out['currency'] = fare_info.get('currency')
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--port', type=int, default=9232)
    p.add_argument('--date', required=True, help='목표 출발일(이미 열린 날짜)')
    p.add_argument('--origin', default='CDG')
    p.add_argument('--destination', default='ICN')
    p.add_argument('--flight', default='902')
    p.add_argument('--family', default='KEBONUSEY')
    p.add_argument('--capture-iso', required=True)
    p.add_argument('--capture-date', required=True, help='달력 라벨(예: 12월 16일)')
    p.add_argument('--capture-cabin', default='일반석')
    p.add_argument('--with-fare', action='store_true',
                   help='비교군: 운임을 거쳐 주문한다(기본은 운임 생략)')
    a = p.parse_args()
    target = Target(a.date, a.origin, a.destination, a.family, 'KE', a.flight)
    report = {'tool': 'order_path_probe', 'startedAt': datetime.now().isoformat(timespec='seconds'),
              'target': {'date': a.date, 'route': f'{a.origin}-{a.destination}', 'flight': a.flight,
                         'family': a.family},
              'withFare': a.with_fare, 'steps': []}

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        transport.arm(ctx)
        transport.install(page)
        since = transport.begin_generation(page)
        log('캡처 통과 시작(사이트 주문 요청은 차단)')
        from live_order import ensure_krw
        steps = site_drive.capture_pass(page, a.capture_date, cabin=a.capture_cabin, log=log,
                                        flight=a.flight, ensure_currency=ensure_krw)
        report['prepare'] = steps
        snap = transport.snapshot(page, since=since)
        report['captured'] = sorted(k.split('/')[-1] for k in snap)
        if not all(k in snap for k in (AVAIL, FARE, ORDER)):
            log(f'캡처 부족: {report["captured"]} - 중단')
            return finish(report, 1)
        back = site_drive.return_to_calendar(page, log=log)
        if back.get('cells', 0) <= 10:
            log('달력 복귀 실패 - 중단')
            return finish(report, 1)

        # 여기부터는 사이트 주문 차단(watch)이 없다. 우리가 보내는 주문만 나간다.
        t0 = time.monotonic()
        body = json.loads(snap[AVAIL].body)
        body['segmentList'][0].update(departureDate=a.date.replace('-', ''),
                                      departureAirport=a.origin, arrivalAirport=a.destination)
        r = transport.send_request(page, snap[AVAIL], body=json.dumps(body, ensure_ascii=False))
        req = availability.Request(target, 'probe', 'g', time.monotonic(), body, {})
        verdict = availability.judge(req, r.get('status'), r.get('body'), 'g', 'probe',
                                     time.monotonic())
        report['steps'].append({'name': 'award', 'status': r.get('status'),
                                'elapsedMs': round(r.get('elapsedMs') or 0), 'verdict': verdict.state,
                                'atMs': round((time.monotonic() - t0) * 1000)})
        log(f'조회 {r.get("status")} {round(r.get("elapsedMs") or 0)}ms 판정={verdict.state}')
        if verdict.state != 'selected':
            log('조회가 selected 가 아니다 - 주문하지 않는다')
            return finish(report, 2)

        if a.with_fare:
            selection = verdict.selection
            fbody = json.loads(snap[FARE].body)
            fbody['recommendList'][0].update(recommendId=selection.recommend_id,
                                             flightId=selection.flight_id)
            rf = transport.send_request(page, snap[FARE], body=json.dumps(fbody, ensure_ascii=False))
            freq = fare.build_request(selection, 'g', 'probe', target, None, json.loads(snap[FARE].body),
                                      {}, time.monotonic())
            fv = fare.judge(freq, rf.get('status'), rf.get('body'), freq.generation, 'g', 'probe',
                            target, time.monotonic())
            report['steps'].append({'name': 'fare', 'status': rf.get('status'),
                                    'elapsedMs': round(rf.get('elapsedMs') or 0), 'verdict': fv.state,
                                    'atMs': round((time.monotonic() - t0) * 1000)})
            log(f'운임 {rf.get("status")} {round(rf.get("elapsedMs") or 0)}ms 판정={fv.state}')
            if fv.state != 'validated':
                return finish(report, 2)

        log('**주문 전송**(운임 ' + ('거침' if a.with_fare else '생략') + ') - 1회만 보낸다')
        ro = transport.send_request(page, snap[ORDER])
        outcome = judge_order(ro.get('body'))
        report['steps'].append({'name': 'order', 'status': ro.get('status'),
                                'elapsedMs': round(ro.get('elapsedMs') or 0),
                                'atMs': round((time.monotonic() - t0) * 1000), **outcome})
        log(f'주문 {ro.get("status")} {round(ro.get("elapsedMs") or 0)}ms → {outcome["state"]}'
            + (f' · 오류 {json.dumps(outcome["error"], ensure_ascii=False)}' if outcome.get('error') else '')
            + (f' · 구간 {json.dumps(outcome.get("segment"), ensure_ascii=False)}' if outcome.get('segment') else ''))
        return finish(report, 0)


def finish(report, code):
    report['finishedAt'] = datetime.now().isoformat(timespec='seconds')
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'order-path-probe-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'결과: {path}')
    return code


if __name__ == '__main__':
    sys.exit(main())
