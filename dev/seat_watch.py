"""좌석 수 장시간 감시기. 조회만 보낸다(운임·주문 없음).

목적: 미결제 주문이 잡은 좌석이 **언제 풀리는지** 실측한다. 우리가 주문하면 좌석 수가 N → N−1 로
줄고, 보유가 해제되면 다시 N 으로 돌아온다. 그 복귀 시각을 잡는 것이 이 도구의 일이다.

부하를 거의 주지 않도록 기본 60초 간격이며, 요청은 겹치지 않는다(앞 응답을 받고 다음을 보낸다).
계측 계정(9233)에서 돌려 예매 세션과 분리한다. 식별자·응답 원문은 저장하지 않는다.

  python dev/seat_watch.py --target 2026-12-17 --origin CDG --destination ICN --flight 902 \
      --family KEBONUSEY --minutes 30
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'api_booking'))
sys.path.insert(0, str(ROOT / 'dev'))
from runtime import new_run, output_dir, atomic_json, heartbeat, KST  # noqa: E402
import award_observer as ao  # noqa: E402
from availability import Target  # noqa: E402

SEND = """(req) => {
  const t0 = Date.now(), p0 = performance.now();
  return fetch(req.url, {method: req.method, headers: req.headers, body: req.body,
                         credentials: 'include', cache: 'no-store'})
    .then(r => r.text().then(text => ({status: r.status, body: text, sentAt: t0,
                                       receivedAt: t0 + (performance.now() - p0)})))
    .catch(e => ({error: String(e), sentAt: t0, receivedAt: t0 + (performance.now() - p0)}));
}"""


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] [seat_watch] {msg}', flush=True)


def read_seat(body, target):
    """응답에서 목표 편·등급의 좌석 수만 뽑는다. (상태, 좌석) 형태."""
    try:
        data = json.loads(body or 'null')
    except (ValueError, TypeError):
        return 'invalid-json', None
    if not isinstance(data, dict):
        return 'invalid-schema', None
    code = data.get('code') or data.get('errorCode')
    if code:
        return f'error:{code}', None
    bounds = data.get('upsellBoundAvailList')
    if not (isinstance(bounds, list) and bounds and isinstance(bounds[0], dict)):
        return 'target-absent', None
    for flight in bounds[0].get('availFlightList') or []:
        if not isinstance(flight, dict):
            continue
        legs = flight.get('flightInfoList') or []
        leg = legs[0] if legs and isinstance(legs[0], dict) else {}
        if (len(legs) != 1 or leg.get('flightNumber') != target.flight
                or leg.get('operationCarrierCode') != target.carrier
                or leg.get('codeShare') is not False
                or str(leg.get('departureDateTime') or '')[:8] != target.date.replace('-', '')):
            continue
        for fare in flight.get('commercialFareFamilyList') or []:
            if isinstance(fare, dict) and fare.get('fareFamily') == target.family:
                return 'ok', fare.get('seatCount')
    return 'target-absent', None


def transitions(samples):
    """좌석 수가 바뀐 지점. 값은 '마지막 이전 값 수신 ~ 첫 새 값 수신' 구간이다."""
    out, prev = [], None
    for s in samples:
        if s['state'] != 'ok':
            continue
        if prev is not None and s['seat'] != prev['seat']:
            out.append({'from': prev['seat'], 'to': s['seat'],
                        'lastSeenAt': prev['at'], 'firstSeenAt': s['at'],
                        'gapSeconds': round((datetime.fromisoformat(s['at'])
                                             - datetime.fromisoformat(prev['at'])).total_seconds(), 1),
                        'increase': _num(s['seat']) > _num(prev['seat'])})
        prev = s
    return out


def _num(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def run(a):
    target = Target(a.target, a.origin, a.destination, a.family, a.carrier, a.flight)
    identity = new_run()
    out = output_dir()
    end = datetime.now(KST) + timedelta(minutes=a.minutes)
    report = {'runId': identity, 'kind': 'seat-watch', 'target': a.target, 'family': a.family,
              'route': f'{a.origin}-{a.destination} {a.carrier}{a.flight}', 'gapSeconds': a.gap,
              'endsAt': end.isoformat(timespec='seconds'), 'ok': False, 'why': '준비 중',
              'samples': [], 'transitions': []}

    def save():
        report['transitions'] = transitions(report['samples'])
        atomic_json(out / 'seat_watch.json', report)

    save()
    from playwright.sync_api import sync_playwright
    helper = None
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
            ctx = browser.contexts[0]
            pages = [p for p in ctx.pages if ao.CAL in p.url]
            if len(pages) != 1:
                raise ValueError(f'{a.port} 의 달력 탭이 정확히 하나여야 합니다')
            page = pages[0]
            req, captured_on = ao.capture(page, a.target, log)
            if not req:
                raise ValueError('조회 요청을 캡처하지 못했습니다')
            body = json.loads(req['body'])
            body['segmentList'][0].update(departureDate=a.target.replace('-', ''),
                                          departureAirport=a.origin, arrivalAirport=a.destination)
            req['body'] = json.dumps(body, ensure_ascii=False)
            report['capture'] = {'capturedOn': captured_on, 'headerNames': sorted(req['headers'])}
            helper = ctx.new_page()
            url = 'https://www.koreanair.com/__astra_seat_watch__?run=' + identity
            helper.route(url, lambda route: route.fulfill(status=200, content_type='text/html',
                         body='<!doctype html><meta charset=utf-8><title>ASTRA 좌석 감시</title>'
                              '<p>조회만 보냅니다(주문 없음).</p>'))
            helper.goto(url, wait_until='domcontentloaded')
            page.bring_to_front()
            report.update(why='감시 중')
            log(f'{identity} 감시 시작: {report["route"]} {a.target} {a.family} · {a.gap}초 간격 · '
                f'{end.strftime("%H:%M:%S")} 종료')
            while datetime.now(KST) < end:
                result = helper.evaluate(SEND, req)
                state, seat = read_seat(result.get('body'), target) if result.get('status') == 200 \
                    else (f'http-{result.get("status")}' if result.get('status') else 'fetch-error', None)
                row = {'at': datetime.now(KST).isoformat(timespec='milliseconds'), 'state': state,
                       'seat': seat, 'elapsedMs': round(result.get('receivedAt', 0)
                                                        - result.get('sentAt', 0))}
                report['samples'].append(row)
                log(f'좌석={seat} ({state}) {row["elapsedMs"]}ms')
                save()
                changed = [t for t in report['transitions'] if t['increase']]
                if changed and a.stop_on_release:
                    report['why'] = '좌석 복귀 관측'
                    log(f'**좌석이 돌아왔다: {changed[-1]}**')
                    break
                heartbeat('seat-watch', 'watching', target=a.target, seat=seat)
                helper.wait_for_timeout(max(0, int(a.gap * 1000)))
            report.update(ok=any(s['state'] == 'ok' for s in report['samples']))
            if report['why'] == '감시 중':
                report['why'] = '시간 종료'
            save()
            helper.close()
            helper = None
    except Exception as exc:  # noqa: BLE001
        report.update(ok=False, why=str(exc) if isinstance(exc, ValueError) else type(exc).__name__)
        save()
        log(f'감시 실패: {report["why"]} / {out}')
        return 2
    log(f'종료({report["why"]}): 표본 {len(report["samples"])} · 변화 {report["transitions"]}')
    log(str(out / 'seat_watch.json'))
    return 0 if report['ok'] else 3


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='좌석 수 장시간 감시(조회만)')
    ap.add_argument('--target', required=True, help='출발일 YYYY-MM-DD')
    ap.add_argument('--origin', default='CDG')
    ap.add_argument('--destination', default='ICN')
    ap.add_argument('--carrier', default='KE')
    ap.add_argument('--flight', default='902')
    ap.add_argument('--family', default='KEBONUSEY', choices=('KEBONUSEY', 'KEBONUSPR', 'KEBONUSFC'))
    ap.add_argument('--port', type=int, default=9233, choices=(9232, 9233))
    ap.add_argument('--gap', type=float, default=60.0, help='요청 간격(초). 앞 응답을 받고 나서 센다')
    ap.add_argument('--minutes', type=float, default=30.0, help='감시 시간(분)')
    ap.add_argument('--stop-on-release', action='store_true', help='좌석이 늘어나면 바로 멈춘다')
    raise SystemExit(run(ap.parse_args()))
