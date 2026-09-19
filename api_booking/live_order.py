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
import math
import os
import re
from uuid import uuid4
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / 'dev'))
import permit  # noqa: E402
import pipeline  # noqa: E402
import site_drive  # noqa: E402
import transport  # noqa: E402
import recovery  # noqa: E402
import order_evidence  # noqa: E402
import connected_bridge  # noqa: E402
import explicit_retry  # noqa: E402
from evidence import order_amount_diagnostic  # noqa: E402
from availability import Target  # noqa: E402
from order_flow import Checks, Event, OrderFlow  # noqa: E402
from runtime import KST, measure_clock  # noqa: E402

AVAIL = '/api/ap/booking/avail/awardAvailability'
FARE = '/api/ap/booking/avail/fareInformation'
ORDER = '/api/ap/booking/traveller/inputTravellers'
STATE = ROOT / 'dev-shots' / 'state'
# 캡처 준비 등급 표기 → 운임 계열. 모르는 표기는 None 으로 두어 여정 불일치로 본다.
CABIN_FAMILY = {'일반석': 'KEBONUSEY', '프레스티지': 'KEBONUSPR'}
# 선발사는 NTP 불확실성이 이 값 이하로 측정됐을 때만 허용한다(초).
CLOCK_MAX_UNCERTAINTY = 0.1
PRE_FIRE_MAX_MS = 3000
OBSERVE_MAX = 3
# 주문 전송 전에 준비가 무효가 된 경우의 종료 코드. 체인(api_day)은 이 코드에서만 재준비한다.
EXIT_REPREPARE = 3
# 무장 점검: 토큰이 발사 시각 + 이 초 이후까지 유효해야 한다.
TOKEN_MARGIN = 300
# 미개방 형태 대조에 쓰는 필드. 식별자는 없다.
SHAPE_FIELDS = ('status', 'keys', 'code', 'errorCode', 'resultCode', 'responseCode',
                'emptyFare', 'bounds', 'flights')
