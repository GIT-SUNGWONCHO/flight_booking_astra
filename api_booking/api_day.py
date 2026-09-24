"""API 예매 무인 체인: Chrome 9232 → 로그인·달력 → KRW → live_order(캡처·대기·발사·인계).

2026-09-19 사용자 승인 범위: 리허설과 실전테스트에서 프로그램이 무장·발사하고 주문 1건까지
진행한다. 결제창(카드사 창) 안의 최종 승인은 사용자만 한다. ICN 도착 노선은 결제수단 직전에
멈춘다(live_order → site_drive.payment_pass).

  리허설  python api_booking/api_day.py --mode rehearsal --target-date 2027-08-24 \\
              --capture-iso 2027-08-23 --fire-in-min 20 --own-mileage 100000 \\
              --observe-date 2027-09-14
  실전    python api_booking/api_day.py --mode live --target-date 2027-09-14 \\
              --capture-iso 2027-09-13 --at 09:00:00 --health-at 08:50:00 \\
              --capture-not-before 08:38:00 --reprep-cutoff 08:52:00 --own-mileage 100000

재준비: live_order 가 주문 전에 준비 무효(종료 코드 3)로 끝나면 마감 전까지 로그인부터 다시
준비한다. 그 밖의 종료 코드는 주문이 나갔을 수 있으므로 다시 실행하지 않는다.
리허설은 dev-shots/state-rehearsal/<id>/ 상태 폴더를 써서 실전 전송권과 섞지 않는다.
비밀번호·토큰은 다루지 않는다(로그인은 dev/setup.py 가 .env 로 한다).
"""
from __future__ import annotations
import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import KST  # noqa: E402

PY = str(ROOT / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python'))
# 예매 포트. 9232·9243 = 와이프 스카이패스, 9242 = 본인 네이버.
# 9233 은 계측 전용이라 여기 쓰지 않는다.
BOOKING_PORTS = (9232, 9242, 9243, 9244)
PORT = 9232
OUT = ROOT / 'dev-shots' / 'api-day'
EXIT_REPREPARE = 3
ENV = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}


def log(msg):
    print(f'[{datetime.now(KST).strftime("%H:%M:%S")}] [api_day] {msg}', flush=True)


def keep_awake():
    """실행 동안 절전·화면 꺼짐을 막는다(Windows). 프로세스가 끝나면 풀린다."""
    if os.name == 'nt':
        try:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001 | 0x00000002)
        except Exception:
            log('절전 방지 설정 실패 - 전원 설정을 직접 확인한다')


def today_at(hms):
    d = datetime.now(KST).date()
    h, m, s = (int(x) for x in hms.split(':'))
    return datetime(d.year, d.month, d.day, h, m, s, tzinfo=KST)


def lingering_orders(port=None):
    """이 저장소의 live_order 프로세스(PID).

    port 를 주면 **그 포트로 도는 것만** 센다. 2계정 동시 실행에서 한쪽이 다른 쪽의
    주문 프로세스를 죽이지 않게 하려는 것이다(2026-09-23). port 를 주지 않으면 예전처럼 전부.
    """
    if os.name != 'nt':
        return []
    cmd = ("Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
           "Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress")
    try:
        out = subprocess.run(['powershell', '-NoProfile', '-Command', cmd], capture_output=True,
                             text=True, encoding='utf-8', errors='replace', timeout=30).stdout.strip()
        rows = json.loads(out) if out else []
    except Exception:
        return []
    rows = rows if isinstance(rows, list) else [rows]
    mine = str(ROOT).lower()
    want = re.compile(r'(?:^|\s)--port\s+%d(?:\s|$)' % port) if port else None
    out = []
    for r in rows:
        cmd = r.get('CommandLine') or ''
        if 'live_order.py' not in cmd or mine not in cmd.lower() or r['ProcessId'] == os.getpid():
            continue
        if want and not want.search(cmd):
            continue
        out.append(r['ProcessId'])
    return out


