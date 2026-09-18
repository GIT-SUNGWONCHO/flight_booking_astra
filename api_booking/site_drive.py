"""정상 UI를 실제 클릭으로 통과시킨다. 캡처 준비·게이트 인계·결제 단계에 쓴다.

capture_pass 는 게이트의 승객·연락처 확인까지 누른다. 이때 사이트가 보내는 주문
요청(inputTravellers)은 네트워크 단계에서 막아 본문만 캡처한다(D2). 막지 못하면
사이트가 임시 좌석 보유를 만들 수 있으므로 호출자가 중단해야 한다.
결제는 하지 않으며 최종 승인 버튼은 누르지 않는다.

합성 이벤트(dispatchEvent)는 kds-* 커스텀 엘리먼트에서 동작하지 않는다(실측
2026-09-12). 그래서 좌표를 구해 실제 마우스 클릭을 보낸다.
"""
from __future__ import annotations
import json
import re
import time
from urllib.parse import urlsplit

import handoff

CALENDAR = 'https://www.koreanair.com/booking/calendar-fare-bonus'

GATE = 'https://www.koreanair.com/payment/gate/RT/NR'

# click_id 로 누르면 안 되는 대상. 결제하기(btn-payment)는 제공자 창을 여는 단계라
# 허용하고, 최종 승인은 제공자 창 안에서 사용자가 한다. 빈 튜플이어도 이름은 정의돼야 한다.
FORBIDDEN_IDS = ()

# 제공자 창(Npay/카드) 안에서는 아무것도 누르지 않는다. 최종 승인은 사용자만 한다.
# `btn-payment`(결제하기)는 제공자 창을 여는 단계이며 기존 예매 매크로의 17단계에도
# 포함된다. 리허설 완료 기준이 '실제 결제창 도착'이므로 여기까지는 수행한다.


def _box_by_text(page, pattern):
    return page.evaluate("""(pat) => { let hit=null; const re=new RegExp(pat);
      const deep=(root,d)=>{ if(!root||d>10||hit) return;
        for(const x of root.querySelectorAll('button,[role=button],kds-button')){
          const s=(x.textContent||'').trim(); const r=x.getBoundingClientRect();
          if(re.test(s)&&r.width>1&&r.height>1){hit={x:r.left+r.width/2,y:r.top+r.height/2};return;} }
        for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return hit; }""", pattern)


def _box_by_id(page, element_id):
    return page.evaluate("""(eid) => { let hit=null;
      const deep=(root,d)=>{ if(!root||d>10||hit) return;
        const e=root.getElementById ? root.getElementById(eid) : null;
        if(e){ const b=e.getBoundingClientRect();
          if(b.width>1&&b.height>1){hit={x:b.left+b.width/2,y:b.top+b.height/2};return;} }
        for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return hit; }""", element_id)


def click_text(page, pattern, wait=8000):
    box = _box_by_text(page, pattern)
    if not box:
        return False
    page.mouse.click(box['x'], box['y'])
    page.wait_for_timeout(wait)
    return True


def click_id(page, element_id, wait=7000):
    if element_id in FORBIDDEN_IDS:
        raise ValueError(f'금지된 대상: {element_id}')
    height = page.evaluate('() => window.innerHeight') or 900
    for _ in range(3):
        box = _box_by_id(page, element_id)
        if not box:
            return False
        # 하단 고정 바(결제하기)는 스크롤해도 움직이지 않는다. 창 안에 있으면 바로 누른다.
        if box['y'] < 60 or box['y'] > height - 5:
            page.evaluate("(b)=>window.scrollBy(0,b.y-450)", box)
            page.wait_for_timeout(800)
            continue
        page.mouse.click(box['x'], box['y'])
        page.wait_for_timeout(wait)
        return True
    return False


def select_date(page, label, wait=2500):
    """달력에서 '09월 07일' 같은 라벨의 날짜 셀을 고른다. 이미 선택돼 있으면 두 번 누르지 않는다."""
    hit = page.evaluate("""(lab) => { let hit=null;
      const deep=(root,d)=>{ if(!root||d>9||hit) return;
        for(const e of root.querySelectorAll('[id^=dep-fare]')){
          const t=(e.textContent||'').replace(/\\s+/g,' ');
          if(t.includes(lab)){ const r=e.getBoundingClientRect();
            hit={x:r.left+r.width/2, y:r.top+r.height/2,
                 selected:t.includes('선택됨'), soldout:t.includes('좌석 없음')}; return; } }
        for(const e of root.querySelectorAll('*')) if(e.shadowRoot) deep(e.shadowRoot,d+1); };
      deep(document,0); return hit; }""", label)
    if not hit or hit['soldout']:
        return hit
    if not hit['selected']:
        page.mouse.click(hit['x'], hit['y'])
        page.wait_for_timeout(wait)
    return hit


def select_fare(page, cabin='일반석', wait=3000):
    """항공편 화면에서 '항공편명 KE901 일반석 35,000 마일' 형태의 운임 셀을 고른다."""
    box = page.evaluate("""(cab) => { let hit=null;
      const re=new RegExp('^항공편명 KE\\\\d+ '+cab+' [\\\\d,]+ 마일(?: [1-9][0-9]* 석)?$');
      const deep=(root,d)=>{ if(!root||d>10||hit) return;
        for(const x of root.querySelectorAll('*')){
          const s=((x.getAttribute&&x.getAttribute('aria-label'))||x.textContent||'')
                    .trim().replace(/\\s+/g,' ');
          const r=x.getBoundingClientRect(); if(r.width<2||r.height<2) continue;
          if(re.test(s)){ hit={x:r.left+r.width/2, y:r.top+r.height/2, label:s}; return; } }
        for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return hit; }""", cabin)
    if box:
        page.mouse.click(box['x'], box['y'])
        page.wait_for_timeout(wait)
    return box


