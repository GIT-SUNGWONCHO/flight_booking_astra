"""시간단축 A 조사: 조회(awardAvailability)를 건너뛰고 운임부터 보낼 수 있는가.

09:00 전 조회는 ERT.10032 만 돌려주므로(9/19) 운임에 넣을 recommendId·flightId 를
개방 전에 얻을 방법은 '규칙으로 만들기'뿐이다. 이 도구는 이미 열린 날짜로 다음을 본다.

1단계(조회만): 같은 날짜 반복·다른 날짜의 식별자를 비교해 같음·규칙 여부를 **구조로만** 기록한다.
2단계(--fare-tests, 운임만): 대조 운임, 최신 조회가 아닌 식별자로 운임, 조회 응답을 기다리지 않고 겹쳐 보낸 운임.

주문은 없다. 사이트 흐름은 운임 선택 → [다음]까지만 가며, 주문 요청은 HandoffWatch 로 네트워크에서
막고 계수한다. 식별자·토큰·응답 원문은 파일·터미널에 쓰지 않는다(길이·문자 종류·관계 이름만).
결과: dev-shots/research/award-id-probe-<시각>.json
"""
from __future__ import annotations
import argparse
import base64
import binascii
import json
import re
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
from availability import Selection, Target  # noqa: E402
from evidence import error_detail  # noqa: E402

AVAIL, FARE = availability.PATH, fare.PATH
OUT = ROOT / 'dev-shots' / 'research'


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)


# ---------------------------------------------------------------- 구조 기술(원문 없음)

def _classes(s):
    out = set()
    for ch in s:
        out.add('digit' if ch.isdigit() else 'upper' if ch.isupper() else
                'lower' if ch.islower() else 'sym')
    return sorted(out)


def _symbols(s):
    return sorted({ch for ch in s if not ch.isalnum()})


def _decoded(s):
    if len(s) < 8 or not re.fullmatch(r'[A-Za-z0-9+/=_-]+', s):
        return None
    for fn in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            raw = fn(s + '=' * (-len(s) % 4))
        except (binascii.Error, ValueError):
            continue
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            continue
        if text and sum(ch.isprintable() for ch in text) / len(text) > 0.95:
            return text
    return None


def describe(value, ctx, depth=0):
    """문자열 식별자의 구조. ctx 는 {이름: 알려진 값}. 원문·조각 값은 넣지 않는다."""
    if not isinstance(value, str):
        return {'type': type(value).__name__}
    tokens = [t for t in re.split(r'[^A-Za-z0-9]+', value) if t]
    rows = []
    for t in tokens:
        rows.append({'len': len(t), 'cls': _classes(t),
                     'equals': sorted(k for k, v in ctx.items() if v and t == v),
                     'contains': sorted(k for k, v in ctx.items()
                                        if v and len(v) >= 3 and v in t and t != v)})
    out = {'len': len(value), 'cls': _classes(value), 'symbols': _symbols(value),
           'tokens': rows,
           'containsKnown': sorted(k for k, v in ctx.items() if v and len(v) >= 3 and v in value)}
    text = _decoded(value) if depth == 0 else None
    if text is not None:
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            out['base64Json'] = {k: sorted(n for n, v in ctx.items() if v and str(obj[k]) == v)
                                 for k in sorted(obj)}
        else:
            out['base64'] = describe(text, ctx, depth + 1)
    return out


def diff(a, b):
    """두 문자열의 차이 크기만. 같은 위치 비교·공통 앞뒤 길이."""
    if not (isinstance(a, str) and isinstance(b, str)):
        return {'comparable': False}
    pre = 0
    while pre < min(len(a), len(b)) and a[pre] == b[pre]:
        pre += 1
    suf = 0
    while suf < min(len(a), len(b)) - pre and a[-1 - suf] == b[-1 - suf]:
        suf += 1
    out = {'equal': a == b, 'lenA': len(a), 'lenB': len(b), 'commonPrefix': pre, 'commonSuffix': suf}
    if len(a) == len(b):
        out['hamming'] = sum(x != y for x, y in zip(a, b))
    ta = [t for t in re.split(r'[^A-Za-z0-9]+', a) if t]
    tb = [t for t in re.split(r'[^A-Za-z0-9]+', b) if t]
    if len(ta) == len(tb):
        out['tokenDiff'] = [i for i, (x, y) in enumerate(zip(ta, tb)) if x != y]
    return out


