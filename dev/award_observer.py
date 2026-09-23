"""예매 조회 계측기. 9233(본인 계정)에서 awardAvailability 조회만 보낸다. 운임·주문·좌석 선택 없음.

목적(2026-09-19 사용자 승인)
  1. 조회 API 개방 경계: 개방 몇 ms 전 송신부터 열림 응답이 오는가 → 선발사 값의 근거
     (발사 75초 전 서버 시각 추정, 60초 전 시계 재측정으로 기준을 맞춘다)
  2. 목표 편·등급 seatCount 감소 시각 → 경쟁 좌석이 빠지는 시점

일정: 개방 denseFromMs~denseToMs 는 denseGapMs 간격(겹쳐 보냄), 그 뒤 postMs 까지 sparseGapMs 간격.
같은 세션에서 조회끼리 겹치는 것은 응답이 정상이었다(9/19 조사). 이 세션은 운임을 보내지 않으므로
운임과 겹칠 때의 ERT.3010/4002 문제는 없다. 예매(9232)와 세션·Chrome 이 분리되어 있다.

캡처: 달력에서 이미 열린 날짜로 [검색]해 사이트가 보내는 조회 요청의 헤더·본문을 메모리로 잡고
날짜만 목표로 바꾼다. 달력으로 돌아간 뒤 보조 탭에서 보낸다. 식별자·응답 원문·승객 정보는 저장하지 않는다
(편·등급 seatCount·오류 코드·크기·시각만).

  실전    python dev/award_observer.py --day 2026-09-20
  리허설  python dev/award_observer.py --day 2026-09-19 --rehearsal --at 21:00:00 --target 2027-09-09
"""
from __future__ import annotations
import argparse
import json
import os
import statistics
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'api_booking'))
from runtime import new_run, output_dir, atomic_json, heartbeat, measure_clock, resolve_time  # noqa: E402
from test_calendar import plan, load_calendar, KST  # noqa: E402
import server_clock  # noqa: E402

API = '/api/ap/booking/avail/awardAvailability'
CAL = '/booking/calendar-fare-bonus'
FAMILY = {'프레스티지': 'KEBONUSPR', '일반석': 'KEBONUSEY', '일등석': 'KEBONUSFC'}
DEFAULTS = {'mode': 'both', 'denseFromMs': -1800, 'denseToMs': 600, 'denseGapMs': 150,
            'sparseGapMs': 300, 'postMs': 10000, 'maxInflight': 12, 'maxLagMs': 100,
            'serverClockSeconds': 4.0}


def settings():
    return {**DEFAULTS, **(load_calendar().get('awardObserver') or {})}


def schedule(cfg):
    """개방 기준 송신 오프셋(ms) 목록. 촘촘한 구간 뒤 성긴 구간."""
    dense = list(range(cfg['denseFromMs'], cfg['denseToMs'] + 1, cfg['denseGapMs']))
    start = (dense[-1] if dense else cfg['denseToMs']) + cfg['sparseGapMs']
    return dense + list(range(start, cfg['postMs'] + 1, cfg['sparseGapMs']))