def calendar_cells(page):
    """달력 날짜 셀 수. 문서가 달력이 아니면 0 이다."""
    return page.evaluate("""() => { let n=0;
      const deep=(r,d)=>{ if(!r||d>9) return;
        n += r.querySelectorAll('[id^=dep-fare]').length;
        for(const x of r.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return n; }""") or 0


def wait_calendar(page, *, min_cells=10, tries=20, interval_ms=1000):
    """달력 셀이 실제로 그려질 때까지 기다린다. 로드 직후 클릭하면 앱이 받지 않는다."""
    drawn = 0
    for _ in range(tries):
        drawn = calendar_cells(page)
        if drawn > min_cells:
            break
        page.wait_for_timeout(interval_ms)
    return drawn


def on_calendar(page):
    """달력 조회 경로이면서 셀이 그려져 있는지. 발사 준비 재확인에 쓴다."""
    return 'calendar-fare-bonus' in (page.url or '') and calendar_cells(page) > 10


def return_to_calendar(page, *, log=print, settle_ms=7000, clock=time.monotonic):
    """캡처 뒤 달력 조회 화면으로 돌아가 셀이 그려졌는지 본다.

    새 문서가 되므로 페이지 안 캡처는 사라진다. 발사는 메모리 캡처로 한다(D2).
    복귀하는 동안에도 주문 요청을 막고 사이트 요청을 센다.
    검색 조건이 달력에 남는지는 실사이트에서 확인하지 않았다.
    반환: {'cells', 'orderRequests', 'siteRequests', 'error'(예외 이름, 있을 때만)}
    """
    out = {'cells': 0}
    watch = HandoffWatch(page.context, page=page, clock=clock)
    watch.start()
    try:
        page.goto(CALENDAR, wait_until='load', timeout=60000)
        page.wait_for_timeout(settle_ms)
        out['cells'] = wait_calendar(page)
        log(f'  달력 복귀: 셀 {out["cells"]}개 · {page.url[-40:]}')
    except Exception as exc:  # noqa: BLE001 - 주문 요청 계수를 호출자에게 넘긴다
        out['error'] = type(exc).__name__
        log(f'  달력 복귀 중 예외: {out["error"]}')
    finally:
        watch.stop()
        out.update(watch_counts(watch.records()))
    return out


def watch_counts(records):
    """관찰 기록을 횟수로만 줄인다. 원문 참조는 버린다."""
    orders = [r for r in records if r['path'] == handoff.ORDER_PATH]
    site = {}
    for rec in records:
        name = rec['path'].rsplit('/', 1)[-1]
        site[name] = site.get(name, 0) + 1
    return {'orderRequests': {'seen': len(orders), 'blocked': sum(r['blocked'] for r in orders),
                              'unblocked': sum(not r['blocked'] for r in orders)},
            'siteRequests': site}


CAPTURE_STEPS = ('calendar', 'date', 'search', 'fare', 'next', 'passenger', 'contact')


def capture_complete(steps):
    """준비 통과가 승객·연락처 확인까지 예외 없이 끝났는지."""
    return (isinstance(steps, dict) and 'error' not in steps
            and all(steps.get(k) is True for k in CAPTURE_STEPS))


def capture_pass(page, date_label, *, cabin='일반석', log=print, clock=time.monotonic,
                 settle_ms=7000):
    """달력에서 시작해 승객·연락처 확인까지 통과시켜 3구간을 캡처하게 한다.

    통과하는 동안 사이트의 주문 요청은 네트워크에서 막는다. 페이지 안 후킹은 요청을
    보내기 전에 본문을 잡으므로 캡처는 남는다. 돌려주는 값은 단계별 성공 여부와
    `orderRequests`(seen·blocked·unblocked), `siteRequests`(경로별 횟수)다.
    unblocked 가 0 이 아니면 사이트가 주문을 만들었을 수 있다.
    클릭·이동 중 예외는 던지지 않고 `error`(예외 이름)로 돌려준다. 예외가 나도 호출자가
    주문 요청 계수를 먼저 보고 기록할 수 있어야 한다(검토 165a57e4 P1).
    """
    steps = {}
    watch = HandoffWatch(page.context, page=page, clock=clock)
    watch.start()
    try:
        _capture_steps(page, date_label, cabin, log, steps, settle_ms)
    except Exception as exc:  # noqa: BLE001
        steps['error'] = type(exc).__name__
        log(f'  준비 통과 중 예외: {steps["error"]}')
    finally:
        watch.stop()
        steps.update(watch_counts(watch.records()))
    return steps