def substitute(value, ctx_from, ctx_to):
    """ctx_from 의 값(3자 이상, 두 문맥에서 다른 것)을 ctx_to 값으로 바꾼 예측."""
    if not isinstance(value, str):
        return None
    keys = sorted((k for k in ctx_from if k in ctx_to and ctx_from[k] and ctx_to[k]
                   and len(ctx_from[k]) >= 3 and ctx_from[k] != ctx_to[k]),
                  key=lambda k: -len(ctx_from[k]))
    out = value
    for k in keys:
        out = out.replace(ctx_from[k], ctx_to[k])
    return out


# ---------------------------------------------------------------- 응답 해석(메모리)

def parse_award(body):
    try:
        data = json.loads(body or 'null')
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def entries(data, target):
    """응답의 (편, 운임등급) 항목. ids 는 메모리에만 두고 기록에는 describe 결과만 쓴다."""
    out = []
    bounds = data.get('upsellBoundAvailList') if data else None
    if not (isinstance(bounds, list) and bounds and isinstance(bounds[0], dict)):
        return out
    flights = bounds[0].get('availFlightList') or []
    for fi, flight in enumerate(flights):
        if not isinstance(flight, dict):
            continue
        info = flight.get('flightInfoList') or [{}]
        leg = info[0] if isinstance(info[0], dict) else {}
        for ri, f in enumerate(flight.get('commercialFareFamilyList') or []):
            if not isinstance(f, dict):
                continue
            out.append({
                'flightIdx': fi, 'fareIdx': ri, 'nLegs': len(info),
                'flightNumber': leg.get('flightNumber'), 'carrier': leg.get('operationCarrierCode'),
                'codeShare': leg.get('codeShare'), 'family': f.get('fareFamily'),
                'seatCount': f.get('seatCount'), 'soldout': f.get('soldout'),
                'departureDateTime': leg.get('departureDateTime'),
                'flightId': flight.get('flightId'), 'recommendId': f.get('recommendId'),
                'flightKeys': sorted(flight), 'fareKeys': sorted(f),
                '_flight': flight, '_fare': f,
            })
    return out


def context(target, e, data):
    """식별자와 비교할 알려진 값. 응답 안의 다른 문자열 필드도 이름으로 넣는다."""
    d = target.date.replace('-', '')
    ctx = {'date8': d, 'date6': d[2:], 'dateDash': target.date, 'mmdd': d[4:],
           'flight': str(e['flightNumber'] or ''), 'flight4': str(e['flightNumber'] or '').zfill(4),
           'carrierFlight': f'{e["carrier"]}{e["flightNumber"]}',
           'carrierFlight4': f'{e["carrier"]}{str(e["flightNumber"] or "").zfill(4)}',
           'family': e['family'] or '', 'family2': (e['family'] or '')[-2:],
           'origin': target.origin, 'destination': target.destination, 'carrier': e['carrier'] or '',
           'depDateTime': e['departureDateTime'] or '',
           'depHHMM': (e['departureDateTime'] or '')[8:12],
           'flightIdx0': str(e['flightIdx']), 'flightIdx1': str(e['flightIdx'] + 1),
           'fareIdx0': str(e['fareIdx']), 'fareIdx1': str(e['fareIdx'] + 1)}
    for prefix, src in (('resp', data), ('flight', e['_flight']), ('fare', e['_fare'])):
        for k, v in (src or {}).items():
            if isinstance(v, (str, int)) and not isinstance(v, bool) and k not in ('flightId', 'recommendId'):
                ctx[f'{prefix}.{k}'] = str(v)
    return {k: v for k, v in ctx.items() if isinstance(v, str)}


# ---------------------------------------------------------------- 전송

def send_award(page, cap, target):
    body = json.loads(cap.body)
    body['segmentList'][0].update(departureDate=target.date.replace('-', ''),
                                  departureAirport=target.origin, arrivalAirport=target.destination)
    r = transport.send_request(page, cap, body=json.dumps(body, ensure_ascii=False))
    req = availability.Request(target, 'probe', 'g', time.monotonic(), body, {})
    verdict = availability.judge(req, r.get('status'), r.get('body'), 'g', 'probe',
                                 time.monotonic()).state if r.get('ok') else 'fetch-failed'
    data = parse_award(r.get('body'))
    return {'status': r.get('status'), 'elapsedMs': round(r.get('elapsedMs') or 0),
            'verdict': verdict, 'error': error_detail(data) if verdict != 'selected' else None,
            'responseKeys': sorted(data) if data else None}, data