SAMPLER = r"""(x) => {
  const S = {events: [], inflight: 0, stopped: false, next: 0};
  const t = x.target;
  const parse = (text) => {
    let d; try { d = JSON.parse(text); } catch (e) { return {state: 'invalid-json'}; }
    if (!d || typeof d !== 'object') return {state: 'invalid-schema'};
    const code = d.code || d.errorCode || null;
    if (code === 'ERT.10032') return {state: 'not-open', code: code};
    if (code) return {state: 'error', code: String(code).slice(0, 24),
                      message: String(d.message || '').replace(/[0-9]{5,}/g, '#').slice(0, 120),
                      sub: String(((d.subMessages || [])[0] || {}).message || '').replace(/[0-9]{5,}/g, '#').slice(0, 160)};
    const b = (d.upsellBoundAvailList || [])[0];
    const fl = (b && b.availFlightList) || [];
    if (!fl.length) return {state: 'open', flights: 0, target: 'no-flights'};
    for (const f of fl) {
      const legs = f.flightInfoList || [], leg = legs[0] || {};
      if (legs.length !== 1 || leg.flightNumber !== t.flight || leg.operationCarrierCode !== t.carrier
          || leg.codeShare !== false || String(leg.departureDateTime || '').slice(0, 8) !== t.date8) continue;
      // 응답에는 전 등급이 들어 있다. 목표 등급 말고 나머지도 좌석 수만 남긴다
      // (2026-09-22: 9석짜리 일반석의 감소가 계단형인지 급감인지 보려고 추가).
      const all = {};
      for (const r of (f.commercialFareFamilyList || [])) all[r.fareFamily] = r.seatCount;
      const fare = (f.commercialFareFamilyList || []).find(r => r.fareFamily === t.family);
      if (!fare) return {state: 'open', flights: fl.length, target: 'family-absent', seats: all};
      return {state: 'open', flights: fl.length, target: 'found', seat: fare.seatCount,
              seats: all, soldout: fare.soldout, flightSoldOut: f.soldOut};
    }
    return {state: 'open', flights: fl.length, target: 'flight-absent'};
  };
  const send = (i, due) => {
    S.inflight++;
    const t0 = Date.now(), p0 = performance.now();
    fetch(x.req.url, {method: 'POST', headers: x.req.headers, body: x.req.body,
                      credentials: 'include', cache: 'no-store'})
      .then(r => r.text().then(text => {
        const row = {kind: 'response', id: i, due: due, sentAt: t0, lagMs: t0 - due,
                     receivedAt: t0 + (performance.now() - p0), status: r.status, bytes: text.length};
        Object.assign(row, r.status === 200 ? parse(text) : {state: 'http-' + r.status});
        S.events.push(row); }))
      .catch(e => S.events.push({kind: 'response', id: i, due: due, sentAt: t0, lagMs: t0 - due,
                                 receivedAt: t0 + (performance.now() - p0), state: 'fetch-error'}))
      .finally(() => { S.inflight--; });
  };
  const tick = () => {
    if (S.stopped) return;
    const now = Date.now();
    while (S.next < x.slots.length && x.slots[S.next] <= now) {
      const due = x.slots[S.next];
      if (now - due > x.maxLagMs) S.events.push({kind: 'skipped', id: S.next, due: due, why: 'late'});
      else if (S.inflight >= x.maxInflight) S.events.push({kind: 'skipped', id: S.next, due: due, why: 'inflight'});
      else send(S.next, due);
      S.next++;
    }
    if (S.next >= x.slots.length) return;
    const wait = x.slots[S.next] - Date.now();
    setTimeout(tick, wait > 40 ? wait - 25 : 1);
  };
  window.__AWARD_OBS = {drain: () => S.events.splice(0), stop: () => { S.stopped = true; },
                        inflight: () => S.inflight, done: () => S.next >= x.slots.length && S.inflight === 0};
  tick();
  return x.slots.length;
}"""


def summarize(events, open_local_ms):
    """개방 경계와 좌석 변화. 시각은 개방 기준 초(보정)."""
    rel = lambda ms: round((ms - open_local_ms) / 1000, 3)
    res = sorted((e for e in events if e.get('kind') == 'response'), key=lambda e: e['sentAt'])
    not_open = [e for e in res if e.get('state') == 'not-open']
    opened = [e for e in res if e.get('state') == 'open']
    edge = {'lastNotOpenSent': rel(max(e['sentAt'] for e in not_open)) if not_open else None,
            'firstOpenSent': rel(min(e['sentAt'] for e in opened)) if opened else None,
            'lastNotOpenReceived': rel(max(e['receivedAt'] for e in not_open)) if not_open else None,
            'firstOpenReceived': rel(min(e['receivedAt'] for e in opened)) if opened else None}
    if opened and not_open:
        first = min(e['sentAt'] for e in opened)
        edge['notOpenAfterFirstOpen'] = sum(e['sentAt'] > first for e in not_open)
    changes, prev = [], None
    for e in (e for e in opened if e.get('target') == 'found'):
        if prev is not None and e.get('seat') != prev.get('seat'):
            changes.append({'from': prev.get('seat'), 'to': e.get('seat'),
                            'lastSentWithFrom': rel(prev['sentAt']), 'firstSentWithTo': rel(e['sentAt']),
                            'firstReceivedWithTo': rel(e['receivedAt'])})
        prev = e
    elapsed = [e['receivedAt'] - e['sentAt'] for e in res]
    codes = {}
    for e in res:
        if e.get('state') not in ('open', 'not-open'):
            key = e.get('code') or e.get('state')
            codes[key] = codes.get(key, 0) + 1
    return {'responses': len(res), 'notOpen': len(not_open), 'open': len(opened),
            'errors': codes, 'skipped': sum(e.get('kind') == 'skipped' for e in events),
            'maxLagMs': max((e.get('lagMs', 0) for e in res), default=None),
            'elapsedMedianMs': round(statistics.median(elapsed)) if elapsed else None,
            'elapsedMaxMs': round(max(elapsed)) if elapsed else None,
            'bytesMedian': statistics.median([e['bytes'] for e in res if e.get('bytes')]) if any(
                e.get('bytes') for e in res) else None,
            'targetStates': sorted({e.get('target') for e in opened if e.get('target')}),
            'firstSeat': next((e.get('seat') for e in opened if e.get('target') == 'found'), None),
            'edge': edge, 'seatChanges': changes,
            'interpretation': '경계·좌석 변화는 송신~수신 구간으로만 말한다. 서버가 처리 중 언제 재고를 읽는지는 모른다.'}