def _capture_steps(page, date_label, cabin, log, steps, settle_ms):
    if 'calendar-fare-bonus' not in page.url:
        page.goto(CALENDAR, wait_until='load', timeout=60000)
        page.wait_for_timeout(settle_ms)
    drawn = wait_calendar(page)
    steps['calendar'] = drawn > 10
    if not steps['calendar']:
        log(f'  달력 셀이 그려지지 않았다(셀 {drawn}개). 누르지 않고 멈춘다.')
        return
    cell = select_date(page, date_label)
    steps['date'] = bool(cell) and not (cell or {}).get('soldout')
    log(f'  날짜 {date_label}: {cell} (셀 {drawn}개)')
    if not steps['date']:
        return
    steps['search'] = False
    for attempt in range(3):
        click_text(page, '^검색$', 11000)
        if 'select-award-flight' in page.url:
            steps['search'] = True
            break
        log(f'  검색 {attempt+1}회차 이동 없음, 재시도')
        page.wait_for_timeout(3000)
    log(f'  검색 → {page.url[-40:]}')
    if 'select-award-flight' not in page.url:
        return
    steps['fare'] = bool(select_fare(page, cabin))
    log(f'  운임 선택: {steps["fare"]}')
    steps['next'] = click_text(page, '^다음$', 12000)
    log(f'  다음 → {page.url[-40:]}')
    if 'payment/gate' not in page.url:
        return
    steps['passenger'] = click_id(page, 'submit-passenger-ADT-0')
    steps['contact'] = click_id(page, 'submit-contact')
    log(f'  승객={steps["passenger"]} 연락처={steps["contact"]}')


def read_gate_summary(page):
    """결제 게이트 화면에 지금 무엇이 올라와 있는지 읽는다. 클릭하지 않는다."""
    return page.evaluate("""() => {
      const out=[]; const seen=new Set();
      const deep=(r,d)=>{ if(!r||d>9) return;
        for(const x of r.querySelectorAll('*')){
          const t=(x.textContent||'').trim().replace(/\\s+/g,' ');
          const b=x.getBoundingClientRect();
          if(t && b.width>1 && b.height>1 && t.length<60 && !seen.has(t)){seen.add(t); out.push(t);} }
        for(const x of r.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
      deep(document,0); return out; }""")


def gate_matches(summary, *, date, mileage=None, flight=None):
    """화면이 목표 여정인지 본다. 확신할 수 없으면 False 를 돌려준다.

    결제 게이트는 편명을 표시하지 않는다(2026-09-13 실측). 날짜는 '09월 07일 (화)'
    처럼 0을 채운 한글 표기다. 그래서 **날짜 + 마일리지**로 대조한다.
    API 가 새 주문을 만들어도 브라우저가 이전 주문을 보고 있으면 날짜가 달라 막힌다.
    """
    joined = ' '.join(summary)
    yyyy, mm, dd = date.split('-')
    date_forms = (f'{mm}월 {dd}일', f'{int(mm)}월 {int(dd)}일',
                  f'{yyyy}.{mm}.{dd}', f'{yyyy}-{mm}-{dd}', f'{yyyy}{mm}{dd}')
    hits = {'date': any(form in joined for form in date_forms)}
    if mileage:
        hits['mileage'] = any(f'{mileage} 마일' in t or t.strip() == mileage for t in summary)
    return all(hits.values()), hits


def _wait_id(page, element_id, timeout_ms=8000):
    """요소가 보일 때까지 기다린다. 제한이 0 이어도 한 번은 확인한다."""
    waited = 0
    while True:
        if _box_by_id(page, element_id):
            return True
        if waited >= timeout_ms:
            return False
        page.wait_for_timeout(300)
        waited += 300


# 기존 매크로 ke_award/util.js alreadyOn 과 같은 규칙으로 동의 상태를 읽는다.
# 차이: 규칙에 걸리는 표지가 없으면 'unknown' 으로 돌려준다(alreadyOn 은 false).
AGREE_STATE_JS = """(eid) => { let el=null;
  const deep=(root,d)=>{ if(!root||d>10||el) return;
    const e=root.getElementById ? root.getElementById(eid) : null; if(e){ el=e; return; }
    for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
  deep(document,0);
  if(!el) return 'missing';
  const a=el.getAttribute('aria-pressed')||el.getAttribute('aria-checked')||el.getAttribute('aria-selected');
  if(a==='true') return 'on';
  if(a==='false') return 'off';
  let n=el;
  for(let d=0; d<2 && n; d++){ const cl=n.classList;
    if(cl) for(const c of cl) if(/^(active|selected|checked|on|agreed|is-active|is-selected|is-checked)$/i.test(c)) return 'on';
    n=n.parentElement; }
  const inp=el.querySelector && el.querySelector('input[type="checkbox"],input[type="radio"]');
  if(inp) return inp.checked ? 'on' : 'off';
  return 'unknown'; }"""


def agree_state(page, agree_id):
    return page.evaluate(AGREE_STATE_JS, agree_id)


def _wait_gone(page, element_id, timeout_ms=4000, step_ms=200):
    waited = 0
    while waited < timeout_ms:
        if not _box_by_id(page, element_id):
            return True
        page.wait_for_timeout(step_ms)
        waited += step_ms
    return not _box_by_id(page, element_id)


