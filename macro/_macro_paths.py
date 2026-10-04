"""macro/ 의 실행기가 dev/(실전과 함께 쓰는 공용 모듈)를 찾게 한다.

macro/ 는 2026-10-04 에 dev/ 에서 갈라 나왔다. 그전에는 같은 폴더라 runtime·test_calendar 같은
공용 모듈이 저절로 보였다. dev 모듈을 가져오는 파일은 맨 먼저 이 모듈을 가져온다.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_dev = str(ROOT / 'dev')
if _dev not in sys.path:
    sys.path.insert(0, _dev)