def capture(page, target, log):
    """이미 열린 날짜로 [검색]해 조회 요청을 메모리로 잡는다. 목표 날짜 기준 2~7일 전을 차례로 시도."""
    import site_drive
    seen = {}

    def on_request(req):
        if urlsplit(req.url).path == API and req.method == 'POST' and 'req' not in seen:
            try:
                headers = {k: v for k, v in req.all_headers().items() if not k.startswith(':')
                           and not k.startswith('sec-')
                           and k not in ('referer', 'user-agent', 'cookie', 'host', 'content-length',
                                         'origin', 'accept-encoding', 'connection')}
                seen['req'] = {'url': req.url, 'body': req.post_data, 'headers': headers}
            except Exception:
                pass
    page.context.on('request', on_request)
    try:
        for back in range(2, 8):
            day = date.fromisoformat(target) - timedelta(days=back)
            if CAL not in page.url:
                page.goto(site_drive.CALENDAR, wait_until='load', timeout=60000)
                page.wait_for_timeout(5000)
            drawn = site_drive.wait_calendar(page)
            steps = {}
            label = f'{day.month:02d}월 {day.day:02d}일'
            # 날짜 셀은 토글이고 첫 클릭이 선택으로 안 잡히는 일이 잦다(9/22~23 반복).
            # 선택될 때까지 누른 뒤 [검색]을 직접 부른다. _date_and_search 를 쓰면 그 안에서
            # 한 번 더 눌러 선택이 풀리고, 빈 날짜로 검색해 달력으로 되튕긴다.
            cell = None
            for _ in range(4):
                cell = site_drive.select_date(page, label)
                if not cell or cell.get('soldout') or cell.get('selected'):
                    break
                page.wait_for_timeout(1000)
            log(f'  날짜 {label}: {cell} (셀 {drawn}개)')
            ok = bool(cell) and cell.get('selected') and not cell.get('soldout')
            if ok:
                ok = False
                for _ in range(3):
                    site_drive.click_text(page, '^검색$', 11000)
                    if 'select-award-flight' in page.url:
                        ok = True
                        break
                    log('  검색 이동 없음, 재시도')
                    page.wait_for_timeout(2000)
            if ok:
                for _ in range(40):
                    if 'req' in seen:
                        break
                    page.wait_for_timeout(250)
                if 'req' in seen:
                    return seen['req'], day.isoformat()
    finally:
        page.context.remove_listener('request', on_request)
        # 9/19 리허설: 달력 복귀 직후 달력 계측기가 새로 고치면 달력 요청이 30초 동안 나오지 않았다.
        # 셀이 그려지고 안정될 때까지 기다린 뒤 준비 완료를 알린다(live_order 복귀와 같은 7초).
        if CAL not in page.url:
            page.goto(site_drive.CALENDAR, wait_until='load', timeout=60000)
        page.wait_for_timeout(7000)
        log(f'  달력 복귀: 셀 {site_drive.wait_calendar(page)}개')
    return None, None


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] [award_observer] {msg}', flush=True)