# 증거에 남기는 응답 코드 형식. 이 형식이 아닌 값은 형(type)만 남긴다.
CODE_FORMAT = re.compile(r'[A-Z]{2,8}[.\-_]?[A-Z0-9]{1,10}')


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
    ap.add_argument('--pre-fire-ms', type=int, default=0,
                    help=f'--at(개방 시각)보다 이만큼 먼저 조회를 보낸다. 기본 0. 상한 {PRE_FIRE_MAX_MS}. '
                         'NTP 불확실성을 모르거나 100ms 초과면 0으로 강제. 리허설 측정값만 쓴다')
    ap.add_argument('--open-retry-max', type=int, default=25,
                    help='첫 조회 뒤 추가 조회 상한(회). 조회만 반복하고 주문은 1회뿐. 기본 25')
    ap.add_argument('--open-retry-gap-ms', type=int, default=150,
                    help='응답을 받은 뒤 다음 조회까지 간격(ms). 동시 요청 없음. 기본 150')
    ap.add_argument('--open-retry-until-ms', type=int, default=8000,
                    help='개방 시각 + 이 값(ms)이 지나면 새 조회를 보내지 않는다. 기본 8000')
    ap.add_argument('--unmeasured-clock-margin-ms', type=int, default=3000,
                    help='NTP 불확실성을 모를 때 재시도 신뢰 경계에 쓰는 여유(ms). 기본 3000')
    ap.add_argument('--observe-date', default='',
                    help='리허설용: 발사 전에 아직 안 열린 이 날짜(같은 노선·등급)로 조회만 보내 '
                         '응답 형태를 증거에 남긴다. 운임·주문으로 넘어가지 않는다')
    ap.add_argument('--observe-count', type=int, default=1,
                    help=f'--observe-date 조회 횟수. 1초 간격, 상한 {OBSERVE_MAX}')
    ap.add_argument('--capture-iso', default='',
                    help='--capture-date 와 같은 날짜의 YYYY-MM-DD. 무장 점검 조회와 상태 인계 검색조건 대조에 쓴다')
    ap.add_argument('--state-dir', default='',
                    help='리허설 전용 상태 폴더. 전송권·주문 의도·증거를 기본 폴더와 분리한다')
    ap.add_argument('--not-open-shape', default='',
                    help='리허설에서 관측한 미개방 응답 형태 JSON 파일. 신뢰 경계 뒤에도 이 형태와 '
                         '같은 응답(매진 제외)은 미개방으로 보고 상한 안에서 재조회한다')
    ap.add_argument('--health-at', default='',
                    help='HH:MM:SS. 대기 중 이 시각에 세션 점검(토큰 만료·열린 날짜 조회·KRW·저장 상태). '
                         f'실패하면 발사하지 않고 종료 코드 {EXIT_REPREPARE}(재준비 가능)로 끝낸다')
    ap.add_argument('--release-no-reference', action='store_true',
                    help='사용자 확인 뒤: --day 의 unknown 의도와 전송권을 보관 폴더로 옮긴다(삭제 아님). '
                         '그 runId 의 주문 증거에 예약번호·주문번호가 모두 없을 때만 허용. 사이트 접속 없음')
    ap.add_argument('--release-reason', default='', help='--release-no-reference 기록 사유(필수)')
    ap.add_argument('--status', action='store_true',
                    help='--day 의 주문 의도·전송권 상태만 읽어 보여 준다. 브라우저·사이트 접속 없음')
    ap.add_argument('--dry', action='store_true',
                    help='API 주문 전송 직전까지만. 조회·운임 요청은 실제로 보낸다. 캡처 준비의 '
                         '주문 요청은 막지만 실사이트에서 막힘을 확인하지 않았다(무주문 보장 아님)')
    ap.add_argument('--inspect-failure', action='store_true',
                    help='주문 응답 검증 실패 후 실행 메모리에 응답을 유지한다. '
                         '터미널 summary/exit만 허용하며 EOF 시 종료. 재전송·결제·잠금 해제 없음')
    ap.add_argument('--state-bridge', action='store_true',
                    help='동일 날짜 일반석 연구용: 검증 응답을 앱 상태로 인계 후 기존 동의/Npay 진행. '
                         '--continue-payment와 열린 입력의 --inspect-failure 필요. 실사이트 미검증')
    ap.add_argument('--retry-of',default='',help='사용자가 새 일반석 시험을 명시 요청한 경우 이전 실패 runId. '
                    '동일 날짜/목표·unknown 1건만 허용, 이력 보존, 자동 재시도 없음')
    ap.add_argument('--resume-preparation',action='store_true',
                    help='주문 전 중단된 명시 재시험 준비를 사용자 지시로 1회 재개. retry-of 필수')
    ap.add_argument('--retry-after-handoff-failure',action='store_true',
                    help='사용자가 새 시험을 승인한 ordered/bridge-exception 1건의 별도 재시험. retry-of 필수')
    ap.add_argument('--retry-after-checkout',action='store_true',
                    help='사용자가 별도 일반석 연속 리허설을 승인한 경우. 이전 Npay 기록/전송권 보존, retry-of 필수')
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

    global STATE
    if a.state_dir:
        # 리허설 전용 상태 폴더. 기본 폴더(실전 전송권·주문 의도)와 섞지 않는다.
        custom = Path(a.state_dir).resolve()
        if custom == (ROOT / 'dev-shots' / 'state').resolve():
            log('--state-dir 는 기본 상태 폴더가 아닌 리허설 폴더여야 한다')
            return 2
        STATE = custom

    if a.status:
        return show_status(a.day)
    if a.release_no_reference:
        return release_no_reference(a.day, a.release_reason)

    if a.capture_iso:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', a.capture_iso) or \
                a.capture_date != f'{a.capture_iso[5:7]}월 {a.capture_iso[8:10]}일':
            log('--capture-iso 는 --capture-date 라벨과 같은 날짜(YYYY-MM-DD)여야 한다')
            return 2

    if a.state_bridge:
        # 2026-09-19 사용자 결정: 프레스티지·다른 캡처 날짜·예약 발사(--at)까지 넓힌다.
        # 검색조건 날짜는 목표 또는 이번 캡처 날짜(--capture-iso)만 허용한다.
        expected_label=f'{a.date[5:7]}월 {a.date[8:10]}일'
        if (not a.continue_payment or not a.inspect_failure or a.gate_only or a.payment_only
                or (a.capture_date != expected_label and not a.capture_iso)):
            log('상태 인계에는 continue-payment·inspect-failure 가 필요하고, 캡처 날짜가 목표와 '
                '다르면 --capture-iso 가 필요하다')
            return 2

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
    if (a.resume_preparation or a.retry_after_handoff_failure or a.retry_after_checkout) and not a.retry_of:
        log('준비 재개에는 retry-of가 필요하다')
        return 2
    a.retry_claim=None
    if a.retry_of:
        if not a.state_bridge or a.dry:
            log('명시 재시험은 state-bridge 일반석 리허설에서만 허용한다')
            return 2
        try:
            a.retry_claim=explicit_retry.claim(STATE,previous_id=a.retry_of,day=a.day,
                target={'date':a.date,'origin':a.origin,'destination':a.destination,
                        'flight':a.flight,'family':a.family},pending=pending,
                at=datetime.now(KST).isoformat(),resume_preparation=a.resume_preparation,
                after_handoff_failure=a.retry_after_handoff_failure,after_checkout=a.retry_after_checkout)
        except (ValueError,OSError):
            log('명시 재시험 기록이 불일치하거나 이미 소비됐다 - 실행하지 않음')
            return 2
        log('사용자 요청 새 시험 1회: 이전 unknown 기록 보존, 서버 해제 확인 아님')
    if pending and a.retry_claim is None:
        log(f'이전 응답 검증 미해결 기록이 있다: {pending}. '
            '예약 목록으로 좌석 확보·해제를 판단할 수 없다. 기록을 보존하고 이전 시도 처리 절차를 확인한다.')
        return 2
    held = permit.existing_permit(STATE)
    if held and a.retry_claim is None:
        log(f'주문 전송권이 이미 쓰였다: {held}. 새 주문을 보내지 않는다. '
            '예약 목록에 없다는 이유로 전송권을 삭제하지 않는다.')
        return 2
    # 실행일과 발사 시각은 시작할 때 한 번 고정한다(D4).
    try:
        fire_at = permit.parse_at(a.day, a.at, KST) if a.at else None
    except ValueError as exc:
        log(f'실행일·발사 시각 형식 오류: {exc}')
        return 2
    if (not 0 <= a.pre_fire_ms <= PRE_FIRE_MAX_MS or a.open_retry_max < 0
            or a.open_retry_gap_ms < 0 or a.open_retry_until_ms < 0
            or a.unmeasured_clock_margin_ms < 0 or not 1 <= a.observe_count <= OBSERVE_MAX):
        log(f'발사 파라미터 범위 오류: 선발사 0~{PRE_FIRE_MAX_MS}ms, 재시도 값은 0 이상, '
            f'관측 1~{OBSERVE_MAX}회')
        return 2
    if a.pre_fire_ms and fire_at is None:
        log('--pre-fire-ms 는 --at 과 함께만 쓴다')
        return 2
    if a.observe_date and (a.observe_date == a.date
                           or (fire_at is not None and not a.dry and not a.state_dir)):
        # 관측 조회는 리허설(즉시 발사·--dry·리허설 상태 폴더) 전용이다. 09시 실전에 섞지 않는다.
        log('--observe-date 는 목표와 다른 날짜로, 리허설(--state-dir)·즉시 발사·--dry 에서만 쓴다')
        return 2
    a.not_open_signature = None
    if a.not_open_shape:
        try:
            raw = json.loads(Path(a.not_open_shape).read_text(encoding='utf-8'))
            sig = {k: raw[k] for k in SHAPE_FIELDS if k in raw}
        except (OSError, ValueError, TypeError):
            sig = None
        if not sig or 'status' not in sig:
            log('--not-open-shape 파일을 읽지 못했거나 대조할 필드가 없다')
            return 2
        a.not_open_signature = sig
        log(f'미개방 응답 형태(신뢰 경계 뒤에도 재조회): {json.dumps(sig, ensure_ascii=False)}')
    try:
        health_at = permit.parse_at(a.day, a.health_at, KST) if a.health_at else None
    except ValueError:
        log('--health-at 형식 오류')
        return 2
    if health_at is not None and (fire_at is None or health_at >= fire_at):
        log('--health-at 은 --at 보다 이른 시각이어야 한다')
        return 2
    a.health_at_dt = health_at
    clock = FireClock(a.unmeasured_clock_margin_ms / 1000.0)
    clock.measure('start')
    log(f'시계 보정(시작): {clock.describe()}')
    why = permit.start_check(day=a.day, now=clock.now(), fire_at=fire_at)
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
        return run(a, ledger, target, balance, fire_at, clock)
    finally:
        log(f'구간별 호출 수: {ledger.summary()}')