def run_step(report, name, args, timeout=300):
    started = time.monotonic()
    log(f'{name}: 시작')
    try:
        p = subprocess.run(args, cwd=ROOT, env=ENV, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
        code, out = p.returncode, (p.stdout or '') + (p.stderr or '')
    except subprocess.TimeoutExpired:
        code, out = -1, 'timeout'
    for line in out.strip().splitlines()[-12:]:
        print('    ' + line, flush=True)
    last = out.strip().splitlines()[-1] if out.strip() else ''
    try:
        verdict = json.loads(last)
    except ValueError:
        verdict = None
    report['steps'].append({'name': name, 'code': code, 'seconds': round(time.monotonic() - started, 1),
                            'result': verdict if isinstance(verdict, dict) else None})
    return code, verdict


def browser(report, restart, port=PORT):
    args = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
            str(ROOT / 'dev' / 'astra_browsers.ps1'), '-Port', str(port)] + (['-Restart'] if restart else [])
    code, _ = run_step(report, f'chrome-{port}' + ('-restart' if restart else ''), args, timeout=120)
    return code == 0


def prepare(report, a):
    """로그인 → 달력(캡처 날짜). 성공하면 True.

    KRW 는 여기서 맞추지 않는다. 9/19 실측으로 홈에서 새로 검색하면 USD 로 돌아가므로
    live_order 의 캡처 통과가 운임 화면에서 직접 맞춘다(site_drive.capture_pass ensure_currency).
    """
    base = [PY, str(ROOT / 'dev' / 'setup.py'), a.destination, '--from', a.origin,
            '--port', str(a.port), '--date', a.capture_iso]
    code, verdict = run_step(report, 'setup-calendar', base)
    if not (isinstance(verdict, dict) and verdict.get('ok')):
        log('달력 복귀 실패')
        return False
    return True


def order_args(a, at, health_at, state_dir):
    label = f'{a.capture_iso[5:7]}월 {a.capture_iso[8:10]}일'
    args = [PY, str(ROOT / 'api_booking' / 'live_order.py'), '--port', str(a.port),
            '--date', a.target_date, '--origin', a.origin, '--destination', a.destination,
            '--flight', a.flight, '--family', a.family, '--capture-date', label,
            '--capture-iso', a.capture_iso, '--capture-cabin', a.capture_cabin,
            '--own-mileage', str(a.own_mileage), '--reuse-member-check',
            '--state-bridge', '--continue-payment', '--inspect-failure',
            '--at', at.strftime('%H:%M:%S'), '--pre-fire-ms', str(a.pre_fire_ms)]
    if health_at is not None:
        args += ['--health-at', health_at.strftime('%H:%M:%S')]
    if a.not_open_shape:
        args += ['--not-open-shape', a.not_open_shape]
    if state_dir:
        args += ['--state-dir', str(state_dir)]
    if a.observe_date:
        args += ['--observe-date', a.observe_date]
    if a.open_retry_gap_ms is not None:
        args += ['--open-retry-gap-ms', str(a.open_retry_gap_ms)]
    if a.order_outcome_file:
        args += ['--order-outcome-file', str(a.order_outcome_file)]
    if a.order_gate_file:
        args += ['--order-gate-file', str(a.order_gate_file)]
    if a.order_gate_timeout is not None:
        args += ['--order-gate-timeout', str(a.order_gate_timeout)]
    return args