def send_fare(page, cap, target, rid, fid, labels=None):
    """rid/fid 를 넣은 운임을 보낸다. 로컬 신선도 검사는 조사 목적상 현재 시각으로 통과시킨다."""
    now = time.monotonic()
    sel = Selection('g', 'probe', target, rid, fid, now)
    req = fare.build_request(sel, 'g', 'probe', target, None, json.loads(cap.body), {}, now)
    r = transport.send_request(page, cap, body=json.dumps(req.body, ensure_ascii=False))
    if not r.get('ok'):
        return {'verdict': 'fetch-failed'}
    verdict = fare.judge(req, r.get('status'), r.get('body'), req.generation, 'g', 'probe',
                         target, time.monotonic()).state
    try:
        data = json.loads(r.get('body') or 'null')
    except ValueError:
        data = None
    out = {'status': r.get('status'), 'elapsedMs': round(r.get('elapsedMs') or 0),
           'verdict': verdict, 'error': error_detail(data) if verdict != 'validated' else None}
    if labels:
        view = fare_view(r.get('body'), labels)
        out.update(pricedAs=view['pricedAs'], pricedFamily=view.get('family'))
    return out


# ---------------------------------------------------------------- 본체

def prepare(page, a, log):
    """달력 → 캡처 날짜 검색 → KRW → 운임 선택 → [다음]. 조회·운임 캡처만 만든다."""
    from live_order import ensure_krw
    steps = {}
    if 'calendar-fare-bonus' not in page.url:
        page.goto(site_drive.CALENDAR, wait_until='load', timeout=60000)
        page.wait_for_timeout(7000)
    drawn = site_drive.wait_calendar(page)
    steps['calendar'] = drawn > 10
    if not steps['calendar'] or not site_drive._date_and_search(page, a.capture_label, log, steps, drawn):
        return steps
    for _ in range(2):
        state = ensure_krw(page)
        log(f'  통화 준비: {state}')
        if state != 'bounced':
            break
        if not site_drive._date_and_search(page, a.capture_label, log, steps,
                                           site_drive.wait_calendar(page)):
            return steps
    steps['currency'] = state == 'krw'
    if not steps['currency']:
        return steps
    steps['fare'] = bool(site_drive.select_fare(page, '일반석', flight=a.flight))
    if steps['fare']:
        steps['next'] = site_drive.click_text(page, '^다음$', 12000)
        page.wait_for_timeout(3000)
    log(f'  준비 단계: {steps} · {page.url[-40:]}')
    return steps


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--port', type=int, default=9232)
    p.add_argument('--origin', default='CDG')
    p.add_argument('--destination', default='ICN')
    p.add_argument('--flight', default='902')
    p.add_argument('--capture-iso', default='2027-09-07', help='이미 열린 캡처 날짜')
    p.add_argument('--dates', default='2027-09-07,2027-09-08,2027-09-09',
                   help='비교할 열린 날짜들(첫 날짜는 반복 조회)')
    p.add_argument('--pipeline-delays', default='0,300,800,1200',
                   help='조회 송신 뒤 운임을 보낼 지연(ms) 목록. 조회 응답을 기다리지 않는다')
    p.add_argument('--family', default='KEBONUSEY', help='운임 시험에 쓸 등급')
    p.add_argument('--fare-tests', action='store_true', help='2단계 운임 시험도 한다(주문 없음)')
    p.add_argument('--overlap-delays', default='',
                   help='같은 날짜 조회 두 개를 겹쳐 보낼 간격(ms) 목록(B 전제 시험)')
    p.add_argument('--cookie-diff', action='store_true',
                   help='달력 복귀 뒤 대기 15초·조회·운임 전후로 바뀐 쿠키 이름만 기록(인계 session-changed 조사)')
    p.add_argument('--timing-at', default='',
                   help='HH:MM:SS 기준 -4초~+10초 동안 조회→운임을 순차 반복해 소요 시간만 잰다(계측기 영향 비교)')
    p.add_argument('--gap', type=float, default=1.5)
    a = p.parse_args()
    a.capture_label = f'{a.capture_iso[5:7]}월 {a.capture_iso[8:10]}일'
    dates = [d.strip() for d in a.dates.split(',') if d.strip()]
    a.pipeline_delays = [int(x) for x in a.pipeline_delays.split(',') if x.strip()]
    a.overlap_delays = [int(x) for x in a.overlap_delays.split(',') if x.strip()]
    tgt = {d: Target(d, a.origin, a.destination, a.family, 'KE', a.flight)
           for d in dates}

    report = {'tool': 'award_id_probe', 'startedAt': datetime.now().isoformat(timespec='seconds'),
              'route': f'{a.origin}-{a.destination} KE{a.flight}', 'family': a.family,
              'dates': dates, 'fareTests': a.fare_tests,
              'awards': [], 'fares': [], 'analysis': {}}

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        transport.arm(ctx)
        transport.install(page)
        watch = site_drive.HandoffWatch(ctx, page=page)
        watch.start()
        try:
            since = transport.begin_generation(page)
            report['prepare'] = prepare(page, a, log)
            snap = transport.snapshot(page, since=since)
            report['captured'] = sorted(k.split('/')[-1] for k in snap)
            if AVAIL not in snap or (a.fare_tests and FARE not in snap):
                log(f'캡처 부족: {report["captured"]} - 중단')
                return finish(report, watch, 1)
            if a.cookie_diff:
                cookie_diff(page, snap, a, tgt[dates[0]], report)
            elif a.timing_at:
                timing_loop(page, snap, a, tgt[dates[0]], report)
            else:
                run(page, snap, a, dates, tgt, report)
        finally:
            watch.stop()
        return finish(report, watch, 0)


