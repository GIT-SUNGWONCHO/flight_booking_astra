"""research/ 의 조사 도구가 api_booking/(예매 프로그램)·dev/(공용) 모듈을 찾게 한다.

research/ 는 2026-10-04 에 api_booking/·dev/ 에서 갈라 나왔다. 그 폴더의 모듈을 가져오는 파일은
맨 먼저 이 모듈을 가져온다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for _name in ('dev', 'api_booking'):
    _p = str(ROOT / _name)
    if _p not in sys.path:
        sys.path.insert(0, _p)
