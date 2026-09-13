"""09시 API 예매 실행기: 달력 조회 → 운임 → 주문(pnr)을 연속으로 보낸다.

**이 실행기는 실제 주문을 만든다.** 결제는 하지 않으므로 예약 확정은 아니고
임시 좌석 보유만 생긴다(FACTS §11). 최종 승인은 사용자만 한다.

한 프로세스로 준비와 발사를 모두 한다. 캡처는 이 프로세스 메모리에만 있으므로
중간에 프로세스를 내리면 다시 준비해야 한다.

  준비  이미 열린 날짜로 사이트를 한 번 통과시켜 3구간의 헤더·본문을 캡처한다.
        사이트의 주문 요청은 네트워크에서 막는다(D2). 막지 못하면 중단한다.
        캡처를 메모리로 옮긴 뒤 달력 조회 화면으로 돌아간다.
  발사  달력 문서에서 목표 날짜로 조회 → **응답에서 목표 등급의 recommendId/flightId 를
        골라 운임 요청을 다시 구성** → 운임 → 필수 검증 → 주문.
        판정은 pipeline(A2 조회·A3 운임·A4a 필수 검증·A4b 주문 준비·A4c 응답)을 거친다(D3).

캡처한 운임 본문을 그대로 재사용하면 준비 때 고른 항공편을 주문하게 된다.
그래서 발사에서는 반드시 최신 조회 응답으로 다시 연결한다.
"""
from __future__ import annotations
import argparse
import json
from uuid import uuid4
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'dev'))
import permit  # noqa: E402
import pipeline  # noqa: E402
import site_drive  # noqa: E402
import transport  # noqa: E402
from availability import Target  # noqa: E402
from order_flow import Checks, Event, OrderFlow  # noqa: E402
from runtime import KST  # noqa: E402

AVAIL = '/api/ap/booking/avail/awardAvailability'
FARE = '/api/ap/booking/avail/fareInformation'
ORDER = '/api/ap/booking/traveller/inputTravellers'
STATE = ROOT / 'dev-shots' / 'state'
# 캡처 준비 등급 표기 → 운임 계열. 모르는 표기는 None 으로 두어 여정 불일치로 본다.
CABIN_FAMILY = {'일반석': 'KEBONUSEY', '프레스티지': 'KEBONUSPR'}


def log(msg):
    print(f'[{datetime.now(KST).strftime("%H:%M:%S.%f")[:-3]}] {msg}', flush=True)


# 준비 통과를 시작하기 전에 남기는 표지. 프로세스가 준비 중에 죽으면 이 상태로 남아
# 다음 실행을 막는다(차단 실패로 사이트 주문이 생겼을 수 있다).
PREPARING = 'preparing'
# 준비 중 관찰한 주문 요청이 전부 차단됐다는 **로컬** 판정. 서버 확인이 아니다.
PREP_CLEAN = 'prep-no-unblocked-order'
NON_BLOCKING = ('resolved', PREP_CLEAN)


def intent_path(day):
    return STATE / f'order-intent-{day}.json'


def unresolved_intent(day):
    """재시작을 넘어 중복 주문을 막는다. 미해결 기록이 있으면 새 주문을 거부한다."""
    path = intent_path(day)
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'state': 'unreadable'}
    if type(rec) is not dict:
        return {'state': 'unreadable'}
    return rec if rec.get('state') not in NON_BLOCKING else None


def unresolved_intents():
    """모든 실행일의 미해결 주문 기록. 자정을 넘긴 재시작도 막는다(검토 9aaff071 P1)."""
    found = {}
    if STATE.exists():
        for path in sorted(STATE.glob('order-intent-*.json')):
            day = path.stem[len('order-intent-'):]
            rec = unresolved_intent(day)
            if rec:
                found[day] = rec
    return found


def record_intent(day, state, **extra):
    # 예약번호 원문·토큰은 남기지 않는다. 상태 표지만 남긴다. 직전 상태 하나를 함께 보존한다.
    # 전송 전후 기록이므로 디렉터리까지 동기화한다(검토 9aaff071 P2).
    path = intent_path(day)
    previous = None
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding='utf-8'))
            previous = ({k: old.get(k) for k in ('state', 'at', 'why')}
                        if type(old) is dict else {'state': 'unreadable'})
        except (OSError, ValueError):
            previous = {'state': 'unreadable'}
    permit.durable_json(path, {'day': day, 'state': state, 'at': datetime.now(KST).isoformat(),
                               **extra, 'previous': previous})