def run(page, snap, a, dates, tgt, report):
    kept = {}   # (라벨) -> {(편, 등급): (rid, fid, ctx)}  메모리 전용

    def award(label, date):
        time.sleep(a.gap)
        meta, data = send_award(page, snap[AVAIL], tgt[date])
        rows = entries(data, tgt[date])
        keyed = {}
        for e in rows:
            if e['carrier'] == 'KE' and e['codeShare'] is False and e['nLegs'] == 1:
                keyed[(e['flightNumber'], e['family'])] = (e['recommendId'], e['flightId'],
                                                           context(tgt[date], e, data))
        kept[label] = keyed
        meta.update(label=label, date=date, flights=len({e['flightIdx'] for e in rows}),
                    fares=[{k: e[k] for k in ('flightIdx', 'fareIdx', 'flightNumber', 'carrier',
                                              'codeShare', 'family', 'seatCount', 'soldout')}
                           for e in rows],
                    flightKeys=rows[0]['flightKeys'] if rows else None,
                    fareKeys=rows[0]['fareKeys'] if rows else None)
        report['awards'].append(meta)
        log(f'조회 {label} {date}: {meta["status"]} {meta["elapsedMs"]}ms {meta["verdict"]} '
            f'· 편 {meta["flights"]} · KE 단독 항목 {len(keyed)}')
        return keyed

    labels = {dates[0]: 'target', dates[-1]: 'previous'}

    def fare_test(name, date, rid, fid, note):
        time.sleep(a.gap)
        res = send_fare(page, snap[FARE], tgt[date], rid, fid, labels)
        res.update(name=name, date=date, note=note)
        report['fares'].append(res)
        log(f'운임 {name}: {res.get("status")} {res.get("elapsedMs")}ms {res["verdict"]} '
            f'가격 날짜={res.get("pricedAs")} 등급={res.get("pricedFamily")}'
            + (f' · 오류 {json.dumps(res["error"], ensure_ascii=False)}' if res.get('error') else ''))
        return res

    d1 = dates[0]
    key = (a.flight, a.family)
    first = award('d1#1', d1)
    second = award('d1#2', d1)
    if a.fare_tests and key in second:
        rid, fid, _ = second[key]
        fare_test('control', d1, rid, fid, '방금 받은 조회의 식별자(대조군)')
    for i, d in enumerate(dates[1:], 2):
        award(f'd{i}', d)
    if a.fare_tests and key in second:
        rid, fid, _ = second[key]
        fare_test('not-latest', d1, rid, fid, '다른 날짜 조회 뒤, 앞선 d1 식별자')
        if key in first and first[key][:2] != second[key][:2]:
            rid, fid, _ = first[key]
            fare_test('older-generation', d1, rid, fid, '같은 날짜 첫 조회의 식별자')

    analysis = report['analysis']
    # 1) 구조
    analysis['structure'] = {
        f'{fl}/{fam}': {'recommendId': describe(v[0], v[2]), 'flightId': describe(v[1], v[2])}
        for (fl, fam), v in second.items()}
    # 2) 같은 날짜 반복
    analysis['repeatSameDate'] = {
        f'{k[0]}/{k[1]}': {'recommendId': diff(first[k][0], second[k][0]),
                           'flightId': diff(first[k][1], second[k][1])}
        for k in second if k in first}
    # 3) 날짜 간 비교와 치환 예측
    cross = {}
    for i, d in enumerate(dates[1:], 2):
        other = kept.get(f'd{i}', {})
        for k in second:
            if k not in other:
                continue
            a_rid, a_fid, a_ctx = second[k]
            b_rid, b_fid, b_ctx = other[k]
            cross[f'{d1}->{d} {k[0]}/{k[1]}'] = {
                'recommendId': dict(diff(a_rid, b_rid),
                                    substitutionPredicts=substitute(a_rid, a_ctx, b_ctx) == b_rid),
                'flightId': dict(diff(a_fid, b_fid),
                                 substitutionPredicts=substitute(a_fid, a_ctx, b_ctx) == b_fid)}
    analysis['crossDate'] = cross
    rule = bool(cross) and all(v['recommendId']['substitutionPredicts']
                               and v['flightId']['substitutionPredicts'] for v in cross.values())
    analysis['substitutionRuleHolds'] = rule
    log(f'같은 날짜 반복 같음: '
        f'{ {k: (v["recommendId"]["equal"], v["flightId"]["equal"]) for k, v in analysis["repeatSameDate"].items()} }')
    log(f'날짜 치환 규칙 성립: {rule}')

    # 4) 운임 시험: 최신 조회가 아닌 식별자, 조회와 겹쳐 보낸 운임(대기 없이)
    if a.fare_tests and key in second and key in kept.get(f'd{len(dates)}', {}):
        last = dates[-1]
        rid, fid, _ = second[key]
        # 판별은 운임이 가격을 낸 날짜로 한다(식별자는 두 조회에서 모두 유효하면 된다).
        analysis['pipelineIdsSameAsPrevious'] = kept[f'd{len(dates)}'][key][:2] == (rid, fid)
        for delay in a.pipeline_delays:
            award(f'reset-{delay}', last)          # 서버의 최신 조회를 마지막 날짜로 되돌린다
            time.sleep(a.gap)
            res = pipelined(page, snap, tgt[d1], rid, fid, delay, {d1: 'target', last: 'previous'})
            res.update(name=f'pipelined-{delay}ms', date=d1,
                       note='목표 날짜 조회를 보내고 응답을 기다리지 않고 운임을 보냄')
            report['fares'].append(res)
            log(f'겹침 {delay}ms: 운임이 가격 낸 날짜={res.get("pricedAs")} {res.get("verdict")} · '
                f'조회 {res.get("awardMs")}ms 운임 {res.get("fareMs")}ms · '
                f'운임 완료-조회 완료 {res.get("fareDoneAfterAwardMs")}ms')
    if a.fare_tests and key in second:
        rid, fid, _ = second[key]
        for delay in a.overlap_delays:
            award(f'reset-ov-{delay}', dates[-1])
            time.sleep(a.gap)
            res = overlapped_awards(page, snap, tgt[d1], delay)
            time.sleep(0.2)
            f = send_fare(page, snap[FARE], tgt[d1], rid, fid, labels)
            res.update(name=f'award-overlap-{delay}ms', fareAfter=f)
            report['fares'].append(res)
            log(f'조회 겹침 {delay}ms: 조회 판정 {res["verdicts"]} 오류 {res["errors"]} · 이어 보낸 운임 '
                f'{f["verdict"]} 가격 날짜={f.get("pricedAs")}'
                + (f' 오류 {json.dumps(f["error"], ensure_ascii=False)}' if f.get('error') else ''))
    analysis['pricedAsMeaning'] = 'target=목표 날짜로 가격 냄, previous=직전 조회 날짜로 가격 냄'


