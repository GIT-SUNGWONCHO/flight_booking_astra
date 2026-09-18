"""계측 9233 체인: Chrome 9233 → setup(본인 네이버 로그인·달력) → calendar_observer.

예매(9232)와 실패를 분리한다(AGENTS §3). 이 프로세스가 실패해도 예매 체인은 계속한다.
주문·좌석 선택을 하지 않는다. 로그인 자격정보는 다루지 않는다(setup.py 가 저장된 세션을 쓴다).

  실전    python dev/observer_chain.py --day 2026-09-19 --target 2027-09-14
  리허설  python dev/observer_chain.py --day 2026-09-19 --target 2027-09-07 --rehearsal --at 04:00:00
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = str(ROOT / ('.venv/Scripts/python.exe' if os.name == 'nt' else '.venv/bin/python'))
ENV = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}


def log(msg):
    print(f'[{datetime.now().strftime("%H:%M:%S")}] [observer_chain] {msg}', flush=True)


def step(name, args, timeout):
    log(f'{name}: 시작')
    try:
        code = subprocess.run(args, cwd=ROOT, env=ENV, timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        code = -1
    log(f'{name}: 종료 코드 {code}')
    return code


def main():
    ap = argparse.ArgumentParser(description='계측 9233 체인')
    ap.add_argument('--day', required=True)
    ap.add_argument('--target', required=True, help='계측 목표 출발일(달력 준비 날짜 겸)')
    ap.add_argument('--origin', default='CDG')
    ap.add_argument('--destination', default='ICN')
    ap.add_argument('--rehearsal', action='store_true')
    ap.add_argument('--at', default='', help='리허설 개방(발사) 시각 HH:MM:SS')
    a = ap.parse_args()
    if a.rehearsal and not a.at:
        log('리허설은 --at 이 필요하다')
        return 2
    if step('chrome-9233', ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                            str(ROOT / 'dev' / 'astra_browsers.ps1'), '-Port', '9233'], 120) != 0:
        return 2
    ready = False
    for attempt in range(2):
        if step(f'setup-9233#{attempt + 1}', [PY, str(ROOT / 'dev' / 'setup.py'), a.destination,
                '--from', a.origin, '--port', '9233', '--date', a.target], 300) == 0:
            ready = True
            break
        time.sleep(10)
    if not ready:
        log('9233 로그인·달력 준비 실패 - 계측하지 않는다(예매와 무관)')
        return 2
    args = [PY, str(ROOT / 'dev' / 'calendar_observer.py'), '--day', a.day]
    if a.rehearsal:
        args += ['--rehearsal', '--at', a.at, '--target', a.target]
    return step('calendar_observer', args, 3600)


if __name__ == '__main__':
    raise SystemExit(main())