def main():
    ap = argparse.ArgumentParser(description='API 예매 무인 체인(9232·9243 와이프 · 9242 본인)')
    ap.add_argument('--mode', required=True, choices=['rehearsal', 'live'])
    ap.add_argument('--port', type=int, default=9232, choices=list(BOOKING_PORTS),
                    help='예매 브라우저 포트. 9232·9243=와이프 스카이패스, 9242=본인 네이버')
    ap.add_argument('--live-state-dir', default='',
                    help='live 에서 이 상태 폴더를 쓴다(동시 실행 시 9232 외 모두). 기본 폴더는 줄 수 없다')
    # 대체 예매 게이트(2026-09-24). 프레스티지가 좌석을 못 잡았을 때만 일반석을 주문한다.
    ap.add_argument('--order-outcome-file', default='', help='주문 판정을 이 파일에 남긴다')
    ap.add_argument('--order-gate-file', default='', help='주문 직전에 이 신호를 본다')
    ap.add_argument('--order-gate-timeout', type=float, default=None, help='신호 대기 상한(초)')
    ap.add_argument('--target-date', required=True)
    ap.add_argument('--capture-iso', required=True, help='이미 열린 캡처 날짜 YYYY-MM-DD(목표와 다른 날)')
    ap.add_argument('--origin', default='CDG')
    ap.add_argument('--destination', default='ICN')
    ap.add_argument('--flight', default='902')
    ap.add_argument('--family', default='KEBONUSPR')
    ap.add_argument('--capture-cabin', default='일반석')
    ap.add_argument('--own-mileage', type=int, required=True)
    ap.add_argument('--pre-fire-ms', type=int, default=0)
    ap.add_argument('--not-open-shape', default='')
    ap.add_argument('--open-retry-gap-ms', type=int, default=None,
                    help='미개방 재조회 간격(응답 뒤, 동시 1건). 주지 않으면 live_order 기본값')
    ap.add_argument('--observe-date', default='')
    ap.add_argument('--at', default='', help='발사 시각 HH:MM:SS. live 필수, rehearsal 도 주면 실전과 같은 시각 흐름')
    ap.add_argument('--health-at', default='', help='세션 점검 시각 HH:MM:SS(--at 과 함께)')
    ap.add_argument('--capture-not-before', default='', help='캡처를 이 시각 전에는 시작하지 않는다')
    ap.add_argument('--reprep-cutoff', default='', help='이 시각 뒤에는 다시 준비하지 않는다(--at 과 함께)')
    ap.add_argument('--observer', action='store_true',
                    help='계측 9233 체인(dev/observer_chain.py)을 별도 프로세스로 함께 띄운다. 실패해도 예매는 계속')
    ap.add_argument('--fire-in-min', type=float, default=20.0, help='rehearsal: 캡처 시작 뒤 발사까지(분)')
    ap.add_argument('--health-before-min', type=float, default=10.0, help='rehearsal: 발사 몇 분 전에 점검')
    ap.add_argument('--no-restart', action='store_true', help='live: Chrome 9232 재기동을 하지 않는다')
    ap.add_argument('--max-attempts', type=int, default=3)
    a = ap.parse_args()
    if a.capture_iso == a.target_date:
        log('캡처 날짜는 목표와 다른 이미 열린 날짜여야 한다')
        return 2
    if a.mode == 'live' and not a.at:
        log('live 는 --at 이 필요하다')
        return 2
    if a.at and not (a.health_at and a.reprep_cutoff):
        log('--at 을 쓰면 --health-at·--reprep-cutoff 도 필요하다(실전과 같은 시각 흐름)')
        return 2
    if a.observer and not a.at:
        log('--observer 는 --at 과 함께만 쓴다(계측 시각을 발사 시각에 맞춘다)')
        return 2
    if a.mode == 'live' and a.observe_date:
        log('live 에서는 관측 조회를 섞지 않는다')
        return 2
    if a.order_outcome_file and a.order_gate_file:
        log('한 실행이 신호를 쓰면서 동시에 기다릴 수는 없다')
        return 2
    if a.observer and a.port != 9232:
        log('계측 체인(9233)은 한 번만 띄운다 - --observer 는 9232 쪽에서만 쓴다')
        return 2
    if a.live_state_dir:
        if a.mode != 'live':
            log('--live-state-dir 는 live 에서만 쓴다(리허설은 자동으로 나뉜다)')
            return 2
        if Path(a.live_state_dir).resolve() == (ROOT / 'dev-shots' / 'state').resolve():
            log('--live-state-dir 는 기본 상태 폴더가 아니어야 한다(전송권·주문 의도가 섞인다)')
            return 2
    if a.port != 9232 and a.mode == 'live' and not a.live_state_dir:
        log(f'{a.port} live 는 --live-state-dir 가 필요하다(9232 와 상태 폴더를 나눈다)')
        return 2

    keep_awake()
    run_id = datetime.now(KST).strftime('%Y%m%d-%H%M%S') + '-' + uuid4().hex[:6]
    OUT.mkdir(parents=True, exist_ok=True)
    report = {'runId': run_id, 'mode': a.mode, 'port': a.port,
              'target': {'date': a.target_date, 'origin': a.origin,
              'destination': a.destination, 'flight': a.flight, 'family': a.family},
              'captureIso': a.capture_iso, 'startedAt': datetime.now(KST).isoformat(), 'steps': []}
    path = OUT / f'{a.mode}-{run_id}.json'

    def save(**extra):
        report.update(extra)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    state_dir = ((ROOT / 'dev-shots' / 'state-rehearsal' / run_id) if a.mode == 'rehearsal'
                 else (Path(a.live_state_dir).resolve() if a.live_state_dir else None))
    # 같은 포트로 도는 것만 본다. 2계정 동시 실행에서 서로를 죽이지 않게 한다.
    left = lingering_orders(a.port)
    if left:
        if a.mode == 'rehearsal':
            log(f'포트 {a.port} 의 live_order 가 이미 실행 중이다(PID {left}) - 리허설을 시작하지 않는다')
            save(result='lingering-process')
            return 2
        # 실전 전: 리허설이 조사 모드로 남아 9232 에 감시를 붙이고 있으면 09시 주문을 막을 수 있다.
        for pid in left:
            log(f'남은 live_order PID {pid} 종료(리허설 잔여 정리)')
            subprocess.run(['taskkill', '/PID', str(pid), '/T', '/F'], capture_output=True)
        report['killedLingering'] = left
    if a.observer:
        # 계측은 예매와 실패를 분리한다. 출력은 별도 로그로만 남기고 결과를 기다리지 않는다.
        obs_log = OUT / f'observer-{run_id}.log'
        obs_args = [PY, str(ROOT / 'dev' / 'observer_chain.py'), '--day', datetime.now(KST).date().isoformat(),
                    '--target', a.target_date, '--origin', a.origin, '--destination', a.destination]
        if a.mode == 'rehearsal':
            obs_args += ['--rehearsal', '--at', a.at]
        observer = subprocess.Popen(obs_args, cwd=ROOT, env=ENV, stdout=obs_log.open('w', encoding='utf-8'),
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        report['observer'] = {'pid': observer.pid, 'log': str(obs_log)}
        log(f'계측 9233 체인 시작(PID {observer.pid}, 로그 {obs_log.name}) - 실패해도 예매는 계속')
    if not browser(report, restart=(a.mode == 'live' and not a.no_restart), port=a.port):
        save(result='chrome-failed')
        return 2
    cutoff = today_at(a.reprep_cutoff) if a.reprep_cutoff else None
    for attempt in range(1, a.max_attempts + 1):
        log(f'준비 시도 {attempt}/{a.max_attempts}')
        if cutoff and datetime.now(KST) >= cutoff:
            log('재준비 마감이 지났다 - 발사하지 않는다')
            save(result='reprep-cutoff')
            return 2
        if not prepare(report, a):
            save()
            if attempt < a.max_attempts:
                time.sleep(10)
                continue
            save(result='prepare-failed')
            return 2
        if a.at:
            at = today_at(a.at)
            health = today_at(a.health_at)
            if a.capture_not_before:
                start = today_at(a.capture_not_before)
                wait = (start - datetime.now(KST)).total_seconds()
                if wait > 0:
                    log(f'캡처 시작 {a.capture_not_before} 까지 {wait:.0f}초 대기')
                    time.sleep(wait)
            if datetime.now(KST) >= health:
                health = None   # 재준비 뒤에는 무장 점검이 대신한다
        else:
            at = (datetime.now(KST) + timedelta(minutes=a.fire_in_min)).replace(microsecond=0)
            health = at - timedelta(minutes=a.health_before_min)
        args = order_args(a, at, health, state_dir)
        log(f'live_order 시작: 발사 {at.strftime("%H:%M:%S")} · 점검 '
            f'{health.strftime("%H:%M:%S") if health else "없음"} · 상태폴더 {state_dir or "기본"}')
        save(fireAt=at.isoformat(), healthAt=health.isoformat() if health else None)
        # 콘솔을 물려준다: 인계 실패 시 조사 모드(summary/exit)를 사용자가 쓸 수 있다.
        code = subprocess.call(args, cwd=ROOT, env=ENV)
        report['steps'].append({'name': f'live_order#{attempt}', 'code': code})
        if code == EXIT_REPREPARE:
            log('주문 전 준비 무효(3) - 다시 준비한다')
            save()
            continue
        save(result='done' if code == 0 else f'live_order-exit-{code}', finishedAt=datetime.now(KST).isoformat())
        log(f'끝: live_order 종료 코드 {code}. 보고서 {path}')
        return code
    save(result='attempts-exhausted')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