def run(a):
    p = plan(a.day)
    if not p['enabled']:
        log(p['skipReason'])
        return 0
    if (a.at or a.target) and not a.rehearsal:
        raise ValueError('확정 일정을 바꾸려면 --rehearsal 이 필요합니다')
    if (a.family or a.origin or a.destination or a.flight) and not a.rehearsal:
        raise ValueError('등급·노선 변경은 --rehearsal 에서만 허용합니다')
    opening = resolve_time(a.at or p['openAt'])
    target = a.target or p['departureDate']
    if a.rehearsal:
        now = datetime.now(KST)
        if date.fromisoformat(target) > now.date() + timedelta(days=360 if now.hour >= 9 else 359):
            raise ValueError('리허설은 이미 열린 출발일을 사용합니다')
    cfg = settings()
    family = FAMILY.get(p.get('cabin'), 'KEBONUSPR')
    if a.family or a.origin or a.destination or a.flight:
        family = a.family or family
        p = dict(p, origin=a.origin or p['origin'], destination=a.destination or p['destination'],
                 flight=a.flight or p['flight'])
    identity = new_run()
    out = output_dir()
    clock = measure_clock()
    offset = clock['offset'] if clock.get('ok') else 0.0
    open_local = opening.timestamp() * 1000 - offset * 1000
    offsets = schedule(cfg)
    report = {'runId': identity, 'kind': 'award-observer', 'rehearsal': a.rehearsal,
              'route': f'{p["origin"]}-{p["destination"]} KE{p["flight"]}', 'target': target,
              'family': family, 'openAt': opening.isoformat(), 'clock': clock, 'settings': cfg,
              'slots': len(offsets), 'ok': False, 'why': '준비 중', 'events': []}

    def save():
        report.update(summarize(report['events'], open_local))
        atomic_json(out / 'award_observer.json', report)

    def ready(state):
        if a.ready_file:
            Path(a.ready_file).write_text(state, encoding='utf-8')

    save()
    from playwright.sync_api import sync_playwright
    helper = None
    try:
        with sync_playwright() as pw:
            b = pw.chromium.connect_over_cdp('http://127.0.0.1:9233', timeout=15000)
            ctx = b.contexts[0]
            pages = [t for t in ctx.pages if CAL in t.url]
            if len(pages) != 1:
                raise ValueError('9233 의 달력 탭이 정확히 하나여야 합니다')
            page = pages[0]
            req, captured_on = capture(page, target, log)
            if not req:
                raise ValueError('조회 요청을 캡처하지 못했습니다')
            body = json.loads(req['body'])
            seg = body.get('segmentList') or []
            if len(seg) != 1:
                raise ValueError('편도 조회 요청이 아닙니다')
            seg[0].update(departureDate=target.replace('-', ''), departureAirport=p['origin'],
                          arrivalAirport=p['destination'])
            req['body'] = json.dumps(body, ensure_ascii=False)
            report['capture'] = {'capturedOn': captured_on, 'headerNames': sorted(req['headers']),
                                 'travelerCount': len(body.get('travelers') or [])}
            helper = ctx.new_page()
            helper_url = 'https://www.koreanair.com/__astra_award_observer__?run=' + identity
            helper.route(helper_url, lambda route: route.fulfill(status=200, content_type='text/html',
                         body='<!doctype html><meta charset=utf-8><title>ASTRA 9233 · 예매 조회 계측</title>'
                              '<p>계측 전용 보조 탭. 조회만 보냅니다(운임·주문 없음).</p>'))
            helper.goto(helper_url, wait_until='domcontentloaded')
            page.bring_to_front()
            slots = [open_local + ms for ms in offsets]
            if time.time() * 1000 > slots[0] - 5000:
                raise ValueError('계측 시작 마감 초과')
            report.update(why='대기 중', prepared=True)
            save()
            ready('ready')
            log(f'{identity} 준비: {report["route"]} {target} {family} · 캡처 {captured_on} · '
                f'표본 {len(slots)}개 · 개방 {opening.isoformat()}')
            # 캡처 헤더는 예매 쪽에서도 40분 이상 재사용해 왔다. 샘플러는 시작 20초 전에 건다.
            # T-75초 서버 시각 추정, T-60초 시계 재측정(9/20: 37분 전 측정값을 써서 0.1초 어긋났다).
            probed = measured = False
            while time.time() * 1000 < slots[0] - 20000:
                heartbeat('award-observer', 'ready', target=target, openAt=opening.isoformat())
                left = slots[0] - time.time() * 1000
                if not probed and left < 75000:
                    probed = True
                    report['serverClock'] = server_clock.measure(seconds=cfg['serverClockSeconds'],
                                                                 offset=offset)
                    log(f'서버 시각 추정: 로컬이 서버보다 {report["serverClock"].get("localAheadOfServerMs")}ms 빠름 · '
                        f'기준시각 대비 {report["serverClock"].get("trueAheadOfServerMs")}ms · '
                        f'표본 {report["serverClock"].get("samples")}')
                if not measured and left < 60000:
                    measured = True
                    os.environ.pop('KE_CLOCK', None)
                    again = measure_clock()
                    report['clockFinal'] = again
                    if again.get('ok'):
                        moved = (again['offset'] - offset) * 1000
                        offset = again['offset']
                        open_local = opening.timestamp() * 1000 - offset * 1000
                        slots = [open_local + ms for ms in offsets]
                        log(f'시계 재측정: 오프셋 {offset * 1000:+.1f}ms(직전 대비 {moved:+.1f}ms) '
                            f'±{(again.get("uncertainty") or 0) * 1000:.1f}ms')
                    else:
                        log('시계 재측정 실패 - 시작 측정값을 그대로 쓴다')
                # time.sleep 은 금지: 동기 Playwright 는 잠든 동안 CDP 메시지를 처리하지 않아 같은 Chrome 의
                # 새 문서·다른 클라이언트 연결이 멈춘다(9/19 리허설: 달력 계측기 새로 고침·연결 시간 초과).
                helper.wait_for_timeout(1000)
            helper.evaluate(SAMPLER, {'slots': slots, 'req': req, 'maxInflight': cfg['maxInflight'],
                                      'maxLagMs': cfg['maxLagMs'],
                                      'target': {'flight': p['flight'], 'carrier': 'KE', 'family': family,
                                                 'date8': target.replace('-', '')}})
            last_save = 0
            deadline = slots[-1] + 15000
            while time.time() * 1000 < deadline:
                batch = helper.evaluate('() => window.__AWARD_OBS.drain()')
                report['events'].extend(batch)
                for e in batch:
                    if e.get('kind') == 'response':
                        seat = f' 좌석={e.get("seat")}' if e.get('target') == 'found' else f' {e.get("target") or ""}'
                        log(f'표본 {e["id"]}: {e.get("state")}{seat} {e.get("code") or ""} '
                            f'송신 {(e["sentAt"] - open_local) / 1000:+.3f}s → 수신 {(e["receivedAt"] - open_local) / 1000:+.3f}s')
                if time.time() - last_save > 1:
                    heartbeat('award-observer', 'observing', target=target, openAt=opening.isoformat())
                    save()
                    last_save = time.time()
                if time.time() * 1000 > slots[-1] and helper.evaluate('() => window.__AWARD_OBS.done()'):
                    break
                helper.wait_for_timeout(200)
            helper.evaluate('() => window.__AWARD_OBS.stop()')
            report['events'].extend(helper.evaluate('() => window.__AWARD_OBS.drain()'))
            report.update(why='측정 종료', ok=any(e.get('kind') == 'response' for e in report['events']))
            save()
            helper.close()
            helper = None
    except Exception as e:
        report.update(ok=False, why=str(e) if isinstance(e, ValueError) else type(e).__name__)
        save()
        ready('failed')
        log(f'계측 실패: {report["why"]} / {out}')
        return 2
    s = report
    log(f'요약: 응답 {s["responses"]} · 미개방 {s["notOpen"]} · 열림 {s["open"]} · 오류 {s["errors"]} · '
        f'건너뜀 {s["skipped"]} · 경계 {s["edge"]} · 좌석 변화 {s["seatChanges"]}')
    log(str(out / 'award_observer.json'))
    return 0 if report['ok'] else 3


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='예매 조회 계측기(9233, 조회만)')
    ap.add_argument('--day', required=True)
    ap.add_argument('--at', default='')
    ap.add_argument('--target', default='')
    ap.add_argument('--rehearsal', action='store_true')
    ap.add_argument('--family', default='', choices=('', 'KEBONUSEY', 'KEBONUSPR', 'KEBONUSFC'),
                    help='리허설 전용: 계측 등급(좌석이 있는 등급으로 읽기 확인)')
    ap.add_argument('--origin', default='', help='리허설 전용: 출발 공항')
    ap.add_argument('--destination', default='', help='리허설 전용: 도착 공항')
    ap.add_argument('--flight', default='', help='리허설 전용: 편명 숫자')
    ap.add_argument('--ready-file', default='', help='준비 완료(ready)·실패(failed)를 적을 파일(체인용)')
    raise SystemExit(run(ap.parse_args()))