def agree_step(page, agree_id, *, log=print, pace=1.0, max_scroll=12):
    """동의 하나를 기존 절차(동의 → 끝까지 스크롤 → 확인)로 처리하고 상태를 다시 읽는다.

    - 이미 켜져 있으면 누르지 않는다(켜진 동의를 다시 눌러 끄는 9/13 08:33 사고 방지).
    - 모달이 뜨지 않았는데 켜지지도 않았으면 실패다. '모달 없음 = 동의됨'으로 보지 않는다.
    - 상태 표지를 읽을 수 없으면(unknown) 누르기 전에도 뒤에도 완료로 보지 않는다.
    - 스크롤 버튼은 사라질 때까지 누른다. 확인 뒤 모달이 닫혀야 한다.
    반환: result(already-on·agreed·agreed-without-modal·실패 사유), clicked, verified, failed
    """
    def w(ms):
        return max(0, int(ms * pace))
    before = agree_state(page, agree_id)
    out = {'id': agree_id, 'before': before, 'clicked': False, 'verified': False, 'failed': False}
    if before in ('missing', 'unknown'):
        # 표지가 없으면 켜진 동의인지 모른다. 눌러서 끌 수 있으므로 누르지 않는다.
        out.update(result=f'state-{before}', failed=True)
    elif before == 'on':
        out.update(result='already-on', verified=True)
    elif not click_id(page, agree_id, wait=w(1500)):
        out.update(result='not-clickable', failed=True)
    else:
        out['clicked'] = True
        if not (_wait_id(page, 'btnScrollDown', w(6000)) or _wait_id(page, 'btnConfirm', w(1500))):
            after = agree_state(page, agree_id)
            out.update(after=after, result='agreed-without-modal' if after == 'on' else 'no-modal',
                       verified=after == 'on', failed=after != 'on')
        else:
            for _ in range(max_scroll):
                if not _box_by_id(page, 'btnScrollDown'):
                    break
                click_id(page, 'btnScrollDown', wait=w(500))
            if _box_by_id(page, 'btnScrollDown'):
                out.update(result='scroll-not-finished', failed=True)
            elif not (_wait_id(page, 'btnConfirm', w(4000)) and click_id(page, 'btnConfirm', wait=w(1500))):
                out.update(result='confirm-missing', failed=True)
            elif not _wait_gone(page, 'btnConfirm', w(4000)):
                out.update(result='modal-not-closed', failed=True)
            else:
                after = agree_state(page, agree_id)
                # 완료가 확인된 경우만 통과한다. 표지가 없어 확인할 수 없으면(unknown) 멈춘다
                # (검토 2ee60c2b P1, API README '각 동의 상태·완료 확인').
                out.update(after=after, result='agreed' if after == 'on' else f'after-{after}',
                           verified=after == 'on', failed=after != 'on')
    log(f'  동의 {agree_id}: {out["result"]} (전 {before}, 클릭 {out["clicked"]}, '
        f'확인 {out["verified"]})')
    return out


MANUAL_STEPS = (
    '8~10  첫 동의 → 약관 끝까지 스크롤 → 확인',
    '11~13 위험품 등 두 번째 동의 → 스크롤 → 확인',
    '14    마일리지 적용',
    '15~16 결제수단 Npay 선택 (ICN 출발. 다른 수단으로 바꾸지 않음)',
    '17    결제하기 → pay.naver.com 새 창 → 최종 승인은 사용자',
)

# 인계 중 지켜볼 요청. 주문·선택 재시작·화면 주문 참조의 출처.
HANDOFF_WATCHED = ((handoff.ORDER_PATH,) + handoff.SELECTION_PATHS
                   + tuple(handoff.REFERENCE_SOURCES))


class HandoffWatch:
    """API 주문 뒤 인계하는 동안 브라우저가 보내는 요청을 지켜본다.

    - 주문 요청(inputTravellers)은 네트워크 단계에서 막는다(context.route → abort).
      인계는 새 주문을 만드는 방법이 아니다. 이 프로세스의 주문은 이미 끝났다.
      주문·선택 요청은 컨텍스트 전체(모든 탭)를 본다.
    - 화면 주문 참조는 관측 계약(GET 쿼리)의 해당 값만 메모리로 읽는다. 어느 페이지의
      주 프레임에서, 관찰 시작 뒤 몇 번째 이동 문서에서 나왔는지 함께 남긴다.
    - 기록은 원문 참조를 담으므로 로그·파일에 쓰지 않는다. 판정 결과만 쓴다.

    한계: 서비스 워커 등 context.route 가 가로채지 못하는 경로는 확인하지 않았다.
    막지 못한 주문 요청은 request 이벤트로 남아 판정에서 실패가 된다.
    """

    def __init__(self, context, *, page=None, clock=time.monotonic):
        self._context = context
        self._page = page
        self._clock = clock
        self._records = {}
        self._started = None
        self._documents = 0

    @staticmethod
    def _is_order(url):
        return urlsplit(url).path == handoff.ORDER_PATH

    def _from_target(self, request):
        """인계 대상 페이지의 주 프레임 요청인지. 프레임이 없는 요청(서비스 워커 등)은 아니다."""
        if self._page is None:
            return False
        try:
            frame = request.frame
            return frame is self._page.main_frame
        except Exception:  # noqa: BLE001
            return False

    def _record(self, request):
        rec = self._records.get(request)
        if rec is None:
            parts = urlsplit(request.url)
            body = None
            if parts.path in handoff.REFERENCE_SOURCES and request.method != 'GET':
                try:
                    body = request.post_data
                except Exception:  # noqa: BLE001 - 본문을 못 읽으면 읽지 못한 참조로 둔다
                    body = None
            value, readable = handoff.reference_in(request.method, request.url, body)
            target = self._from_target(request)
            rec = {'path': parts.path, 'origin': f'{parts.scheme}://{parts.netloc}',
                   'at': self._clock(), 'reference': value, 'readable': readable,
                   'blocked': False, 'target': target,
                   'document': self._documents if target else -1}
            self._records[request] = rec
        return rec

    def _on_navigated(self, frame):
        if self._page is not None and frame is self._page.main_frame:
            self._documents += 1

    def _on_request(self, request):
        if urlsplit(request.url).path in HANDOFF_WATCHED:
            self._record(request)

    def _block_order(self, route):
        rec = self._record(route.request)
        try:
            route.abort()
        except Exception:  # noqa: BLE001 - 막지 못했으면 막았다고 기록하지 않는다
            rec['blocked'] = False
            return
        rec['blocked'] = True

    def start(self):
        if self._started is not None:
            raise RuntimeError('watch-already-started')
        self._started = self._clock()
        self._context.route(self._is_order, self._block_order)
        self._context.on('request', self._on_request)
        if self._page is not None:
            self._page.on('framenavigated', self._on_navigated)
        return self._started

    def stop(self):
        if self._started is None:
            return
        if self._page is not None:
            self._page.remove_listener('framenavigated', self._on_navigated)
        self._context.remove_listener('request', self._on_request)
        self._context.unroute(self._is_order, self._block_order)

    @property
    def started(self):
        return self._started

    def records(self):
        return [dict(r) for r in self._records.values()]

    def judge(self, reference, ordered_at):
        return handoff.judge_screen_order(reference, ordered_at=ordered_at,
                                          started=self._started, requests=self.records(),
                                          now=self._clock())