PIPE = """(arg) => {
  const f = (window.__KE_TX && window.__KE_TX.origFetch) || window.fetch;
  const go = (q) => { const t0 = performance.now();
    return f(q.url, {method: q.method, headers: q.headers, body: q.body, credentials: 'include',
                     cache: 'no-store'})
      .then(r => r.text().then(b => ({status: r.status, body: b, t0: t0, t1: performance.now()})))
      .catch(e => ({error: String(e), t0: t0, t1: performance.now()})); };
  const aw = go(arg.award);
  return new Promise(res => setTimeout(res, arg.delay)).then(() => {
    const fa = go(arg.fare);
    return Promise.all([aw, fa]); });
}"""


OVERLAP = """(arg) => {
  const f = (window.__KE_TX && window.__KE_TX.origFetch) || window.fetch;
  const go = (q) => { const t0 = performance.now();
    return f(q.url, {method: q.method, headers: q.headers, body: q.body, credentials: 'include',
                     cache: 'no-store'})
      .then(r => r.text().then(b => ({status: r.status, body: b, t0: t0, t1: performance.now()})))
      .catch(e => ({error: String(e), t0: t0, t1: performance.now()})); };
  const first = go(arg.award);
  return new Promise(res => setTimeout(res, arg.delay)).then(() => Promise.all([first, go(arg.award)]));
}"""


