"""시험 일괄 실행. 실사이트와 로그인된 Chrome 에 접속하지 않는다(픽스처·로컬 Chromium 만).

  .venv/Scripts/python.exe run_tests.py                 전부
  .venv/Scripts/python.exe run_tests.py live            예매 프로그램      api_booking/test_*.py
  .venv/Scripts/python.exe run_tests.py engine          페이지 스크립트·공용  test/
  .venv/Scripts/python.exe run_tests.py macro           옛 브라우저 매크로   macro/test/
  .venv/Scripts/python.exe run_tests.py research        조사 도구          research/test/
  .venv/Scripts/python.exe run_tests.py -k hud -k gate  이름에 그 글자가 든 것만
  .venv/Scripts/python.exe run_tests.py --no-browser    로컬 Chromium 을 띄우는 시험을 뺀다(빠르다)

여러 묶음을 함께 줄 수 있다(live engine). 하나라도 실패하면 종료 코드 1.
시험은 파일마다 따로 돌린다. 멈춘 시험은 --timeout(초) 뒤 끊고 실패로 센다.
Chrome 을 이름으로 종료하지 않는다 - 예매용 Chrome 을 건드리지 않기 위해서다.
"""
from __future__ import annotations
import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GROUPS = {
    'live': ('예매 프로그램', ['api_booking/test_*.py']),
    'engine': ('페이지 스크립트·공용', ['test/test_*.js', 'test/test_*.py']),
    'macro': ('옛 브라우저 매크로', ['macro/test/test_*.py']),
    'research': ('조사 도구', ['research/test/test_*.py']),
}
# Git 에 넣지 않는 저장 소스가 있어야 도는 시험. 없으면 건너뛴다.
NEEDS = {'test_site_rehydrate.py': ROOT / 'dev-shots' / 'api-source-2026-09-10'}


def uses_browser(path):
    try:
        return 'playwright' in path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return False


def run_one(path, timeout, env):
    if path.suffix == '.js':
        node = shutil.which('node')
        if not node:
            return 'skip', 'node 없음', 0.0
        cmd = [node, str(path)]
    else:
        cmd = [sys.executable, str(path)]
    started = time.monotonic()
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return 'FAIL', f'{timeout}초 안에 끝나지 않음', time.monotonic() - started
    took = time.monotonic() - started
    out = (p.stdout or '') + (p.stderr or '')
    if p.returncode == 0:
        ran = re.findall(r'Ran (\d+) tests?', out)
        return 'ok', (f'{ran[-1]}개' if ran else ''), took
    last = [l for l in out.strip().splitlines() if l.strip()]
    return 'FAIL', (last[-1][:90] if last else f'종료 코드 {p.returncode}'), took


def main():
    ap = argparse.ArgumentParser(description='시험 일괄 실행(실사이트 접속 없음)')
    ap.add_argument('groups', nargs='*', choices=list(GROUPS), default=[],
                    help='묶음: ' + ' '.join(GROUPS) + ' (없으면 전부)')
    ap.add_argument('-k', action='append', default=[], metavar='글자', help='파일 이름에 이 글자가 든 것만')
    ap.add_argument('--no-browser', action='store_true', help='로컬 Chromium 을 띄우는 시험을 뺀다')
    ap.add_argument('--timeout', type=int, default=300, help='파일 하나의 제한 시간(초)')
    a = ap.parse_args()
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    failed, counts = [], {'ok': 0, 'FAIL': 0, 'skip': 0}
    started = time.monotonic()
    for name in (a.groups or list(GROUPS)):
        title, patterns = GROUPS[name]
        files = [f for pat in patterns for f in sorted(ROOT.glob(pat))]
        if a.k:
            files = [f for f in files if any(k in f.name for k in a.k)]
        if not files:
            continue
        print(f'\n[{name}] {title} - {len(files)}개 파일', flush=True)
        for f in files:
            rel = f.relative_to(ROOT).as_posix()
            if a.no_browser and uses_browser(f):
                status, detail, took = 'skip', '브라우저 시험', 0.0
            elif f.name in NEEDS and not NEEDS[f.name].exists():
                status, detail, took = 'skip', '저장 소스 없음', 0.0
            else:
                status, detail, took = run_one(f, a.timeout, env)
            counts[status] += 1
            if status == 'FAIL':
                failed.append(rel)
            print(f'  {status:<4} {rel:<46} {took:6.1f}s  {detail}', flush=True)
    total = time.monotonic() - started
    print(f'\n통과 {counts["ok"]} · 실패 {counts["FAIL"]} · 건너뜀 {counts["skip"]}  ({total:.0f}초)')
    for rel in failed:
        print(f'  실패: {rel}')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