def release_no_reference(day, reason):
    """예약번호 없는 unknown 을 사용자 확인으로 보관한다. 파일은 지우지 않고 옮긴다.

    조건: 그날 의도가 unknown·runId 있음, 전송권의 runId 가 같음, 그 runId 의 received.json 에
    pnr·orderId 가 모두 없음. 하나라도 어긋나면 아무것도 옮기지 않는다. 서버 해제 확인이 아니다.
    """
    if not reason.strip():
        log('--release-reason 이 필요하다')
        return 2
    path = intent_path(day)
    try:
        intent = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        log('그날 주문 의도 기록을 읽을 수 없다 - 옮기지 않음')
        return 2
    run_id = intent.get('runId') if type(intent) is dict else None
    held = permit.existing_permit(STATE)
    if intent.get('state') != 'unknown' or not run_id or not held or held.get('runId') != run_id:
        log(f'unknown·runId·전송권이 서로 맞지 않는다(의도 {intent.get("state")}, 전송권 {held}) - 옮기지 않음')
        return 2
    try:
        received = order_evidence.load_received(STATE, run_id)
    except Exception:
        received = None
    if type(received) is not dict or received.get('pnr') or received.get('orderId'):
        log('주문 증거가 없거나 예약번호·주문번호가 있다 - 사용자 예약 확인 절차가 필요하다. 옮기지 않음')
        return 2
    stamp = datetime.now(KST).strftime('%Y%m%dT%H%M%S')
    dest = STATE / 'released' / f'{day}-{run_id}-{stamp}'
    dest.mkdir(parents=True, exist_ok=False)
    permit.durable_json(dest / 'release.json', {
        'day': day, 'runId': run_id, 'releasedAt': datetime.now(KST).isoformat(),
        'reason': reason.strip()[:300], 'evidence': {'pnr': None, 'orderId': None,
            'judgment': (received.get('diagnostic') or {}).get('state')},
        'meaning': '예약번호 없는 unknown 의 사용자 확인 보관. 서버 해제 확인 아님'})
    path.replace(dest / path.name)
    permit.permit_path(STATE).replace(dest / permit.PERMIT_NAME)
    log(f'보관 완료: {dest}. 새 실행이 가능해졌다')
    return 0


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
        log('  현재 실행기는 새 실행을 거부한다. unknown은 로컬 판정 실패이며 좌석 보유 증거가 아니다. '
            '예약 목록으로 해제를 판정하거나 기록을 임의 삭제하지 않는다. 별도 재시도 처리 절차가 필요하다.')
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
    observe: 리허설 관측 조회(--observe-date). 운임·주문으로 이어지지 않는다
    """
    PHASES = ('prep', 'fire', 'handoff', 'observe', 'health')

    def __init__(self):
        self.counts = {phase: {} for phase in self.PHASES}

    def add(self, phase, name, n=1):
        if phase not in self.counts or not isinstance(name, str) or type(n) is not int:
            raise ValueError('invalid-ledger-entry')
        self.counts[phase][name] = self.counts[phase].get(name, 0) + n

    def summary(self):
        return {phase: dict(sorted(items.items())) for phase, items in self.counts.items()}


def _ms(seconds):
    return None if seconds is None else round(seconds * 1000, 1)


def _iso(moment):
    return moment.isoformat(timespec='milliseconds')


class FireClock:
    """NTP 보정 시각. offset 은 기준시각-로컬(초)이며 OS 시계는 바꾸지 않는다.

    measure 는 매번 runtime 의 30분 캐시(KE_CLOCK)를 비우고 새로 잰다. 측정이 실패하면
    오프셋은 직전 성공값(없으면 0=로컬 시계)을 쓰되 불확실성을 모르는 것으로 두어
    선발사를 금지한다.
    """

    def __init__(self, unmeasured_margin):
        self.offset = 0.0
        self.uncertainty = None
        self.source = 'unmeasured-local-clock'
        self.unmeasured_margin = unmeasured_margin
        self.measurements = []

    def measure(self, label):
        os.environ.pop('KE_CLOCK', None)
        try:
            state = measure_clock()
        except Exception:
            state = {'ok': False}
        unc = state.get('uncertainty')
        off = state.get('offset')
        ok = (state.get('ok') is True and type(unc) in (int, float) and type(off) in (int, float)
              and math.isfinite(unc) and math.isfinite(off) and unc >= 0)
        if ok:
            self.offset, self.uncertainty, self.source = off, unc, str(state.get('source'))
        else:
            self.uncertainty = None
        self.measurements.append({'label': label, 'ok': ok, 'at': _iso(datetime.now(KST)),
                                  'offsetMs': _ms(off) if ok else None,
                                  'uncertaintyMs': _ms(unc) if ok else None,
                                  'source': str(state.get('source')) if ok else None})
        return ok

    def now(self):
        return datetime.now(KST) + timedelta(seconds=self.offset)

    def pre_fire_allowed(self):
        return self.uncertainty is not None and self.uncertainty <= CLOCK_MAX_UNCERTAINTY

    def margin(self):
        """재시도 신뢰 경계 여유(초). 불확실성을 모르면 보수적 여유를 쓴다."""
        return self.uncertainty if self.uncertainty is not None else self.unmeasured_margin

    def describe(self):
        if self.uncertainty is None:
            return (f'불확실성 모름(측정 실패) · 오프셋 {self.offset * 1000:+.1f}ms · '
                    f'선발사 금지 · 신뢰 경계 여유 {self.unmeasured_margin * 1000:.0f}ms')
        return (f'오프셋 {self.offset * 1000:+.1f}ms · 불확실성 ±{self.uncertainty * 1000:.1f}ms · '
                f'기준 {self.source} · 선발사 {"허용" if self.pre_fire_allowed() else "금지(불확실성 초과)"}')


class OpenRetry:
    """개방 직후 조회 재시도 판정. 응답 코드가 아니라 **보낸 시각**으로 판단한다.

    - 개방 시각 + 시계 불확실성(신뢰 경계) 전에 보낸 조회의 selected 아닌 응답은 전부 재시도.
      정각 전 부정 응답(no-target·business-error·sold-out 등)은 판정 근거가 아니다.
    - 경계 뒤에 보낸 조회는 기존 판정을 신뢰한다. 단 not-open 은 판정 자체가 '아직 안 열림'
      이므로 상한 안에서 재조회한다(서버 개방이 기준시각보다 늦는 경우 대비).
    - 횟수·간격·마감 상한이 있고, 시도는 모두 기록한다. 재시도는 조회만이다.
    """

    def __init__(self, *, open_at, clock, max_retries, gap, until):
        self.open_at = open_at
        self.clock = clock
        self.max_retries = max_retries
        self.gap = gap
        self.until = until
        self.attempts = []

    def boundary(self):
        return None if self.open_at is None else self.open_at + timedelta(seconds=self.clock.margin())

    def deadline(self):
        return None if self.open_at is None else self.open_at + timedelta(seconds=self.until)

    # 일시 처리 실패("정상적으로 처리되지 않았습니다. 잠시 후 다시 시도해 주세요.", 9/20 계측 관측).
    # 좌석 판정이 아니므로 경계 뒤에도 상한 안에서 재조회한다(2026-09-20 사용자 결정).
    TRANSIENT_CODES = frozenset(('ERT.3002',))

    def decide(self, state, sent, not_open_like=False, code=None):
        """(결정, 이유). 결정은 selected / retry / stop.

        not_open_like: 리허설에서 관측한 미개방 응답 형태와 같다(매진 판정은 제외하고 넘긴다).
        """
        if state == 'selected':
            return 'selected', 'selected'
        if self.open_at is None:
            return 'stop', 'no-open-time'
        if len(self.attempts) >= self.max_retries:   # 이번 시도 기록 전: 지금까지 추가 조회 수
            return 'stop', 'retry-cap'
        if self.clock.now() + timedelta(seconds=self.gap) > self.deadline():
            return 'stop', 'retry-deadline'
        if sent < self.boundary():
            return 'retry', 'before-trust-boundary'
        if state == 'not-open':
            return 'retry', 'not-open'
        if not_open_like:
            return 'retry', 'not-open-shape'
        if code in self.TRANSIENT_CODES:
            return 'retry', 'transient-error'
        return 'stop', 'trusted-verdict'

    def record(self, **entry):
        self.attempts.append(entry)


def award_code(result):
    """조회 응답의 업무 코드(code/errorCode). 없거나 읽지 못하면 None."""
    try:
        data = json.loads((result or {}).get('body') or 'null')
    except (ValueError, TypeError, AttributeError):
        return None
    if not isinstance(data, dict):
        return None
    value = data.get('code') or data.get('errorCode')
    return value if isinstance(value, str) else None


def response_shape(result, target):
    """조회 응답의 형태만 허용 목록으로 뽑는다. 식별자·토큰·본문 원문은 넣지 않는다."""
    shape = {}
    if type(result) is not dict:
        return {'resultType': type(result).__name__}
    shape['status'] = result.get('status') if type(result.get('status')) is int else None
    if result.get('error'):
        shape['transportError'] = str(result.get('error'))[:40]
    body = result.get('body')
    try:
        payload = json.loads(body) if isinstance(body, str) else body
    except (ValueError, TypeError):
        return {**shape, 'body': 'not-json', 'bodyLength': len(body)}
    if type(payload) is not dict:
        return {**shape, 'bodyType': type(payload).__name__}
    shape['keys'] = sorted(k for k in payload if isinstance(k, str))[:40]
    for k in ('code', 'errorCode', 'resultCode', 'responseCode'):
        if k in payload:
            v = payload[k]
            shape[k] = v if isinstance(v, str) and CODE_FORMAT.fullmatch(v) else f'<{type(v).__name__}>'
    for k in ('error', 'errors', 'errorList', 'errorMessage', 'responseMessage', 'message'):
        if payload.get(k) not in (None, '', [], {}):
            shape[k + 'Present'] = True
    if isinstance(payload.get('currency'), str) and re.fullmatch(r'[A-Z]{3}', payload['currency']):
        shape['currency'] = payload['currency']
    if type(payload.get('emptyFare')) is bool:
        shape['emptyFare'] = payload['emptyFare']
    bounds = payload.get('upsellBoundAvailList')
    if type(bounds) is list:
        shape['bounds'] = len(bounds)
        flights = bounds[0].get('availFlightList') if bounds and type(bounds[0]) is dict else None
        if type(flights) is list:
            shape['flights'] = len(flights)
            numbers = []
            for flight in flights:
                info = flight.get('flightInfoList') if type(flight) is dict else None
                leg = info[0] if type(info) is list and info and type(info[0]) is dict else {}
                if isinstance(leg.get('flightNumber'), str) and re.fullmatch(r'\d{1,4}', leg['flightNumber']):
                    numbers.append(leg['flightNumber'])
            shape['flightNumbers'] = numbers[:20]
            for flight in flights:
                if type(flight) is not dict:
                    continue
                info = flight.get('flightInfoList')
                leg = info[0] if type(info) is list and info and type(info[0]) is dict else {}
                if leg.get('flightNumber') != target.flight:
                    continue
                fares = flight.get('commercialFareFamilyList')
                fam = [f for f in fares if type(f) is dict and f.get('fareFamily') == target.family] \
                    if type(fares) is list else []
                shape['targetFlight'] = {
                    'soldOut': flight.get('soldOut') if type(flight.get('soldOut')) is bool else None,
                    'family': ({'soldout': fam[0].get('soldout') if type(fam[0].get('soldout')) is bool
                                else None,
                                'seatCount': fam[0].get('seatCount')
                                if isinstance(fam[0].get('seatCount'), str)
                                and re.fullmatch(r'\d{1,3}', fam[0]['seatCount']) else None}
                               if fam else None)}
                break
    return shape


def shape_matches(shape, signature):
    """관측된 미개방 형태의 모든 필드가 같을 때만 True. 서명이 없으면 False."""
    if not signature or type(shape) is not dict:
        return False
    return all(shape.get(k) == v for k, v in signature.items())


def save_timing(a, timing_id, clock, retry, pre_fire, observe=None):
    """발사 시각·조회 시도 증거. Git 제외 경로에만 쓴다. 실패해도 발사 흐름을 바꾸지 않는다."""
    try:
        permit.durable_json(STATE / f'fire-timing-{a.day}-{timing_id}.json', {
            'timingId': timing_id, 'day': a.day, 'at': _iso(datetime.now(KST)),
            'target': {'date': a.date, 'origin': a.origin, 'destination': a.destination,
                       'flight': a.flight, 'family': a.family},
            'clock': {'offsetMs': _ms(clock.offset), 'uncertaintyMs': _ms(clock.uncertainty),
                      'source': clock.source, 'measurements': clock.measurements},
            'preFireMs': pre_fire,
            'openAt': _iso(retry.open_at) if retry and retry.open_at else None,
            'trustBoundary': _iso(retry.boundary()) if retry and retry.open_at else None,
            'retryDeadline': _iso(retry.deadline()) if retry and retry.open_at else None,
            'retryLimits': {'maxRetries': a.open_retry_max, 'gapMs': a.open_retry_gap_ms,
                            'untilMs': a.open_retry_until_ms},
            'attempts': retry.attempts if retry else [],
            'observe': observe or []})
    except Exception:
        log('발사 시각 증거 저장 실패 - 발사 흐름은 그대로')


def run(a, ledger, target, balance, fire_at, clock):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}', timeout=15000)
        ctx = browser.contexts[0]
        if a.state_bridge:
            candidates=[p for p in ctx.pages if p.url==site_drive.CALENDAR]
            if len(candidates)!=1:
                log('상태 인계 대상 달력 탭이 정확히 하나여야 한다 - 준비/주문하지 않음')
                return 2
            page=candidates[0]
        else:
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
        steps = site_drive.capture_pass(page, a.capture_date, cabin=a.capture_cabin, log=log,
                                        flight=a.flight, ensure_currency=ensure_krw)
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

        timing_id = uuid4().hex[:12]
        observed = []
        if a.observe_date:
            observed = observe_unopened(page, snap, a, ledger, clock)
            save_timing(a, timing_id, clock, None, 0, observe=observed)

        # 무장 점검: 09시에야 발견할 문제(세션·통화·편명·저장 상태)를 캡처 직후에 확인한다.
        failed = readiness_check(page, snap, a, ledger, target, fire_at, 'arm')
        if failed:
            return failed

        def still_ready(full=True):
            """발사 준비가 아직 유효한지. full 이면 달력 셀까지 읽는다(페이지 왕복 1회)."""
            gone = missing_captures(snap, a.capture_max_age, time.time())
            if gone:
                return f'캡처 만료: {[g.split("/")[-1] for g in gone]}'
            if not same_origin(page, snap):
                return f'문서 오리진 변경: {page.url[:60]}'
            if 'calendar-fare-bonus' not in (page.url or ''):
                return f'달력 조회 화면 이탈: {page.url[-40:]}'
            if a.state_bridge and page.url != site_drive.CALENDAR:
                # 인계 결속(bind)은 정확히 같은 주소만 받는다. 대기 중에 먼저 잡는다.
                return f'달력 주소 불일치: {page.url[-40:]}'
            if full and not site_drive.on_calendar(page):
                return '달력 셀이 보이지 않음'
            return None

        if a.arm_file:
            armed = Path(a.arm_file)
            log(f'**사용자 무장 대기** — 발사를 시작하려면 {armed} 를 만든다')
            log('AGENTS: 실전 대기 시작은 사용자가 누른다. 프로그램이 대신 무장하지 않는다')
            last_check = time.monotonic()
            while not armed.exists():
                if fire_at is not None and clock.now() >= fire_at:
                    log('발사 시각까지 무장되지 않았다 - 발사하지 않는다')
                    return 2
                if time.monotonic() - last_check >= 5:
                    last_check = time.monotonic()
                    why = still_ready()
                    if why:
                        log(f'무장 전에 준비가 무효가 됐다({why}) - 중단. 다시 준비한다')
                        return EXIT_REPREPARE   # 주문 전: 체인이 재준비할 수 있다
                time.sleep(0.5)
            # 발사 시각이 지난 뒤 확인된 무장은 늦은 실제 주문이 되므로 거부한다(9/13 검토 P2).
            if fire_at is not None and clock.now() >= fire_at:
                log('발사 시각이 지난 뒤 무장이 확인됐다 - 발사하지 않는다')
                return 2
            log('사용자 무장 확인. 발사 대기로 넘어간다')

        # 선발사: 보정 시각 기준 개방 시각보다 pre_fire 만큼 먼저 첫 조회를 보낸다.
        # 정각 전 부정 응답은 OpenRetry 가 시각으로 걸러 재조회한다. 불확실성을 모르면 0.
        pre_fire = a.pre_fire_ms if clock.pre_fire_allowed() else 0
        pl = pipeline.Pipeline(target, member=member, balance=balance)
        binding = None
        health_done = getattr(a, 'health_at_dt', None) is None
        if fire_at is not None:
            def launch_at():
                return fire_at - timedelta(milliseconds=pre_fire)
            wait = (launch_at() - clock.now()).total_seconds()
            log(f'{a.day} {a.at} (선발사 {pre_fire}ms, 보정 시각) 까지 {wait:.0f}초 대기. '
                '이 프로세스를 내리면 메모리 캡처가 사라진다')
            final_checked = False
            while True:
                left = (launch_at() - clock.now()).total_seconds()
                if left <= 0:
                    break
                if not health_done and clock.now() >= a.health_at_dt:
                    health_done = True
                    failed = readiness_check(page, snap, a, ledger, target, fire_at, 'health')
                    if failed:
                        return failed
                    continue
                if left > 60:
                    nap = min(60, left - 30)
                    if not health_done:
                        nap = min(nap, max(0.5, (a.health_at_dt - clock.now()).total_seconds()))
                    time.sleep(nap)
                    left = (launch_at() - clock.now()).total_seconds()
                    why = still_ready()
                    log(f'대기 {left:.0f}초 남음 · 준비 유지={why is None} · {page.url[-32:]}')
                    if why:
                        log(f'대기 중 준비가 무효가 됐다({why}) - 중단')
                        return EXIT_REPREPARE   # 주문 전: 체인이 재준비할 수 있다
                elif not final_checked:
                    # 마지막 60초 안에 한 번 달력 셀까지 확인하고 시계를 캐시 없이 다시 잰다.
                    # 재측정이 실패하면 불확실성을 모르므로 선발사를 0으로 내린다.
                    final_checked = True
                    why = still_ready()
                    if why:
                        log(f'정각 전 마지막 확인에서 준비 무효({why}) - 중단')
                        return EXIT_REPREPARE   # 주문 전: 체인이 재준비할 수 있다
                    clock.measure('final')
                    if pre_fire and not clock.pre_fire_allowed():
                        log(f'마지막 시계 측정에서 불확실성 조건 미달 - 선발사 {pre_fire}ms → 0')
                        pre_fire = 0
                    elif a.pre_fire_ms and not pre_fire and clock.pre_fire_allowed():
                        # 9/20 콜드: 시작 측정만 실패하고 최종 측정은 ±31ms 였는데 선발사가 0 으로 남았다.
                        # 선발사 조건은 발사 직전(최종) 측정으로 판단한다.
                        pre_fire = a.pre_fire_ms
                        log(f'최종 시계 측정이 조건을 만족 - 선발사 {pre_fire}ms 복원')
                    log(f'시계 보정(최종): {clock.describe()} · 선발사 {pre_fire}ms')
                elif a.state_bridge and binding is None:
                    # 인계 결속(쿠키·저장값 읽기)을 발사 경로에서 빼 T-15초에 미리 잡는다.
                    if left > 15:
                        time.sleep(left - 15)
                        continue
                    binding = make_binding(page, pl, a)
                    if binding is None:
                        return EXIT_REPREPARE   # 주문 전: 체인이 재준비할 수 있다
                else:
                    time.sleep(max(0.0, left) + 0.001)

        # 대기에서 늦게 깼거나(절전 등) 날짜가 바뀌었으면 발사하지 않는다.
        why = permit.fire_check(day=a.day, now=clock.now(),
                                fire_at=(fire_at - timedelta(milliseconds=pre_fire))
                                if fire_at is not None else None,
                                late_limit=a.late_limit)
        if why:
            log(f'발사 거부: {why}')
            return 2
        why = still_ready(full=fire_at is None)
        if why:
            log(f'발사 직전 준비 무효({why}) - 주문하지 않는다')
            return EXIT_REPREPARE   # 주문 전: 체인이 재준비할 수 있다
        retry = OpenRetry(open_at=fire_at, clock=clock, max_retries=a.open_retry_max,
                          gap=a.open_retry_gap_ms / 1000.0, until=a.open_retry_until_ms / 1000.0)
        def checkpoint():
            save_timing(a, timing_id, clock, retry, pre_fire, observe=observed)
        # 증거 파일은 주문 응답 뒤(결제 인계 전)와 종료 때 쓴다. 조회~주문 사이에는 쓰지 않는다.
        try:
            return fire(page, snap, a, ledger, pl, retry, checkpoint=checkpoint, binding=binding)
        finally:
            checkpoint()


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


def observe_unopened(page, snap, a, ledger, clock):
    """리허설 부수 관측: 아직 안 열린 날짜로 조회만 보내 응답 형태를 남긴다. 운임·주문 없음."""
    try:
        target = Target(a.observe_date, a.origin, a.destination, a.family, a.carrier, a.flight)
    except ValueError as exc:
        log(f'관측 날짜가 올바르지 않다: {exc} - 관측 생략')
        return [{'error': 'invalid-observe-target'}]
    out = []
    for i in range(a.observe_count):
        if i:
            time.sleep(1.0)
        pl = pipeline.Pipeline(target, member=None, balance=None)
        body, why = pl.award_body(snap[AVAIL].body, snap[AVAIL].headers)
        if why:
            out.append({'n': i + 1, 'error': why})
            break
        ledger.add('observe', 'awardAvailability-attempt')
        sent = clock.now()
        r = transport.send_request(page, snap[AVAIL], body=body)
        received = clock.now()
        state = pl.judge_award(r)
        entry = {'n': i + 1, 'date': a.observe_date, 'sentAt': _iso(sent),
                 'receivedAt': _iso(received),
                 'elapsedMs': r.get('elapsedMs') if type(r) is dict else None,
                 'verdict': state, 'shape': response_shape(r, target)}
        out.append(entry)
        log(f'미개방 날짜 관측 {i + 1}/{a.observe_count}: {a.observe_date} 판정={state} '
            f'형태={json.dumps(entry["shape"], ensure_ascii=False)}')
    return out


def ensure_krw(page, timeout_ms=20000):
    """운임 화면 통화를 KRW 로. 'krw'(확인)·'bounced'(적용 뒤 달력으로 돌아감)·'failed'.

    조작은 dev/prepare_currency.py 와 같은 실측 요소(#currencyBtn, #filter-currency 의 KRW
    라벨, .filter__apply)만 쓴다. 달력으로 돌아간 경우의 재검색은 capture_pass 가 한다.
    """
    def krw():
        try:
            return 'KRW' in page.locator('#currencyBtn').inner_text(timeout=5000)
        except Exception:
            return False
    try:
        if krw():
            return 'krw'
        page.locator('#currencyBtn').click(timeout=timeout_ms)
        labels = page.locator('#filter-currency label').filter(has_text='KRW')
        if labels.count() != 1:
            log('  통화 목록에서 KRW 를 하나로 찾지 못했다')
            return 'failed'
        labels.click(timeout=timeout_ms)
        page.locator('#filter-currency .filter__apply').click(timeout=timeout_ms)
        try:
            page.wait_for_function(
                "() => location.pathname.includes('/booking/calendar-fare-bonus') || "
                "(document.querySelector('#currencyBtn')?.innerText || '').includes('KRW')",
                timeout=timeout_ms)
        except Exception:
            # 적용 뒤 문서가 바뀌면 대기 함수의 실행 문맥이 사라질 수 있다. 주소로 판정한다.
            page.wait_for_timeout(3000)
        if 'calendar-fare-bonus' in page.url:
            page.wait_for_timeout(3000)
            return 'bounced'
        return 'krw' if krw() else 'failed'
    except Exception as exc:
        log(f'  통화 KRW 준비 실패: {type(exc).__name__}: {str(exc)[:60]}')
        return 'failed'


def alert():
    """사용자 차례를 소리로 알린다. 실패해도 흐름에 영향 없음."""
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1200, 300)
    except Exception:
        print('\a', end='', flush=True)


def make_binding(page, pl, a):
    """상태 인계 결속. 실패하면 None(주문 전이므로 발사하지 않는다)."""
    try:
        binding = connected_bridge.bind(page, session=pl.session, subject=pl.subject,
                                        search_date=a.capture_iso or None)
        connected_bridge.state_bridge.validate_prepared_storage(binding.storage, pl.target,
                                                                a.capture_iso or None)
        return binding
    except Exception as exc:
        log(f'상태 인계 준비 문맥 확인 실패({type(exc).__name__}: {str(exc)[:40]}) - API 발사하지 않음')
        return None


def readiness_check(page, snap, a, ledger, target, fire_at, label):
    """무장·세션 점검. 문제가 없으면 None, 있으면 종료 코드.

    EXIT_REPREPARE(3): 세션·통화·저장 상태 문제. 주문 전이므로 체인이 다시 준비할 수 있다.
    2: 설정 문제(편명·노선 등). 다시 준비해도 같으므로 체인이 멈춘다.
    조회는 이미 열린 캡처 날짜로만 보낸다(fetch 만 쓰므로 앱 저장값을 바꾸지 않는다).
    """
    report = {}
    try:
        from session_health import session_health
        opening = (fire_at or datetime.now(KST)).timestamp()
        health = session_health(page.context, opening)
        report['token'] = {k: health.get(k) for k in ('known', 'secondsAfterOpen', 'reason')}
        if health.get('known') and (health.get('secondsAfterOpen') or 0) < TOKEN_MARGIN:
            log(f'[{label}] 로그인 토큰이 발사+{TOKEN_MARGIN}초 전에 만료된다 - 재준비 필요')
            return EXIT_REPREPARE
    except Exception as exc:
        report['token'] = {'known': False, 'reason': type(exc).__name__}
    if a.state_bridge and page.url != site_drive.CALENDAR:
        log(f'[{label}] 달력 주소 불일치 - 재준비 필요')
        return EXIT_REPREPARE
    if a.state_bridge:
        try:
            storage = page.evaluate(
                'keys => Object.fromEntries(keys.map(k => [k, sessionStorage.getItem(k)]))',
                list(connected_bridge.state_bridge.KEYS))
            connected_bridge.state_bridge.validate_prepared_storage(storage, target,
                                                                    a.capture_iso or None)
            report['storage'] = 'ready'
        except Exception as exc:
            log(f'[{label}] 사이트 저장 상태가 인계 조건과 다르다({str(exc)[:40]}) - 재준비 필요')
            return EXIT_REPREPARE
    if a.capture_iso:
        family = CABIN_FAMILY.get(a.capture_cabin) or a.family
        try:
            probe_target = Target(a.capture_iso, a.origin, a.destination, family, a.carrier, a.flight)
        except ValueError:
            log(f'[{label}] 점검 목표 구성 실패 - 설정 확인 필요')
            return 2
        probe = pipeline.Pipeline(probe_target, member=None, balance=None)
        body, why = probe.award_body(snap[AVAIL].body, snap[AVAIL].headers)
        if why:
            log(f'[{label}] 점검 조회 본문 구성 실패: {why}')
            return 2
        ledger.add('health', f'{label}-awardAvailability')
        r = transport.send_request(page, snap[AVAIL], body=body)
        state = probe.judge_award(r)
        shape = response_shape(r, probe_target)
        report['probe'] = {'verdict': state, 'status': shape.get('status'),
                           'currency': shape.get('currency'),
                           'flightNumbers': shape.get('flightNumbers')}
        log(f'[{label}] 점검 조회({a.capture_iso}) 판정={state} 통화={shape.get("currency")} '
            f'편={shape.get("flightNumbers")} 토큰={report["token"]}')
        if state in ('session-expired', 'fetch-failed', 'http-error', 'invalid-json'):
            return EXIT_REPREPARE
        if shape.get('currency') != 'KRW':
            log(f'[{label}] 조회 응답 통화가 KRW 가 아니다 - 재준비(KRW 설정) 필요')
            return EXIT_REPREPARE
        if state not in ('selected', 'sold-out', 'unverified-stock'):
            log(f'[{label}] 열린 날짜 조회에서 목표 편 {a.carrier}{a.flight} 을 확인하지 못했다({state}) '
                '- 편명·노선 설정 확인 필요')
            return 2
    log(f'[{label}] 점검 통과')
    return None


def fire(page, snap, a, ledger, pl, retry, checkpoint=lambda: None, binding=None):
    """메모리 캡처로 현재 문서에서 조회→운임→필수 검증→주문을 보낸다. 주문 뒤 인계는 옵션에 따른다.

    조회는 retry(OpenRetry)가 보낸 시각으로 재시도 여부를 정한다. 운임·주문은 재시도하지 않는다.
    """
    if a.state_bridge and binding is None:
        binding = make_binding(page, pl, a)
        if binding is None:
            return 2
    t0 = time.monotonic()
    log(f'T0 발사: {a.date} {a.carrier}{a.flight} {a.origin}-{a.destination} {a.family} '
        f'· 문서 {page.url[-40:]}')

    while True:
        # 시도마다 새 조회 본문(새 세대)을 만든다. 이전 응답의 선택을 재사용하지 않는다.
        body, why = pl.award_body(snap[AVAIL].body, snap[AVAIL].headers)
        if why:
            log(f'조회 본문 구성 실패: {why} - 주문하지 않는다')
            return 2
        sent = retry.clock.now()
        r = send_counted(page, snap[AVAIL], ledger, 'awardAvailability', body=body)
        received = retry.clock.now()
        state = pl.judge_award(r)
        shape = response_shape(r, pl.target) if state != 'selected' else None
        like = (state != 'sold-out' and shape is not None
                and shape_matches(shape, getattr(a, 'not_open_signature', None)))
        decision, reason = retry.decide(state, sent, not_open_like=like, code=award_code(r))
        n = len(retry.attempts) + 1
        entry = {'n': n, 'sentAt': _iso(sent), 'receivedAt': _iso(received),
                 'elapsedMs': r.get('elapsedMs') if type(r) is dict else None,
                 'status': r.get('status') if type(r) is dict else None,
                 'verdict': state, 'decision': decision, 'reason': reason}
        if retry.open_at is not None:
            entry['sentVsOpenMs'] = _ms((sent - retry.open_at).total_seconds())
        if shape is not None:
            # 정각 전 응답이 실제로 무엇인지가 리허설·실전의 관측 대상이다. 형태만 남긴다.
            entry['shape'] = shape
        retry.record(**entry)
        log(f'조회#{n} status={entry["status"]} {entry["elapsedMs"] or 0:.0f}ms 판정={state} '
            f'→ {decision}({reason}) (+{time.monotonic()-t0:.3f}s)')
        if decision == 'selected':
            break
        if decision == 'stop':
            log(f'조회 판정 {state} ({reason}) - 주문하지 않는다')
            return 2
        ledger.add('fire', 'awardAvailability-retry')
        time.sleep(retry.gap)

    fbody, why = pl.fare_body(snap[FARE].body, snap[FARE].headers)
    if why:
        log(f'운임 본문 구성 실패: {why} - 주문하지 않는다')
        return 2
    r2 = send_counted(page, snap[FARE], ledger, 'fareInformation', body=fbody)
    state = pl.judge_fare(r2)
    log(f'운임 status={r2.get("status")} {r2.get("elapsedMs",0):.0f}ms 판정={state} '
        f'(+{time.monotonic()-t0:.3f}s)')
    if state != 'validated':
        try:
            from evidence import error_detail
            detail = error_detail(json.loads(r2.get('body') or 'null'))
        except (ValueError, TypeError):
            detail = None
        log(f'운임 판정 {state} - 주문하지 않는다'
            + (f' · 오류 {json.dumps(detail, ensure_ascii=False)}' if detail else ''))
        return 2

    state = pl.prepare_order(snap[ORDER].body)
    log(f'필수 검증·주문 준비 판정={state} (+{time.monotonic()-t0:.3f}s)')
    if a.dry:
        log(f'--dry: 주문 직전 중단. T0→판정 {time.monotonic()-t0:.3f}초')
        return 0 if state == 'ready' else 2
    if state != 'ready':
        log(f'필수 검증 {state} - 주문하지 않는다')
        return 2

    if a.state_bridge:
        started=time.monotonic()
        try:
            connected_bridge.preflight(binding,target=pl.target)
        except Exception as exc:
            diagnostic=connected_bridge.error_summary(exc)
            if diagnostic.get('reason')=='session-changed':
                diagnostic['sessionDiff']=connected_bridge.session_diff(binding)
            ledger.add('fire','bridge-preflight-refused')
            log('주문 전 인계 점검 실패 - 주문 전송 없음: '+json.dumps(diagnostic,ensure_ascii=False))
            return 2
        log(f'주문 전 인계 점검 통과 ({(time.monotonic()-started)*1000:.0f}ms)')

    # 배타 전송권: 같은 실행일에 한 프로세스만 주문 요청을 보낸다. 자동으로 풀지 않는다(D4).
    run_id = uuid4().hex[:12]
    target_record={'date':a.date,'origin':a.origin,'destination':a.destination,
                   'flight':a.flight,'family':a.family}
    if a.retry_claim is not None:
        got=explicit_retry.transfer(a.retry_claim,run_id=run_id,day=a.day,target=target_record,
                                    at=datetime.now(KST).isoformat())
    else:
        got = permit.acquire(STATE, day=a.day, run_id=run_id, now_iso=datetime.now(KST).isoformat(),
                             target=target_record)
    if got is None:
        ledger.add('fire', 'inputTravellers-permit-denied')
        log('**다른 실행이 이미 주문 전송권을 가졌다. 주문하지 않는다.**')
        return 2
    flow = OrderFlow()
    flow.prepare(Checks(target_matches=True, fare_is_current=True, session_matches=True,
                        required_checks_passed=True, no_unresolved_order=True, user_started=True))
    flow.advance(Event.RECORD_INTENT)
    record_intent(a.day, 'sending', date=a.date, flight=a.flight, family=a.family, runId=run_id,
                  awardAttempts=len(retry.attempts))
    flow.advance(Event.BEGIN_SEND)
    # 주문 본문은 캡처 원문을 그대로 보낸다. 구조 판정은 prepare_order 에서 끝났다.
    try:
        r3 = send_counted(page, snap[ORDER], ledger, 'inputTravellers')
        response_received_at=time.time()
        response_received_mono=time.monotonic()
    except BaseException:
        flow.advance(Event.INTERRUPT)
        record_intent(a.day, 'unknown', why='order-send-exception', runId=run_id)
        raise
    try:
        try:
            order_evidence.save_received(STATE,run_id=run_id,target=pl.target,response=r3,
                quote=pl.quote,passenger_fingerprint=pl.member.traveller_digest,
                received_at=response_received_at)
            log('허용 항목 주문 증거 저장 완료 (식별자 원문은 터미널에 출력하지 않음)')
        except Exception:
            log('주문 증거 저장 실패 - 응답은 메모리에 유지하며 주문을 재전송하지 않음')
        outcome = pl.judge_order(r3,allow_observed_amount_layout=a.state_bridge)
    except Exception:
        # 판정 코드 자체의 예외도 이미 전송한 주문을 재시도할 이유가 아니다.
        # 예외 문자열에는 응답 원문이 들어갈 수 있으므로 출력하지 않는다.
        flow.advance(Event.INTERRUPT)
        record_intent(a.day, 'unknown', why='order-judge-exception', runId=run_id)
        save_order_judgment(run_id,'order-judge-exception')
        log('주문 응답 판정 코드 예외 - 원문을 로그에 출력하지 않고 unknown 유지')
        if a.inspect_failure:
            recovery.inspect_failure(r3, pl.quote, emit=log)
        return 2
    save_order_judgment(run_id,outcome.state)
    checkpoint()
    elapsed = response_received_mono - t0
    log(f'주문 status={r3.get("status")} {r3.get("elapsedMs",0):.0f}ms 판정={outcome.state} '
        f'(+{elapsed:.3f}s)')
    if outcome.state != 'order-recorded':
        # 어떤 판정도 주문 미생성의 증거가 아니다. 재전송하지 않는다. 전송권도 남긴다.
        flow.advance(Event.INTERRUPT)
        diagnostic = order_amount_diagnostic(r3.get('body'), pl.quote)
        record_intent(a.day, 'unknown', why=outcome.state, runId=run_id,
                      diagnostic=diagnostic)
        log('주문 응답 진단(개인정보 제외): ' + json.dumps(diagnostic, ensure_ascii=False))
        log('주문 응답 검증 실패 - 재전송하지 않는다. 좌석 상태는 미확인이며 예약 목록으로 판정하지 않는다')
        if a.inspect_failure:
            resumed = recovery.inspect_failure(r3, pl.quote, emit=log,
                resume=(lambda: resume_retained_order(page,a,ledger,pl,binding,r2,r3,run_id))
                    if a.state_bridge else None)
            if resumed == 'resumed':
                return 0
        return 2
    order = outcome.order
    flow.advance(Event.CONFIRM_ORDER)
    record_intent(a.day, 'ordered', segmentStatus=order.segment_status,
                  amountsMatched=order.amounts_matched,
                  paymentAmountsMatched=order.payment_amounts_matched,
                  amountLayout=order.amount_layout, runId=run_id)
    log(f'주문 응답 확인  **T0 → 주문 응답 {elapsed:.3f}초** (보유 시작 시각은 미확인)')
    mileage = pl.mileage_label()
    if a.state_bridge:
        return bridged_payment(page, a, ledger, pl, binding, r2, r3, order, run_id)
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


def save_order_judgment(run_id,state):
    try:order_evidence.save_judgment(STATE,run_id,state)
    except Exception:log('주문 판정 증거 저장 실패 - 기존 응답/전송권 유지, 재전송 없음')


def resume_retained_order(page,a,ledger,pl,binding,fare_response,order_response,run_id):
    """원래 응답만 재검증한다. transport·준비·조회·새 주문으로 돌아가는 경로 없음."""
    if not a.state_bridge or binding is None or binding.used or pl.order_request is None:
        return False
    age=time.monotonic()-pl.order_request.created
    if not 0 <= age <= connected_bridge.state_bridge.RESPONSE_MAX_AGE:
        log('보존 응답의 인계 유효시간 초과. 시각을 갱신하거나 재주문하지 않음')
        return False
    outcome=pl.judge_order(order_response,allow_observed_amount_layout=True)
    if outcome.state!='order-recorded' or not outcome.order.payment_amounts_matched:
        log('보존 응답 재검증 미통과. 기존 미확인 기록 유지')
        return False
    return bridged_payment(page,a,ledger,pl,binding,fare_response,order_response,
                           outcome.order,run_id,inspect_on_failure=False)==0


def bridged_payment(page, a, ledger, pl, binding, fare_response, order_response, order, run_id,
                    *, inspect_on_failure=True):
    """주문 전송은 끝난 상태. 인계 실패도 재전송하지 않고 기존 응답 조사로 이어간다."""
    connection=None
    completed=False
    try:
        connection=connected_bridge.connect(binding,request=pl.order_request,quote=pl.quote,
            fare_response=fare_response,order_response=order_response,
            allow_observed_amount_layout=a.state_bridge,
            passenger_fingerprint=pl.member.traveller_digest)
        result=connection.result
        ready=(result.get('matched') is True and result.get('sessionUnchanged') is True
               and result.get('displayHints') and all(result['displayHints'].values()))
        stage=result.get('stage','bridge-failed')
        log('인계 확인: '+json.dumps({'matched':result.get('matched'),
            'sessionUnchanged':result.get('sessionUnchanged'),
            'cookieStampUnchanged':result.get('cookieStampUnchanged'),
            'displayHints':result.get('displayHints')},ensure_ascii=False))
        if ready:
            result=site_drive.payment_pass(page,flight=a.flight,date=a.date,mileage=pl.mileage_label(),
                reference=order.reference,ordered_at=order.received,origin=a.origin,
                destination=a.destination,amount=pl.quote.total_amount,log=log,
                navigate=False,existing_watch=connection.guard,
                mileage_verifier=connected_bridge.resume_gate.ensure_mileage)
            completed=result.get('completed') is True
            stage=result.get('stage','payment-failed')
        ledger.add('handoff',f'bridge:{stage}')
        record_intent(a.day,'ordered',runId=run_id,handoff=stage,paymentWindowReached=completed,
            diagnostic=result.get('diagnostic'),
            bridgeChecks={k:connection.result.get(k) for k in
                ('matched','sessionUnchanged','cookieStampUnchanged','displayHints','stage')},
            amountsMatched=order.amounts_matched,paymentAmountsMatched=order.payment_amounts_matched,
            amountLayout=order.amount_layout)
        log(f'상태 인계 결과: {stage}, 실제 결제창 완료={completed}')
        if result.get('diagnostic'):
            log('인계 진단(개인정보 제외): '+json.dumps(result['diagnostic'],ensure_ascii=False))
        if stage=='user-payment-method':
            # ICN 도착에서 현대카드 선택을 확인하지 못한 경우: 결제하기 전 상태로 사용자에게 넘긴다.
            alert()
            log('**[사용자 차례] 9232 게이트 화면에서 한국발행 신용/체크카드 → 현대카드 → 결제하기. '
                '카드사 창의 최종 승인은 사용자가 판단한다.**')
            return 0
        if stage=='hyundai-card-window':
            alert()
            log('**[사용자 차례] 현대카드 창이 열렸다. 앱카드/PIN 인증과 최종 결제는 사용자가 한다.**')
            return 0
        if not completed:
            alert()
            log('**인계 미완료. 게이트 탭을 닫지 말고 화면을 확인한다. 재주문하지 않는다.**')
        if not completed and inspect_on_failure:
            resumed=inspect_handoff(page,a,ledger,pl,binding,fare_response,order_response,run_id)
            if resumed:return 0
        return 0 if completed else 2
    except Exception as exc:
        diagnostic=connected_bridge.error_summary(exc)
        record_intent(a.day,'ordered',runId=run_id,handoff='bridge-exception',paymentWindowReached=False,
            diagnostic=diagnostic,
            amountsMatched=order.amounts_matched,paymentAmountsMatched=order.payment_amounts_matched,
            amountLayout=order.amount_layout)
        log('상태 인계 예외 - 재주문 없이 응답 조사. 종료하면 현재 감시 연결도 종료됨')
        log('인계 진단(개인정보 제외): '+json.dumps(diagnostic,ensure_ascii=False))
        if inspect_on_failure:
            resumed=inspect_handoff(page,a,ledger,pl,binding,fare_response,order_response,run_id)
            if resumed:return 0
        return 2
    finally:
        if connection is not None:
            connection.close()


def inspect_handoff(page,a,ledger,pl,binding,fare_response,order_response,run_id):
    # 문맥과 응답은 원래 객체다. 새 주문·재조회 함수를 콜백에 전달하지 않는다.
    can_offer=(binding is not None and getattr(binding,'used',True) is False)
    result=recovery.inspect_failure(order_response,pl.quote,emit=log,
        diagnose=lambda:connected_bridge.retained_report(binding,pl.order_request),
        resume=(lambda:resume_retained_order(page,a,ledger,pl,binding,
                                            fare_response,order_response,run_id)) if can_offer else None)
    return result=='resumed'


if __name__ == '__main__':
    raise SystemExit(main())