def main():
    ap = argparse.ArgumentParser(description='09시 API 예매 실행기')
    ap.add_argument('--port', type=int, default=9232, choices=[9232, 9242])
    ap.add_argument('--date', required=True, help='목표 출발일 YYYY-MM-DD')
    ap.add_argument('--flight', default='901')
    ap.add_argument('--origin', default='ICN')
    ap.add_argument('--destination', default='CDG')
    ap.add_argument('--carrier', default='KE')
    ap.add_argument('--own-mileage', type=int, default=None,
                    help='사용자가 이번 실행 직전에 확인한 본인 가용 마일리지. 없으면 잔액 미확인으로 '
                         '주문하지 않는다(서버 검증 아님, 값은 로그에 쓰지 않음)')
    ap.add_argument('--own-mileage-valid-hours', type=float, default=6.0,
                    help='--own-mileage 확인값을 쓸 수 있는 시간. 기본 6시간')
    ap.add_argument('--reuse-member-check', action='store_true',
                    help='준비 통과(캡처 날짜·등급)에서 본 회원·승객 확인을 목표 날짜·등급에 재사용한다. '
                         '회원 검증의 여정 의존성은 미확인이므로 사용자가 판단해 켤 때만 쓴다. '
                         '승객·노선이 다르면 켜도 거부한다')
    ap.add_argument('--family', default='KEBONUSPR', help='KEBONUSPR=프레스티지 KEBONUSEY=일반석')
    ap.add_argument('--at', default='', help='발사 시각 HH:MM:SS(--day 의 KST). 비우면 즉시. '
                                          '시작 때 이미 지났거나 무장이 늦으면 발사하지 않는다')
    ap.add_argument('--late-limit', type=float, default=permit.DEFAULT_LATE_LIMIT,
                    help='--at 뒤 발사 허용 지연(초). 대기에서 늦게 깨면 발사하지 않는다. 기본 3')
    ap.add_argument('--status', action='store_true',
                    help='--day 의 주문 의도·전송권 상태만 읽어 보여 준다. 브라우저·사이트 접속 없음')
    ap.add_argument('--dry', action='store_true',
                    help='API 주문 전송 직전까지만. 조회·운임 요청은 실제로 보낸다. 캡처 준비의 '
                         '주문 요청은 막지만 실사이트에서 막힘을 확인하지 않았다(무주문 보장 아님)')
    ap.add_argument('--day', default=None, help='실행일 YYYY-MM-DD(KST). 기본은 오늘')
    ap.add_argument('--capture-date', default='',
                    help="캡처용 이미 열린 날짜 라벨 예: '09월 07일'. 발사 모드에서 필수. 이 날짜로 "
                         "사이트를 한 번 통과시켜 이번 실행의 캡처를 만든다. 주문 요청은 막고, 막지 못하면 중단")
    ap.add_argument('--capture-cabin', default='일반석')
    ap.add_argument('--capture-max-age', type=float, default=3600.0,
                    help='캡처 최대 나이(초). 무장 대기·발사 직전에 이보다 오래되면 중단한다. 기본 3600')
    ap.add_argument('--continue-payment', action='store_true',
                    help='주문 뒤 게이트 주문 참조 확인 → 동의 2개(상태 확인) → 마일리지 → Npay → 결제하기 → '
                         '새 Npay 창·금액·결제 세션 참조 판정(D5). 로컬 시험만 통과, 실사이트 미검증. '
                         '제공자 창 안에서는 누르지 않는다')
    ap.add_argument('--gate-only', action='store_true',
                    help='pnr 이후 결제 게이트로 옮겨 대조만 하고 멈춘다. 동의·Npay·결제하기는 사용자가 한다')
    ap.add_argument('--payment-only', default='',
                    help="이미 결제 게이트에 있는 주문으로 결제 단계만 시험한다. 마일 표기 예: '35,000'. "
                         "어느 주문인지 대조할 수 없어 D1 이후에는 아무것도 누르지 않는다")
    ap.add_argument('--arm-file', default='',
                    help='이 파일이 생길 때까지 발사 대기를 시작하지 않는다. '
                         'AGENTS: 실전 대기 시작은 사용자가 누른다')
    a = ap.parse_args()
    if a.day is None:
        a.day = datetime.now(KST).strftime('%Y-%m-%d')

    if a.status:
        return show_status(a.day)

    if a.payment_only:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
            page = browser.contexts[0].pages[0]
            log(f'결제 단계만 시험: 현재 {page.url[:70]}')
            out = site_drive.payment_pass(page, flight=a.flight, date=a.date,
                                          mileage=a.payment_only, log=log, navigate=False)
            log(f'결과: {out}')
            return 0 if out.get('matched') and out['steps'].get('payment') else 2

    # --dry 도 조회·운임을 보내고 캡처 준비를 거친다. 미해결 주문·남은 전송권이 있으면 모두 거부한다.
    pending = unresolved_intents()
    if pending:
        log(f'미해결 주문 기록이 있다: {pending}. 정상 예약 조회로 확인한 뒤 해당 '
            f'{STATE}/order-intent-<날짜>.json 을 state=resolved 로 바꾼다')
        return 2
    held = permit.existing_permit(STATE)
    if held:
        log(f'주문 전송권이 이미 쓰였다: {held}. 새 주문을 보내지 않는다. '
            f'정상 예약 조회로 확인한 뒤 사용자가 {permit.permit_path(STATE)} 를 치운다')
        return 2
    # 실행일과 발사 시각은 시작할 때 한 번 고정한다(D4).
    try:
        fire_at = permit.parse_at(a.day, a.at, KST) if a.at else None
    except ValueError as exc:
        log(f'실행일·발사 시각 형식 오류: {exc}')
        return 2
    why = permit.start_check(day=a.day, now=datetime.now(KST), fire_at=fire_at)
    if why:
        log(f'시작 거부: {why} (실행일 {a.day}, 발사 {a.at or "즉시"})')
        return 2

    try:
        target = Target(a.date, a.origin, a.destination, a.family, a.carrier, a.flight)
    except ValueError as exc:
        log(f'목표가 올바르지 않다: {exc}')
        return 2
    started = time.monotonic()
    balance = pipeline.BalanceEvidence(started, started + a.own_mileage_valid_hours * 3600,
                                       a.own_mileage)
    if a.own_mileage is None:
        log('**--own-mileage 가 없다. 잔액 미확인으로 주문 단계에서 멈춘다(--dry 판정 확인용).**')
    ledger = Ledger()
    try:
        return run(a, ledger, target, balance, fire_at)
    finally:
        log(f'구간별 호출 수: {ledger.summary()}')