def overlapped_awards(page, snap, target, delay):
    """같은 날짜 조회 두 개를 delay 간격으로 겹쳐 보낸다(B 시차 조회의 전제)."""
    body = json.loads(snap[AVAIL].body)
    body['segmentList'][0].update(departureDate=target.date.replace('-', ''),
                                  departureAirport=target.origin, arrivalAirport=target.destination)
    q = {'url': snap[AVAIL].url, 'method': snap[AVAIL].method, 'headers': snap[AVAIL].headers,
         'body': json.dumps(body, ensure_ascii=False)}
    rows = page.evaluate(OVERLAP, {'award': q, 'delay': delay})
    verdicts, errors = [], []
    for r in rows:
        req = availability.Request(target, 'probe', 'g', time.monotonic(), body, {})
        verdicts.append(availability.judge(req, r.get('status'), r.get('body'), 'g', 'probe',
                                           time.monotonic()).state)
        errors.append((error_detail(parse_award(r.get('body'))) or {}).get('code'))
    return {'verdicts': verdicts, 'errors': errors,
            'ms': [round(r['t1'] - r['t0']) for r in rows],
            'secondSentAfterFirstMs': round(rows[1]['t0'] - rows[0]['t0'])}


def fare_view(body, labels):
    """운임 응답이 어느 날짜·등급으로 가격을 냈는지(원문 없음)."""
    try:
        data = json.loads(body or 'null')
    except ValueError:
        return {'pricedAs': 'invalid-json'}
    if not isinstance(data, dict):
        return {'pricedAs': 'invalid-schema'}
    try:
        leg = data['boundList'][0]['segmentList'][0]
    except (KeyError, IndexError, TypeError):
        return {'pricedAs': 'no-itinerary', 'error': error_detail(data)}
    day = str(leg.get('departureDateTime') or '')[:8]
    names = {d.replace('-', ''): n for d, n in labels.items()}
    return {'pricedAs': names.get(day, 'other-date'), 'family': leg.get('fareFamily'),
            'flightNumber': leg.get('flightNumber'), 'error': error_detail(data)}


def pipelined(page, snap, target, rid, fid, delay, labels):
    award_body = json.loads(snap[AVAIL].body)
    award_body['segmentList'][0].update(departureDate=target.date.replace('-', ''),
                                        departureAirport=target.origin,
                                        arrivalAirport=target.destination)
    fare_body = json.loads(snap[FARE].body)
    fare_body['recommendList'][0].update(recommendId=rid, flightId=fid)
    q = lambda cap, body: {'url': cap.url, 'method': cap.method, 'headers': cap.headers,
                           'body': json.dumps(body, ensure_ascii=False)}
    aw, fa = page.evaluate(PIPE, {'award': q(snap[AVAIL], award_body),
                                  'fare': q(snap[FARE], fare_body), 'delay': delay})
    req = availability.Request(target, 'probe', 'g', time.monotonic(), award_body, {})
    out = {'awardStatus': aw.get('status'), 'fareStatus': fa.get('status'),
           'awardVerdict': availability.judge(req, aw.get('status'), aw.get('body'), 'g', 'probe',
                                              time.monotonic()).state,
           'awardMs': round(aw['t1'] - aw['t0']), 'fareMs': round(fa['t1'] - fa['t0']),
           'fareSentAfterAwardMs': round(fa['t0'] - aw['t0']),
           'fareDoneAfterAwardMs': round(fa['t1'] - aw['t1'])}
    out.update(fare_view(fa.get('body'), labels))
    out['verdict'] = 'validated-target' if (out['pricedAs'] == 'target' and not out['error']
                                            and out.get('family') == target.family
                                            and out.get('flightNumber') == target.flight) else 'no'
    return out


