"""캡처 날짜 고르기: 달력 화면의 날짜 칸을 읽어 '이미 열려 있고 일반석이 있는' 날을 고른다.

준비 통과(캡처)는 목표 날짜가 아닌 **이미 열린 날짜**로 사이트를 한 번 끝까지 가 보는 것이다.
그 날짜에 일반석이 없으면 준비가 실패한다. 2026-09 에는 사람이 전날 달력을 보고 골랐다.
`api_day.py --capture-iso auto` 가 이 모듈로 스스로 고른다.

달력의 날짜 칸은 이렇게 적혀 있다(실사이트 2026-10-04, FCO→ICN):
  '15 09월 15일 (수) , 성수기일반석, 선택됨'   좌석 있음(가장 낮은 등급이 일반석)
  '18 09월 18일 (토) , 성수기 좌석 없음'       매진
  '14 09월 14일 (화) , 성수기 운항편 없음'     그 요일에 편이 없다
  ''                                         아직 열리지 않았다(오늘+360일 뒤)
조회만 한다. 아무것도 누르지 않는다.
"""
from __future__ import annotations
import re
from datetime import date, timedelta

CELLS_JS = """() => { const out=[];
  const deep=(root,d)=>{ if(!root||d>9) return;
    for(const e of root.querySelectorAll('[id^=dep-fare]')){
      out.push({text:(e.textContent||'').replace(/\\s+/g,' ').trim(),
                disabled:e.getAttribute('aria-disabled')==='true'}); }
    for(const e of root.querySelectorAll('*')) if(e.shadowRoot) deep(e.shadowRoot,d+1); };
  deep(document,0); return out; }"""
_LABEL = re.compile(r'(\d{1,2})월\s*(\d{1,2})일')
TENTATIVE_DAYS = 7     # 먼저 달력을 열어 볼 날: 목표 한 주 전(같은 요일이라 운항할 가능성이 높다)


def tentative(target_iso, days=TENTATIVE_DAYS):
    """달력을 열 때 쓸 임시 날짜. 여기서 달력을 읽고 실제 캡처 날짜를 고른다."""
    return (date.fromisoformat(target_iso) - timedelta(days=days)).isoformat()


def parse_cells(cells, target_iso):
    """달력 칸을 {날짜: 상태} 로. 상태: 'seat:<등급>'·'sold-out'·'no-flight'·'unknown'. 빈 칸은 뺀다."""
    target = date.fromisoformat(target_iso)
    out = {}
    for cell in cells or []:
        text = (cell.get('text') or '') if isinstance(cell, dict) else ''
        m = _LABEL.search(text)
        if not m:
            continue
        month, day = int(m.group(1)), int(m.group(2))
        # 칸에는 연도가 없다. 목표 날짜에서 반년 안쪽의 해로 본다.
        when = None
        for year in (target.year, target.year - 1, target.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if abs((candidate - target).days) <= 183:
                when = candidate
                break
        if when is None:
            continue
        if '운항편 없음' in text:
            state = 'no-flight'
        elif '좌석 없음' in text:
            state = 'sold-out'
        elif cell.get('disabled'):
            state = 'unknown'
        else:
            cabin = next((c for c in ('일반석', '프레스티지', '일등석') if c in text), None)
            state = f'seat:{cabin}' if cabin else 'unknown'
        out[when.isoformat()] = state
    return out


def choose(cells, target_iso, cabin='일반석'):
    """목표보다 앞선 날짜 중 그 등급 좌석이 있는 가장 가까운 날. 없으면 None."""
    days = parse_cells(cells, target_iso)
    usable = sorted(d for d, state in days.items() if d < target_iso and state == f'seat:{cabin}')
    return usable[-1] if usable else None


def read_cells(port, timeout=15000):
    """그 자리의 Chrome 에 떠 있는 달력에서 날짜 칸을 읽는다. 달력 탭이 없으면 빈 목록."""
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=timeout)
        for context in browser.contexts:
            for page in context.pages:
                if 'calendar-fare-bonus' in (page.url or ''):
                    return page.evaluate(CELLS_JS)
    return []