def show_status(day):
    """읽기 전용 상태. 주문 의도·전송권을 보여 주고 사용자가 할 일을 알린다. 아무것도 바꾸지 않는다."""
    intent = None
    if intent_path(day).exists():
        try:
            intent = json.loads(intent_path(day).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            intent = {'state': 'unreadable'}
    held = permit.existing_permit(STATE)
    blocking = unresolved_intents()
    log(f'실행일 {day}')
    log(f'  주문 의도: {intent}')
    log(f'  전송권(날짜 무관): {held}')
    log(f'  모든 실행일의 미해결 의도: {blocking}')
    if blocking or held:
        log('  새 실행은 거부된다. 정상 예약 조회로 이 실행일의 주문 상태를 확인한 뒤 '
            '사용자가 의도 기록을 resolved 로 바꾸고 전송권 파일을 치운다. 프로그램은 대신 치우지 않는다.')
        return 2
    log('  남은 주문 의도·전송권 없음')
    return 0


def missing_captures(snap, max_age, now):
    """없거나·너무 오래된 메모리 캡처 경로. 나이를 안 보면 만료된 세션으로 발사한다."""
    bad = []
    for path in (AVAIL, FARE, ORDER):
        cap = snap.get(path)
        if cap is None or (max_age is not None and now - cap.at > max_age):
            bad.append(path)
    return bad


def same_origin(page, snap):
    """현재 문서가 캡처 오리진과 같은지. 다르면 발사 요청을 보낼 수 없다."""
    from urllib.parse import urlsplit
    parts = urlsplit(page.url or '')
    here = f'{parts.scheme}://{parts.netloc}'
    return bool(snap) and all(cap.origin == here for cap in snap.values())


class Ledger:
    """구간별 호출 수. 경로 이름과 횟수만 남기고 값·원문은 담지 않는다.

    prep: 준비 중 사이트가 보낸 요청(주문 요청 차단/미차단 포함)
    fire: 이 실행기가 보낸 요청
    handoff: 인계 중 관찰한 요청 판정 수
    """
    PHASES = ('prep', 'fire', 'handoff')

    def __init__(self):
        self.counts = {phase: {} for phase in self.PHASES}

    def add(self, phase, name, n=1):
        if phase not in self.counts or not isinstance(name, str) or type(n) is not int:
            raise ValueError('invalid-ledger-entry')
        self.counts[phase][name] = self.counts[phase].get(name, 0) + n

    def summary(self):
        return {phase: dict(sorted(items.items())) for phase, items in self.counts.items()}


def run(a, ledger, target, balance, fire_at):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        transport.arm(ctx)
        transport.install(page)   # 현재 문서에도 건다
        log(f'후킹 설치. 현재 {page.url[:70]}')

        # 캡처는 이번 실행의 준비 세대에서 만든 것만 쓴다. 이전 문서·이전 실행의 캡처와
        # 섞으면 헤더 시점과 선택 상태가 다른 요청 조합이 된다(검토 165a57e4 P1).
        if not a.capture_date:
            log('--capture-date 가 필요하다. 발사 캡처는 이번 실행의 준비 통과에서만 만든다.')
            return 2
        log(f'{a.capture_date} {a.capture_cabin} 로 준비 통과를 시작한다(주문 요청은 차단)')
        record_intent(a.day, PREPARING, why='capture-pass')
        since = transport.begin_generation(page)
        steps = site_drive.capture_pass(page, a.capture_date, cabin=a.capture_cabin, log=log)
        log(f'준비 통과 결과: {steps}')
        if not prep_counts_clean(a, ledger, steps):
            return 2
        if not site_drive.capture_complete(steps):
            log('준비 통과가 승객·연락처 확인까지 끝나지 않았다 - 중단. 화면을 확인하고 다시 준비한다')
            return 2
        prepared = time.monotonic()
        reached_order = (steps.get('orderRequests') or {}).get('seen', 0) >= 1
        snap = transport.snapshot(page, since=since)
        missing = missing_captures(snap, a.capture_max_age, time.time())
        if missing:
            log(f'이번 준비에서 캡처하지 못한 경로: {[m.split("/")[-1] for m in missing]} - 중단')
            return 2
        log('3구간 캡처를 메모리에 확보(이번 준비 세대)')
        # 사이트가 승객·연락처 확인을 지나 주문 요청을 보냈다 = 준비 시점 회원·승객 검증 통과 관측.
        # 그 확인이 어느 승객·여정에서 났는지 함께 묶는다(검토 2331c183 P1).
        member = pipeline.member_evidence_from_capture(
            snap[AVAIL].body, snap[ORDER].body, observed=prepared,
            expires=prepared + a.capture_max_age, reached_order_request=reached_order,
            family=CABIN_FAMILY.get(a.capture_cabin), reuse_itinerary=a.reuse_member_check)
        if member is None:
            log('캡처 본문에서 승객·여정을 읽지 못했다 - 회원 증거를 만들 수 없어 중단')
            return 2

        # 발사는 달력 조회 화면에서 시작한다. 게이트 문서에 머물 필요가 없다.
        back = site_drive.return_to_calendar(page, log=log)
        if not prep_counts_clean(a, ledger, back):
            return 2
        if back.get('error') or back['cells'] <= 10:
            log('달력 셀이 그려지지 않았다. 검색 조건·로그인을 확인하고 다시 준비한다.')
            return 2
        if not same_origin(page, snap):
            log(f'현재 문서 오리진이 캡처와 다르다: {page.url[:60]} - 중단')
            return 2

        # 서버 Date 와 로컬 시각을 대조해 오차 방향만 본다. 시계를 고치지는 않는다.
        skew = page.evaluate("""() => {
          const t0 = Date.now();
          return fetch(location.origin + '/kr/ko', {method: 'HEAD', cache: 'no-store'})
            .then(r => ({date: r.headers.get('date'), t0: t0, t1: Date.now()}))
            .catch(e => ({error: String(e)})); }""")
        if skew.get('date'):
            from email.utils import parsedate_to_datetime
            try:
                server = parsedate_to_datetime(skew['date']).timestamp()
                mid = (skew['t0'] + skew['t1']) / 2000.0
                rtt = (skew['t1'] - skew['t0']) / 1000.0
                log(f'시계 대조: 로컬-서버 {mid - server:+.1f}초 (왕복 {rtt:.2f}s, '
                    f'서버 Date 는 초 단위라 ±1초 불확실). 양수면 로컬이 빠름')
            except (TypeError, ValueError) as exc:
                log(f'시계 대조 실패: {exc}')
        else:
            log(f'시계 대조 불가: {skew}')

        def still_ready(full=True):
            """발사 준비가 아직 유효한지. full 이면 달력 셀까지 읽는다(페이지 왕복 1회)."""
            gone = missing_captures(snap, a.capture_max_age, time.time())
            if gone:
                return f'캡처 만료: {[g.split("/")[-1] for g in gone]}'
            if not same_origin(page, snap):
                return f'문서 오리진 변경: {page.url[:60]}'
            if 'calendar-fare-bonus' not in (page.url or ''):
                return f'달력 조회 화면 이탈: {page.url[-40:]}'
            if full and not site_drive.on_calendar(page):
                return '달력 셀이 보이지 않음'
            return None

        if a.arm_file:
            armed = Path(a.arm_file)
            log(f'**사용자 무장 대기** — 발사를 시작하려면 {armed} 를 만든다')
            log('AGENTS: 실전 대기 시작은 사용자가 누른다. 프로그램이 대신 무장하지 않는다')
            last_check = time.monotonic()
            while not armed.exists():
                if fire_at is not None and datetime.now(KST) >= fire_at:
                    log('발사 시각까지 무장되지 않았다 - 발사하지 않는다')
                    return 2
                if time.monotonic() - last_check >= 5:
                    last_check = time.monotonic()
                    why = still_ready()
                    if why:
                        log(f'무장 전에 준비가 무효가 됐다({why}) - 중단. 다시 준비한다')
                        return 2
                time.sleep(0.5)
            # 발사 시각이 지난 뒤 확인된 무장은 늦은 실제 주문이 되므로 거부한다(9/13 검토 P2).
            if fire_at is not None and datetime.now(KST) >= fire_at:
                log('발사 시각이 지난 뒤 무장이 확인됐다 - 발사하지 않는다')
                return 2
            log('사용자 무장 확인. 발사 대기로 넘어간다')

        if fire_at is not None:
            wait = (fire_at - datetime.now(KST)).total_seconds()
            log(f'{a.day} {a.at} 까지 {wait:.0f}초 대기. 이 프로세스를 내리면 메모리 캡처가 사라진다')
            final_checked = False
            while True:
                left = (fire_at - datetime.now(KST)).total_seconds()
                # 정각 전에 쏘지 않는다. 미개방 응답이면 재시도 없이 끝나기 때문이다.
                if left <= 0:
                    break
                if left > 60:
                    time.sleep(min(60, left - 30))
                    left = (fire_at - datetime.now(KST)).total_seconds()
                    why = still_ready()
                    log(f'대기 {left:.0f}초 남음 · 준비 유지={why is None} · {page.url[-32:]}')
                    if why:
                        log(f'대기 중 준비가 무효가 됐다({why}) - 중단')
                        return 2
                elif not final_checked:
                    # 마지막 60초 안에 한 번 달력 셀까지 확인하고, 정각에는 가벼운 확인만 한다.
                    final_checked = True
                    why = still_ready()
                    if why:
                        log(f'정각 전 마지막 확인에서 준비 무효({why}) - 중단')
                        return 2
                else:
                    time.sleep(max(0.0, left) + 0.001)

        # 대기에서 늦게 깼거나(절전 등) 날짜가 바뀌었으면 발사하지 않는다.
        why = permit.fire_check(day=a.day, now=datetime.now(KST), fire_at=fire_at,
                                late_limit=a.late_limit)
        if why:
            log(f'발사 거부: {why}')
            return 2
        why = still_ready(full=fire_at is None)
        if why:
            log(f'발사 직전 준비 무효({why}) - 주문하지 않는다')
            return 2
        return fire(page, snap, a, ledger, pipeline.Pipeline(target, member=member, balance=balance))


def prep_counts_clean(a, ledger, observed):
    """준비 구간 관찰 수를 장부에 넣고, 막지 못한 주문 요청이 있었으면 기록하고 False."""
    for name, n in (observed.get('siteRequests') or {}).items():
        ledger.add('prep', name, int(n))
    orders = observed.get('orderRequests') or {}
    ledger.add('prep', 'inputTravellers-blocked', int(orders.get('blocked', 0)))
    ledger.add('prep', 'inputTravellers-unblocked', int(orders.get('unblocked', 0)))
    if observed.get('error'):
        ledger.add('prep', f'error:{observed["error"]}')
    if orders.get('unblocked', 0):
        record_intent(a.day, 'prep-order-possible', why='prep-order-not-blocked')
        log('**준비 중 주문 요청을 막지 못했다. 사이트가 주문을 만들었을 수 있다. 중단한다.**')
        return False
    record_intent(a.day, PREP_CLEAN)
    return True


def send_counted(page, cap, ledger, name, body=None):
    """발송하며 시도·실제 fetch 호출·미발송·예외를 나눠 센다."""
    ledger.add('fire', f'{name}-attempt')
    try:
        r = transport.send_request(page, cap, body=body)
    except Exception:
        ledger.add('fire', f'{name}-exception')
        raise
    if r.get('error') in ('origin-changed', 'not-a-capture'):
        ledger.add('fire', f'{name}-not-sent')
    else:
        # 네트워크 오류여도 fetch 는 호출됐다. 서버 도달 여부는 따로 모른다.
        ledger.add('fire', f'{name}-fetched')
    return r


def fire(page, snap, a, ledger, pl):
    """메모리 캡처로 현재 문서에서 조회→운임→필수 검증→주문을 보낸다. 주문 뒤 인계는 옵션에 따른다."""
    t0 = time.monotonic()
    log(f'T0 발사: {a.date} {a.carrier}{a.flight} {a.origin}-{a.destination} {a.family} '
        f'· 문서 {page.url[-40:]}')

    body, why = pl.award_body(snap[AVAIL].body, snap[AVAIL].headers)
    if why:
        log(f'조회 본문 구성 실패: {why} - 주문하지 않는다')
        return 2
    r = send_counted(page, snap[AVAIL], ledger, 'awardAvailability', body=body)
    state = pl.judge_award(r)
    log(f'조회 status={r.get("status")} {r.get("elapsedMs",0):.0f}ms 판정={state} '
        f'(+{time.monotonic()-t0:.3f}s)')
    if state != 'selected':
        log(f'조회 판정 {state} - 주문하지 않는다')
        return 2

    fbody, why = pl.fare_body(snap[FARE].body, snap[FARE].headers)
    if why:
        log(f'운임 본문 구성 실패: {why} - 주문하지 않는다')
        return 2
    r2 = send_counted(page, snap[FARE], ledger, 'fareInformation', body=fbody)
    state = pl.judge_fare(r2)
    log(f'운임 status={r2.get("status")} {r2.get("elapsedMs",0):.0f}ms 판정={state} '
        f'(+{time.monotonic()-t0:.3f}s)')
    if state != 'validated':
        log(f'운임 판정 {state} - 주문하지 않는다')
        return 2

    state = pl.prepare_order(snap[ORDER].body)
    log(f'필수 검증·주문 준비 판정={state} (+{time.monotonic()-t0:.3f}s)')
    if a.dry:
        log(f'--dry: 주문 직전 중단. T0→판정 {time.monotonic()-t0:.3f}초')
        return 0 if state == 'ready' else 2
    if state != 'ready':
        log(f'필수 검증 {state} - 주문하지 않는다')
        return 2

    # 배타 전송권: 같은 실행일에 한 프로세스만 주문 요청을 보낸다. 자동으로 풀지 않는다(D4).
    run_id = uuid4().hex[:12]
    got = permit.acquire(STATE, day=a.day, run_id=run_id, now_iso=datetime.now(KST).isoformat(),
                         target={'date': a.date, 'origin': a.origin, 'destination': a.destination,
                                 'flight': a.flight, 'family': a.family})
    if got is None:
        ledger.add('fire', 'inputTravellers-permit-denied')
        log('**다른 실행이 이미 주문 전송권을 가졌다. 주문하지 않는다.**')
        return 2
    flow = OrderFlow()
    flow.prepare(Checks(target_matches=True, fare_is_current=True, session_matches=True,
                        required_checks_passed=True, no_unresolved_order=True, user_started=True))
    flow.advance(Event.RECORD_INTENT)
    record_intent(a.day, 'sending', date=a.date, flight=a.flight, family=a.family, runId=run_id)
    flow.advance(Event.BEGIN_SEND)
    # 주문 본문은 캡처 원문을 그대로 보낸다. 구조 판정은 prepare_order 에서 끝났다.
    try:
        r3 = send_counted(page, snap[ORDER], ledger, 'inputTravellers')
    except BaseException:
        flow.advance(Event.INTERRUPT)
        record_intent(a.day, 'unknown', why='order-send-exception', runId=run_id)
        raise
    outcome = pl.judge_order(r3)
    elapsed = time.monotonic() - t0
    log(f'주문 status={r3.get("status")} {r3.get("elapsedMs",0):.0f}ms 판정={outcome.state} '
        f'(+{elapsed:.3f}s)')
    if outcome.state != 'order-recorded':
        # 어떤 판정도 주문 미생성의 증거가 아니다. 재전송하지 않는다. 전송권도 남긴다.
        flow.advance(Event.INTERRUPT)
        record_intent(a.day, 'unknown', why=outcome.state, runId=run_id)
        log('주문 응답을 이번 목표 주문으로 확인하지 못했다 - 재전송 금지. 정상 예약 조회로 확인한다')
        return 2
    order = outcome.order
    flow.advance(Event.CONFIRM_ORDER)
    record_intent(a.day, 'ordered', segmentStatus=order.segment_status,
                  amountsMatched=order.amounts_matched, runId=run_id)
    log(f'주문 응답 확인  **T0 → 주문 응답 {elapsed:.3f}초** (보유 시작 시각은 미확인)')
    mileage = pl.mileage_label()
    if a.gate_only:
        log(f'결제 게이트로 이동 (주문 참조 대조 + 문구 {a.date}, {mileage} 마일, KRW)')
        # 주문 참조 원문은 메모리로만 넘긴다. 로그에는 판정 상태만 남는다.
        out = site_drive.gate_handoff(page, date=a.date, mileage=mileage,
                                      reference=order.reference, ordered_at=order.received,
                                      log=log)
        ledger.add('handoff', f'gate:{out["order"]}')
        for name, n in (out.get('counts') or {}).items():
            ledger.add('handoff', name, int(n))
        if out['matched']:
            log('**게이트 주문 참조가 이번 주문과 같다. 동의부터 사용자가 진행한다. '
                'Npay 도착·동의 완료는 미확인.**')
            return 0
        log(f'**주문 응답은 받았지만 게이트가 이번 주문으로 확인되지 않았다({out["order"]}). '
            '사용자 확인 필요.**')
        return 2
    if not a.continue_payment:
        log('결제창은 브라우저에서 이어간다. 최종 승인은 사용자가 한다.')
        return 0

    # 기존 예매 절차로 동의~결제하기까지 이어간다(사용자 확정 2026-09-13, D5).
    log(f'결제 단계 시작 (마일 표시 대조값 {mileage})')
    flow.advance(Event.BEGIN_HANDOFF)
    outcome = site_drive.payment_pass(page, flight=a.flight, date=a.date,
                                      mileage=mileage, reference=order.reference,
                                      ordered_at=order.received, log=log,
                                      origin=a.origin, destination=a.destination,
                                      amount=pl.quote.total_amount)
    ledger.add('handoff', f'payment:{outcome.get("order")}')
    ledger.add('handoff', f'stage:{outcome.get("stage")}')
    for name, n in (outcome.get('counts') or {}).items():
        ledger.add('handoff', name, int(n))
    log(f'결제 단계 결과: stage={outcome.get("stage")} completed={outcome.get("completed")} '
        f'창={outcome.get("paymentWindow")}')
    record_intent(a.day, 'ordered', segmentStatus=order.segment_status, runId=run_id,
                  handoff=outcome.get('stage'), paymentWindowReached=bool(outcome.get('completed')))
    if not outcome.get('completed'):
        flow.advance(Event.HANDOFF_FAILED)
        log('**결제창 인계가 완료되지 않았다. 주문은 남아 있을 수 있다 - 재주문하지 않고 사용자가 확인한다.**')
        return 2
    flow.advance(Event.CONFIRM_PAYMENT_WINDOW)
    log('이번 주문의 Npay 결제창에 도착했다. **최종 승인은 사용자가 한다.**')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