def _display_hints(summary, *, date, mileage):
    """화면 문구 대조. 필요조건일 뿐 같은 주문의 증거가 아니다."""
    ok, hits = gate_matches(summary, date=date, mileage=mileage)
    hits['krw'] = any('KRW' in t or t.strip().endswith(' 원') for t in summary)
    return ok and hits['krw'], hits


def open_gate(page, *, date, reference, ordered_at, mileage=None, log=print,
              clock=time.monotonic, wait_ms=9000):
    """관찰을 켠 채 결제 게이트를 열고, 화면 주문이 이번 API 주문인지 판정한다.

    반환 (결과, 관찰기). 관찰기는 호출자가 stop 한다. 결과에는 원문 참조가 없다.
    `matched` 는 주문 참조 일치 **그리고** 날짜·마일리지·KRW 문구 일치일 때만 True.
    """
    result = {'navigated': False, 'matched': False, 'order': None, 'hits': {}}
    if not isinstance(reference, str) or not reference.strip():
        result['order'] = 'missing-reference'
        log('  주문 참조가 없어 게이트를 열지 않는다(어느 주문인지 대조할 수 없음).')
        return result, None
    watch = HandoffWatch(page.context, page=page, clock=clock)
    watch.start()
    try:
        page.goto(GATE, wait_until='load', timeout=60000)
        page.wait_for_timeout(wait_ms)
        result['navigated'] = 'payment/gate' in page.url
        if not result['navigated']:
            result['order'] = 'not-gate'
            log(f'  결제 게이트가 아니다: {page.url[:70]}')
            return result, watch
        summary = read_gate_summary(page)
    except BaseException:
        # 관찰기를 돌려주지 못하면 호출자가 해제할 수 없다. 여기서 풀고 다시 던진다.
        watch.stop()
        raise
    ok, hits = _display_hints(summary, date=date, mileage=mileage)
    verdict = watch.judge(reference, ordered_at)
    result.update(order=verdict.state, hits=hits,
                  extraOrderPossible=verdict.extra_order_possible,
                  matched=ok and verdict.same_reference)
    result['counts'] = {'references': verdict.references_seen,
                        'referencesMatched': verdict.references_matched,
                        'otherPageReferences': verdict.other_page_references,
                        'orderRequests': verdict.order_requests,
                        'orderRequestsUnblocked': verdict.order_requests_unblocked,
                        'selectionRequests': verdict.selection_requests}
    log(f'  화면 문구: {hits} / 화면 주문: {verdict.state} '
        f'(참조 {verdict.references_matched}/{verdict.references_seen}, '
        f'다른 탭·이전 문서 참조 {verdict.other_page_references}, '
        f'주문요청 {verdict.order_requests}·미차단 {verdict.order_requests_unblocked}, '
        f'선택요청 {verdict.selection_requests})')
    return result, watch


def gate_handoff(page, *, date, reference, ordered_at, mileage=None, log=print,
                 clock=time.monotonic, wait_ms=9000):
    """pnr 이후 결제 게이트를 열어 이번 주문이 떠 있는지 대조하고 멈춘다. 아무것도 누르지 않는다.

    README §5.2 의 8~17단계(동의 모달·마일리지·Npay·결제하기)는 사용자가 한다.
    날짜·마일리지·KRW 문구만으로는 이전 주문과 구분할 수 없다(9/13 08:17 두 주문이
    같은 날짜·35,000 마일). 게이트가 보낸 주문 참조가 이번 pnr 과 같아야 통과다.
    """
    result, watch = open_gate(page, date=date, reference=reference, ordered_at=ordered_at,
                              mileage=mileage, log=log, clock=clock, wait_ms=wait_ms)
    try:
        if not result['matched']:
            log('  **게이트가 이번 주문으로 확인되지 않았다. 이 화면으로 결제하지 않는다.**')
            if result.get('extraOrderPossible'):
                log('  **인계 중 막지 못한 주문 요청이 있었다. 추가 주문 가능성 — 사용자 확인 필요.**')
            return result
        log('  **게이트 주문 참조가 이번 주문과 같다. 화면 전체 상태·동의·Npay 는 미확인이다.**')
        for step in MANUAL_STEPS:
            log(f'    {step}')
        return result
    finally:
        if watch is not None:
            watch.stop()


