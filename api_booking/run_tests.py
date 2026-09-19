"""API 예매 로컬 시험(T1) 일괄 실행. 사이트·운영 Chrome 에 접속하지 않는다.

  .\\.venv\\Scripts\\python.exe api_booking\\run_tests.py            # 브라우저 없는 시험
  .\\.venv\\Scripts\\python.exe api_booking\\run_tests.py --browser  # 로컬 Chromium 픽스처 시험 포함

test_site_rehydrate.py 는 Git 제외 저장 소스(dev-shots/api-source-2026-09-10)가 있을 때만 돈다.
"""
from __future__ import annotations
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SAVED_SOURCE = ROOT / 'dev-shots' / 'api-source-2026-09-10'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--browser', action='store_true', help='*_browser.py(로컬 Chromium) 포함')
    a = ap.parse_args()
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    rows, failed = [], []
    for path in sorted(HERE.glob('test_*.py')):
        if path.name.endswith('_browser.py') and not a.browser:
            continue
        if path.name == 'test_site_rehydrate.py' and not SAVED_SOURCE.exists():
            rows.append((path.name, 'skip', '저장 소스 없음'))
            continue
        started = time.monotonic()
        p = subprocess.run([sys.executable, str(path)], cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding='utf-8', errors='replace', timeout=600)
        out = (p.stdout or '') + (p.stderr or '')
        ran = re.findall(r'Ran (\d+) tests?', out)
        detail = f'{ran[-1]}개' if ran else ''
        status = 'ok' if p.returncode == 0 else 'FAIL'
        rows.append((path.name, status, f'{detail} {time.monotonic() - started:.1f}s'.strip()))
        if p.returncode:
            failed.append((path.name, out.strip().splitlines()[-15:]))
    for name, status, detail in rows:
        print(f'{status:4} {name:40} {detail}')
    for name, tail in failed:
        print(f'\n--- {name}')
        print('\n'.join(tail))
    total = sum(int(d.split('개')[0]) for _, s, d in rows if s == 'ok' and '개' in d)
    print(f'\n파일 {len(rows)} · 실패 {len(failed)} · 통과 시험 약 {total}개')
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