def timing_loop(page, snap, a, target, report):
    """예매와 같은 순서(조회 응답 → 운임)를 반복해 시각별 소요 시간을 남긴다. 주문 없음."""
    from runtime import measure_clock, resolve_time
    clock = measure_clock()
    offset = clock['offset'] if clock.get('ok') else 0.0
    at = resolve_time(a.timing_at).timestamp() - offset      # 로컬 시계 기준 개방 시각
    report['timing'] = {'at': a.timing_at, 'clock': clock, 'rows': []}
    while time.time() < at - 4:
        time.sleep(0.05)
    key = (a.flight, a.family)
    while time.time() < at + 10:
        sent = time.time()
        meta, data = send_award(page, snap[AVAIL], target)
        mid = time.time()
        ids = {(e['flightNumber'], e['family']): (e['recommendId'], e['flightId'])
               for e in entries(data, target) if e['carrier'] == 'KE' and e['codeShare'] is False}
        row = {'sentRel': round(sent - at, 3), 'awardMs': meta['elapsedMs'], 'award': meta['verdict']}
        if key in ids:
            f = send_fare(page, snap[FARE], target, *ids[key])
            row.update(fareRel=round(mid - at, 3), fareMs=f.get('elapsedMs'), fare=f['verdict'])
        report['timing']['rows'].append(row)
        log(f'순차 {row}')
    ok = [r for r in report['timing']['rows'] if r.get('fare') == 'validated']
    if ok:
        import statistics
        report['timing']['awardMedianMs'] = statistics.median(r['awardMs'] for r in ok)
        report['timing']['fareMedianMs'] = statistics.median(r['fareMs'] for r in ok)
        log(f"중앙값 조회 {report['timing']['awardMedianMs']}ms 운임 {report['timing']['fareMedianMs']}ms")


def _cookie_digest(page):
    import hashlib
    rows = page.context.cookies(['https://www.koreanair.com'])
    out = {f"{c['name']}|{c['domain']}|{c['path']}": hashlib.sha256(c['value'].encode()).hexdigest()[:12]
           for c in rows}
    member = page.evaluate('()=>sessionStorage.getItem("loggedInUserInfo")') or ''
    out['<sessionStorage.loggedInUserInfo>'] = hashlib.sha256(member.encode()).hexdigest()[:12]
    return out


def _changes(a, b):
    return {'changed': sorted(k for k in a if k in b and a[k] != b[k]),
            'added': sorted(k for k in b if k not in a), 'removed': sorted(k for k in a if k not in b)}


def cookie_diff(page, snap, a, target, report):
    """live_order 의 결속(T-15초)~주문 전 점검 구간을 흉내 낸다. 이름만 기록, 값·해시는 저장하지 않는다."""
    page.goto(site_drive.CALENDAR, wait_until='load', timeout=60000)
    page.wait_for_timeout(8000)
    rounds = []
    for n in range(3):
        s0 = _cookie_digest(page)
        page.wait_for_timeout(15000)
        s1 = _cookie_digest(page)
        meta, data = send_award(page, snap[AVAIL], target)
        s2 = _cookie_digest(page)
        ids = {(e['flightNumber'], e['family']): (e['recommendId'], e['flightId'])
               for e in entries(data, target) if e['carrier'] == 'KE' and e['codeShare'] is False}
        f = send_fare(page, snap[FARE], target, *ids[(a.flight, a.family)]) if (a.flight, a.family) in ids else {}
        s3 = _cookie_digest(page)
        row = {'idle15s': _changes(s0, s1), 'award': _changes(s1, s2), 'fare': _changes(s2, s3),
               'awardVerdict': meta['verdict'], 'fareVerdict': f.get('verdict')}
        rounds.append(row)
        log(f'쿠키 변화 {n + 1}: {json.dumps(row, ensure_ascii=False)}')
    report['cookieDiff'] = rounds


def finish(report, watch, code):
    report['orderRequests'] = site_drive.watch_counts(watch.records())
    report['finishedAt'] = datetime.now().isoformat(timespec='seconds')
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f'award-id-probe-{datetime.now():%Y%m%d-%H%M%S}.json'
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'주문 요청 계수: {report["orderRequests"]}')
    log(f'결과: {path}')
    return code


if __name__ == '__main__':
    sys.exit(main())