AGREE_IDS = ('btn-resv-agree-1', 'btn-resv-agree-3')
# 마일리지 적용이 부르는 차감 API. contracts.PATHS 의 공개 호출부 확인 경로이며 실제 응답
# 구조는 수집하지 못했다. 그래서 HTTP 200·JSON·오류 필드 없음만 본다(적용액 대조 아님).
DEDUCT_PATH = '/api/et/bonusDeduct/bonusBookingDeductMileage'
_DEDUCT_ERROR_KEYS = ('error', 'errors', 'code', 'errorCode', 'errorList', 'errorMessage',
                      'responseCode', 'responseMessage')


def apply_mileage(page, *, log=print, pace=1.0):
    """마일리지 적용을 누르고 이 페이지의 차감 API 응답으로 적용을 확인한다(검토 2ee60c2b P2).

    응답이 없거나 200 이 아니거나 오류 필드가 있으면 verified=False 다. 좌표 클릭 성공은
    적용 증거가 아니다. 응답 원문은 기록하지 않는다.
    """
    timeout = max(1, int(8000 * pace))
    out = {'clicked': False, 'verified': False, 'result': 'apply-button-missing'}
    if not _box_by_id(page, 'btnAwardUseMileageApply'):
        return out
    try:
        with page.expect_response(lambda r: urlsplit(r.url).path == DEDUCT_PATH,
                                  timeout=timeout) as info:
            out['clicked'] = click_id(page, 'btnAwardUseMileageApply', wait=max(0, int(1000 * pace)))
            if not out['clicked']:
                raise TimeoutError('not-clicked')
        response = info.value
    except Exception:  # noqa: BLE001 - 응답을 못 봤으면 적용 미확인
        out['result'] = 'apply-not-clicked' if not out['clicked'] else 'deduct-response-unobserved'
        log(f'  마일리지 적용: {out["result"]}')
        return out
    try:
        status = response.status
        data = json.loads(response.text())
    except Exception:  # noqa: BLE001
        status, data = getattr(response, 'status', None), None
    if status != 200:
        out['result'] = 'deduct-http-error'
    elif type(data) is not dict:
        out['result'] = 'deduct-unreadable'
    elif (any(data.get(k) not in (None, '', False, [], {}) for k in _DEDUCT_ERROR_KEYS)
          or data.get('success') is False or data.get('isSuccess') is False):
        out['result'] = 'deduct-business-error'
    else:
        out.update(result='deduct-response-ok', verified=True)
    log(f'  마일리지 적용: {out["result"]} (적용액 대조 아님)')
    return out
AGREEMENT_WARNING = ('체크해 주세요', '동의해 주세요', '확인 후 체크')


def _radio_checked(page, element_id):
    return page.evaluate("""(eid) => { let c=null;
      const deep=(x,d)=>{ if(!x||d>11||c!==null) return;
        const e=x.getElementById ? x.getElementById(eid) : null;
        if(e){ c=e.checked===true; return; }
        for(const y of x.querySelectorAll('*')) if(y.shadowRoot) deep(y.shadowRoot,d+1); };
      deep(document,0); return c; }""", element_id)


_KRW_TOKEN = re.compile(r'(?<![\d,.])(\d{1,3}(?:,\d{3})+|\d+)\s*원')


def krw_amounts(text):
    """본문에서 '원'이 붙은 금액 토큰을 정수로 모은다. 부분 문자열로 맞추지 않는다."""
    return [int(t.replace(',', '')) for t in _KRW_TOKEN.findall(text or '')]


def amount_verdict(text, expected):
    """기대 금액 판정. 최종 결제액 필드를 식별할 계약이 없으므로, 원 금액 표기가 하나의 값뿐이고
    그 값이 기대 금액과 같을 때만 matched 다. 서로 다른 금액이 섞이면 ambiguous(미확인)."""
    try:
        want = int(expected)
    except (TypeError, ValueError):
        return 'no-expected'
    found = set(krw_amounts(text))
    if not found:
        return 'not-visible'
    if found == {want}:
        return 'matched'
    return 'mismatch' if want not in found else 'ambiguous'


def wait_provider_window(page, original_pages, *, expected, amount=None, timeout_ms=25000,
                         step_ms=500, log=print):
    """결제하기 뒤 이번 실행의 새 제공자 창을 기다려 판정한다. 제공자 창 안에서는 누르지 않는다.

    판정은 운영 dev/payment_window.inspect_payment_window 를 그대로 쓴다. 인계 대상 게이트가
    연 창(opener)만 후보로 본다. 다른 탭이 연 창은 세기만 한다(검토 2ee60c2b P1).
    금액은 amount_verdict 로 본다. 경고 문구가 게이트에 뜨고 새 창이 없으면 곧바로 멈춘다.
    """
    import payment_window
    best, waited = {'ready': False, 'reason': 'no-new-payment-window'}, 0
    while True:
        others = 0
        for candidate in list(page.context.pages):
            if candidate in original_pages or candidate.is_closed():
                continue
            try:
                opener = candidate.opener()
            except Exception:  # noqa: BLE001
                opener = None
            if opener is not page:
                others += 1
                continue
            value = payment_window.inspect_payment_window(candidate, expected)
            if value.get('ready') and amount is not None:
                try:
                    text = candidate.evaluate("() => document.body ? document.body.innerText : ''")
                except Exception:  # noqa: BLE001
                    text = ''
                value['amountVerdict'] = amount_verdict(text, amount)
                # 혜택/포인트 등 다른 금액과 구분: 최종 결제 버튼에 명시된 금액만 대조한다.
                buttons=candidate.evaluate("""()=>[...document.querySelectorAll('button,[role=button]')]
                  .filter(e=>e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'
                    &&/결제하기|동의하고\\s*결제/.test(e.innerText||''))
                  .map(e=>e.innerText||'')""")
                priced=[t for t in buttons if krw_amounts(t)]
                if len(priced)==1:
                    value['amountVerdict']=amount_verdict(priced[0],amount)
                    value['amountSource']='payment-button'
                scoped=payment_amount_texts(candidate)
                if len(scoped)==1:
                    scoped_verdict=amount_verdict(scoped[0],amount)
                    if priced and (len(priced)!=1 or value['amountVerdict']!='matched' or scoped_verdict!='matched'):
                        value['amountVerdict']='conflicting-payment-amounts'
                        value['amountSource']='payment-button-and-total'
                    else:
                        value['amountVerdict']=scoped_verdict
                        value['amountSource']='accessible-payment-total'
                value['amountMatched'] = value['amountVerdict'] == 'matched'
            if value.get('ready') or not best.get('provider'):
                best = value
            if value.get('ready'):
                best['otherTabWindows'] = others
                return best
        best['otherTabWindows'] = others
        if not best.get('provider'):
            summary = ' '.join(read_gate_summary(page))
            if any(w in summary for w in AGREEMENT_WARNING):
                return {'ready': False, 'reason': 'agreement-warning-on-gate'}
        if waited >= timeout_ms:
            return best
        page.wait_for_timeout(step_ms)
        waited += step_ms


def payment_amount_texts(page):
    """Npay 결제금액의 접근성 텍스트. 애니메이션용 aria-hidden 숫자는 복제본에서만 제외."""
    return page.evaluate('''()=>[...document.querySelectorAll('*')]
      .filter(e=>e.children.length===0&&e.textContent.trim()==='결제금액')
      .filter(e=>e.parentElement.getClientRects().length&&getComputedStyle(e.parentElement).visibility!=='hidden')
      .map(e=>{const c=e.parentElement.cloneNode(true);
        c.querySelectorAll('[aria-hidden="true"]').forEach(x=>x.remove());
        return c.textContent.replace(/\\s+/g,' ').trim();})''')


def payment_pass(page, *, flight, date, reference=None, ordered_at=None, mileage=None,
                 log=print, navigate=True, clock=time.monotonic, wait_ms=9000,
                 origin='ICN', destination=None, amount=None, pace=1.0,
                 window_timeout_ms=25000, existing_watch=None, mileage_verifier=None):
    """pnr 이후 기존 예매 절차(동의~결제하기)를 브라우저 클릭으로 이어가고 새 결제창을 판정한다(D5).

    **사용자 확정(2026-09-13): 동의부터는 API 가 아니라 기존 브라우저 클릭 방식.**
    - 게이트 주문 참조가 이번 주문과 같을 때만 누른다(D1). 이미 열린 게이트는 누르지 않는다.
    - 동의 두 개는 각각 상태를 읽고 처리한다. 실패하면 뒤 단계로 가지 않는다.
    - 결제수단은 방향별 목표만 쓴다. ICN 출발 Npay 외에는 이 함수가 다루지 않는다.
    - 완료(`completed`)는 새 제공자 창 ready + 기대 금액 표기 + NaverPay 요청 참조 일치 +
      결제하기 뒤 화면 주문 재판정 통과일 때만이다. 제공자 창 안에서는 아무것도 누르지 않는다.
    """
    def w(ms):
        return max(0, int(ms * pace))
    if not navigate and existing_watch is None:
        log('  이미 열린 게이트는 어느 주문인지 대조할 수 없다. 아무것도 누르지 않는다.')
        return {'navigated': False, 'matched': False, 'order': 'unverifiable-existing-gate',
                'steps': {}, 'completed': False, 'stage': 'not-started'}
    import payment_window
    try:
        expected = payment_window.payment_provider(origin, destination)
    except ValueError:
        expected = None
    # ICN 도착(현대카드)은 결제수단 자동 선택을 검증하지 않았다(2026-09-19 사용자 결정):
    # 동의·마일리지·주문 재판정까지만 하고 결제수단 선택 직전에 멈춰 사용자에게 넘긴다.
    stop_before_method = expected == 'hyundai'
    if expected not in ('npay', 'hyundai'):
        log(f'  방향 {origin}-{destination} 의 결제수단은 이 단계가 다루지 않는다.')
        return {'navigated': False, 'matched': False, 'order': 'unsupported-provider',
                'steps': {}, 'completed': False, 'stage': 'unsupported-provider'}
    if existing_watch is not None:
        if (navigate or not isinstance(existing_watch, HandoffWatch)
                or existing_watch._page is not page or existing_watch._context is not page.context
                or existing_watch.started is None or page.url != GATE):
            return {'matched':False,'completed':False,'stage':'invalid-existing-watch','steps':{}}
        watch=existing_watch
        verdict=watch.judge(reference, ordered_at)
        display,hits=_display_hints(read_gate_summary(page),date=date,mileage=mileage)
        result={'navigated':False,'matched':verdict.same_reference and display,
                'order':verdict.state,'hits':hits}
    else:
        result, watch = open_gate(page, date=date, reference=reference, ordered_at=ordered_at,
                                  mileage=mileage, log=log, clock=clock, wait_ms=wait_ms)
    result.update(steps={}, completed=False, stage='gate')
    try:
        if not result['matched']:
            log('  **화면 주문이 이번 주문으로 확인되지 않았다. 아무것도 누르지 않고 중단한다.**')
            return result
        for agree_id in AGREE_IDS:
            step = agree_step(page, agree_id, log=log, pace=pace)
            result['steps'][agree_id] = step
            if step['failed']:
                result['stage'] = f'agree-failed:{agree_id}'
                log(f'  **동의 {agree_id} 실패({step["result"]}). 뒤 단계로 가지 않는다.**')
                return result
        # 두 번째 동의 처리 중 첫 동의가 꺼지지 않았는지 다시 본다. 켜짐 확인만 통과다.
        for agree_id in AGREE_IDS:
            if agree_state(page, agree_id) != 'on':
                result['stage'] = f'agree-turned-off:{agree_id}'
                log(f'  **동의 {agree_id} 가 꺼져 있다. 결제하기를 누르지 않는다.**')
                return result
        mileage_step = (mileage_verifier(page,mileage) if mileage_verifier is not None
                        else apply_mileage(page, log=log, pace=pace))
        result['steps']['mileage'] = mileage_step
        if not mileage_step['verified']:
            result['stage'] = f'mileage-{mileage_step["result"]}'
            log(f'  **마일리지 적용을 확인하지 못했다({mileage_step["result"]}). 결제하기로 가지 않는다.**')
            return result
        if stop_before_method:
            again = watch.judge(reference, ordered_at)
            result['orderBeforePayment'] = again.state
            if not again.same_reference:
                result['stage'] = 'order-changed-before-payment'
                log(f'  **결제수단 직전 화면 주문 판정이 {again.state} 로 바뀌었다. 사용자 확인 필요.**')
                return result
            result.update(stage='user-payment-method', handedToUser=True)
            log('  **동의·마일리지 확인 완료. 결제수단(한국발행 카드→현대카드)과 결제하기는 사용자가 한다.**')
            return result
        if _radio_checked(page, 'rad-naverpay') is not True:
            label=page.locator('label[for="rad-naverpay"]')
            if label.count()==1 and label.is_visible():
                try:
                    if not label.is_enabled():
                        result['stage']='npay-not-selected'
                        return result
                    label.click(timeout=5000)
                    page.wait_for_timeout(w(1500))
                    result['steps']['npay']=True
                except Exception:
                    result['stage']='npay-not-selected'
                    return result
            else:
                result['steps']['npay'] = click_id(page, 'rad-naverpay', wait=w(1500))
        checked = _radio_checked(page, 'rad-naverpay')
        result['steps']['npayChecked'] = checked
        log(f'  Npay 선택 확인={checked}')
        if checked is not True:
            result['stage'] = 'npay-not-selected'
            log('  **Npay 가 선택되지 않았다. 다른 결제수단 창이 열리지 않게 결제하기를 누르지 않는다.**')
            return result
        # 클릭하는 동안 화면이 다른 주문·새 주문으로 바뀌지 않았는지 결제하기 직전에 다시 본다.
        again = watch.judge(reference, ordered_at)
        result['orderBeforePayment'] = again.state
        if not again.same_reference:
            result['stage'] = 'order-changed-before-payment'
            log(f'  **결제하기 직전 화면 주문 판정이 {again.state} 로 바뀌었다. 누르지 않는다.**')
            return result
        original = set(page.context.pages)
        result['steps']['payment'] = click_id(page, 'btn-payment', wait=w(500))
        log(f'  결제하기 클릭: {result["steps"]["payment"]}')
        if not result['steps']['payment']:
            result['stage'] = 'payment-button-missing'
            return result
        window = wait_provider_window(page, original, expected=expected, amount=amount,
                                      timeout_ms=window_timeout_ms, step_ms=w(500) or 50, log=log)
        result['paymentWindow'] = {k: window.get(k) for k in
                                   ('ready', 'reason', 'provider', 'stage', 'loginRequired',
                                    'errorPage', 'merchantVisible', 'amountVisible', 'amountMatched',
                                    'amountVerdict', 'otherTabWindows')}
        # 결제하기 클릭과 대기 중에 나온 주문 요청도 인계 실패다(차단됐어도).
        after = watch.judge(reference, ordered_at)
        result['orderAfterPayment'] = after.state
        npay_linked = handoff.NPAY_SESSION_PATH in after.matched_sources
        result['npayReferenceMatched'] = npay_linked
        if not after.same_reference:
            result.update(matched=False, extraOrderPossible=after.extra_order_possible,
                          stage='order-changed-after-payment')
            log(f'  **결제하기 이후 화면 주문 판정이 {after.state} 로 바뀌었다. 인계 실패로 본다.**')
            return result
        if not window.get('ready'):
            result['stage'] = f'provider-window:{window.get("stage") or window.get("reason")}'
            log(f'  **목표 결제창에 도착하지 못했다({result["stage"]}). 다른 결제수단으로 바꾸지 않는다.**')
            return result
        if amount is None or window.get('amountMatched') is not True:
            result['stage'] = f'provider-amount-{window.get("amountVerdict") or "unchecked"}'
            log(f'  **Npay 창 금액을 이번 운임 금액으로 확인하지 못했다({result["stage"]}). 완료가 아니다.**')
            return result
        if not npay_linked:
            result['stage'] = 'npay-reference-unobserved'
            log('  **Npay 결제 세션 요청이 이번 주문 참조로 관측되지 않았다. 완료가 아니다.**')
            return result
        result.update(completed=True, stage='npay-checkout')
        log('  **이번 주문의 Npay 결제창에 도착했다. 제공자 창 안에서는 누르지 않는다. 최종 승인은 사용자.**')
        return result
    finally:
        if watch is not None and existing_watch is None:
            watch.stop()
