"""달력을 건너뛰고 조회 화면에서 그대로 시작하기 (백로그 1).

처음에는 조회 주소에 날짜를 박아 밖에서 뛰어들려고 했다. 실측에서 막혔다 - 그 주소는
/booking/select-award-flight/departure 뿐이고 물음표 뒤가 비어 있으며, 응답에
jsessionId 와 pageTicket 이 있다. 서버가 흐름 순서를 강제한다는 뜻이라 중간 페이지로
뛰어드는 것 자체를 막는다.

방법을 바꿨다: 이미 조회 화면에 서 있다가 그 자리에서 새로고침한다. 세션 안에 있으니
티켓도 살아 있고, 주소에 날짜가 없어도 상관없다. 조회 화면은 자기 화면에 7일치 날짜
띠를 들고 있어서 새로 열린 날도 거기 있다. 달력 한 장과 그 전환이 통째로 빠진다.

여기서 확인하는 것은 "빨라졌나" 가 아니라 "엉뚱한 날을 누르지 않는가" 다. 3초 벌자고
잘못된 날짜로 마일리지를 태우는 건 말이 안 되므로, 조금이라도 확신이 없으면 건너뛰지
않고 달력으로 가야 한다.

실행:  .venv/Scripts/python.exe test/test_skipcal.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ke_award.runner import launch_with_retry  # noqa: E402

USERSCRIPT = ROOT / "userscript" / "ke-award-macro.user.js"
FX = ROOT / "test" / "fixture" / "booking"

# 픽스처를 file:// 로 열면 페이지가 자기 API 를 XHR 로 못 부른다(크롬이 file 간
# 요청을 막는다). 그런데 이 기능의 판정 근거가 바로 그 API 응답이라 그러면 아무것도
# 확인할 수 없다. 가짜 호스트를 가로채 파일로 응답한다 - 덤으로 주소 경로가 실제와
# 같아져서 "조회 페이지인가" 판정도 진짜와 동일하게 검사된다.
HOST = "https://ke.test"
CAL = HOST + "/booking/calendar-fare-bonus"
DEP = HOST + "/booking/select-award-flight/departure"

MIME = {".html": "text/html", ".json": "application/json"}


def availability(day: str, seats: bool, soldout: bool = False) -> str:
    """조회 화면이 좌석을 그릴 때 쓰는 응답. 실측(2026-09-01) 모양 그대로.

    좌석이 아직 안 열린 날이면 availFlightList 가 비어 서버 응답에서 날짜를 알 수
    없다 - 그때는 검색 위젯만이 근거가 된다. 그 상황을 그대로 재현해야 의미가 있다.

    soldout=True 는 '날짜는 열렸고 응답도 오는데 프레스티지만 매진' 인 경우다
    (실전 2026-09-01 CDG). KEBONUSPR.soldout:true, 일반석은 남아 있다.
    운항사 필드(operationCarrierCode/codeShare)를 넣어야 매진 판정이 코드셰어를
    걸러낼 수 있다 - keCabin 이 대한항공 운항편만 본다."""
    strip = [f"202708{d:02d}" for d in range(18, 25)]
    KE = {"carrierCode": "KE", "flightNumber": "931",
          "operationCarrierCode": "KE", "codeShare": False}
    if soldout:
        flights = [{
            "flightId": "0", "departureDate": day + "132000",
            "flightInfoList": [KE],
            "commercialFareFamilyList": [
                {"fareFamily": "KEBONUSEY", "seatCount": "9", "soldout": False,
                 "totalMileage": "35000"},
                {"fareFamily": "KEBONUSPR", "seatCount": "0", "soldout": True,
                 "totalMileage": "62500"}],
        }]
    elif seats:
        flights = [{
            "flightId": "0", "departureDate": day + "132000",
            "flightInfoList": [KE],
            "commercialFareFamilyList": [
                {"fareFamily": "KEBONUSPR", "seatCount": "1", "soldout": False,
                 "totalMileage": "62500"}],
        }]
    else:
        flights = []
    return json.dumps({"upsellBoundAvailList": [{
        "boundId": "0",
        "upsellCalendarFareList": [{"date": d} for d in strip],
        "availFlightList": flights,
    }]}, ensure_ascii=False)


def serve(route):
    url = route.request.url
    path = url.split("?")[0][len(HOST):]
    q = dict(pair.split("=", 1) for pair in url.split("?")[1].split("&")) if "?" in url else {}
    if path.endswith("awardAvailability.json"):
        # 날짜와 좌석 유무는 요청에 따라 만들어 준다. 고정 파일로 두면 화면이 보는
        # 날짜와 응답의 날짜가 어긋나 매크로가(옳게) 거부해버린다.
        route.fulfill(status=200, content_type="application/json",
                      body=availability(q.get("d", "20270821"), q.get("seats") == "1",
                                        q.get("so") == "1"))
        return
    f = FX.parent / path.lstrip("/")
    if f.is_dir() or not f.suffix:
        f = f.with_suffix(".html")
    if not f.exists():
        route.fulfill(status=404, body="not found")
        return
    route.fulfill(status=200, content_type=MIME.get(f.suffix, "text/plain"),
                  body=f.read_bytes())

fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {label}{('  <- ' + detail) if detail and not ok else ''}")
    if not ok:
        fails.append(label)


STEPS = """[
  {sel:'#dep-fare-22', text:'22 08월 22일 (일)', tag:'div',
   url:'/booking/calendar-fare-bonus', dynamicDate:true},
  {sel:'#search', text:'검색', tag:'button', url:'/booking/calendar-fare-bonus'},
  /* 실제 steps.json 과 같이 좌석 단계는 dynamicCabin 이다 - 고정 셀렉터가 아니라
     그날 화면에서 등급으로 찾는다. 좌석이 아직 안 열렸을 때 다시 불러오는 것도
     이 단계에 걸려 있다. */
  {sel:'#seat', text:'프레스티지', tag:'button', dynamicCabin:true,
   url:'/booking/select-award-flight/departure'},
  {sel:'#next', text:'다음', tag:'button',
   url:'/booking/select-award-flight/departure'}
]"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    js = USERSCRIPT.read_text(encoding="utf-8")
    profile = ROOT / ".test-profile-skip"
    shutil.rmtree(profile, ignore_errors=True)

    with sync_playwright() as p:
        ctx = launch_with_retry(p, user_data_dir=str(profile), headless=True)
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.set_default_timeout(30000)
            ctx.add_init_script(js)
            ctx.route(HOST + "/**", serve)

            def land(url: str, fresh_loads: bool = False) -> None:
                pg.goto(url, timeout=60000)
                pg.wait_for_function("() => !!window.KE_REC && !!window.KE_HUD", timeout=20000)
                if fresh_loads:
                    # 픽스처의 로드 횟수는 sessionStorage 에 쌓인다. 앞 케이스가
                    # 올려둔 값을 그대로 두면 "아직 좌석 없음" 상황이 재현되지 않는다.
                    pg.evaluate("() => { sessionStorage.removeItem('loads'); }")
                    pg.reload()
                    pg.wait_for_function("() => !!window.KE_HUD", timeout=20000)

            def setup(expect: str, skip: bool = True) -> None:
                pg.evaluate(f"""() => {{
                  KE_REC.state.steps = {STEPS};
                  KE_REC.state.playing = false; KE_REC.state.playAfterReload = false;
                  KE_REC.state.allowPay = false;
                  KE_REC.state.expectDate = {expect!r};
                  KE_REC.state.cabin = '프레스티지';
                  KE_REC.save();
                  KE_HUD.state.startAt = {'departure' if skip else 'calendar'!r};
                  KE_HUD.save();
                  KE_HUD.render();
                }}""")

            def press_play() -> None:
                # ▶ 재생 은 '고급 설정' 안에 접혀 있다(2026-10-04). 사람이 하듯 펴고 누른다.
                if not pg.is_visible("#ke-play"):
                    pg.click("#ke-adv-toggle")
                pg.click("#ke-play")

            def why() -> str:
                return pg.evaluate("document.getElementById('ke-skipcal-why')?.textContent || ''")

            # ---------- 거절해야 하는 경우들 ----------
            land(CAL)
            setup("08-21")
            check("조회 페이지가 아닙니다" in why(),
                  "조회 모드인데 달력에 서 있으면 그렇다고 말한다", why())

            land(DEP)
            pg.wait_for_timeout(400)
            setup("")
            check("목표 날짜" in why(), "목표 날짜가 없으면 건너뛰지 않는다", why())

            setup("08-23")
            check("달력에서 08-23 선택" in why(),
                  "날짜가 다르면 '맞추고 시작' 이라고 알려준다", why())

            # ---------- 되는 경우 ----------
            setup("08-21")
            check("준비됨" in why() and "08-21" in why(),
                  "보고 있는 날 = 목표면 준비됐다고 한다", why())

            before = pg.evaluate("window.__loads")
            pg.evaluate("() => KE_HUD.fire('테스트')")
            pg.wait_for_function(f"() => window.__loads > {before}", timeout=20000)
            pg.wait_for_function("() => !!window.KE_REC", timeout=20000)
            check("departure" in pg.url, "같은 화면에 그대로 있다 (달력으로 안 감)", pg.url)

            pg.wait_for_function("() => (window.__clicks||[]).includes('next')", timeout=20000)
            clicked = pg.evaluate("window.__clicks || []")
            check(clicked[:2] == ["seat", "next"],
                  f"조회 페이지 단계(3·4번)부터 눌렀다 (실제 {clicked})")
            check(pg.evaluate("KE_REC.state.idx") == 4, "끝까지 갔다")

            # ---------- 좌석이 아직 없어도 날짜를 안다 ----------
            # 09:00 직전에 목표 날짜로 맞춰두고 기다리는 상황. 그날 좌석이 아직
            # 없으므로 서버 응답에는 날짜가 없다. 검색 위젯만 보고 알아내야 한다.
            land(DEP + "?depDate=20270822&empty=1")
            pg.wait_for_timeout(400)
            setup("08-22")
            check(pg.evaluate("KE_UTIL.searchedDate()") == "08-22",
                  "좌석이 없어도 검색 위젯에서 조회 날짜를 읽는다",
                  str(pg.evaluate("KE_UTIL.searchedDate()")))
            check("준비됨" in why(),
                  "좌석이 아직 없어도 '이 날짜로 대기 중' 이면 준비된 것이다", why())

            # ---------- 손으로 ▶ 재생 을 눌러도 같은 곳에서 시작한다 ----------
            # 실측(2026-08-27): 조회 화면 모드로 해놓고 ▶ 재생 을 눌렀더니 1단계
            # (달력 날짜 클릭)부터 돌아, 조회 화면에 있지도 않은 dep-fare- 셀을
            # 20초 동안 찾다 멈췄다. 사람이 시험해보는 가장 자연스러운 경로가 그것이다.
            land(DEP + "?depDate=20270821", fresh_loads=True)
            setup("08-21")
            pg.evaluate("() => { KE_REC.state.stepTimeoutMs = 3000; KE_REC.save(); }")
            press_play()
            pg.wait_for_function("() => (window.__clicks||[]).includes('next')", timeout=20000)
            clicked = pg.evaluate("window.__clicks || []")
            check(clicked[:2] == ["seat", "next"],
                  f"▶ 재생 도 3단계부터 시작한다 (실제 {clicked})")
            check(pg.evaluate("KE_REC.state.problem") is False,
                  "1단계를 찾다 멈추지 않는다",
                  str(pg.evaluate("KE_REC.state.message")))

            # 조건이 안 맞으면 헛돌리지 말고 이유를 말한다
            land(CAL)
            pg.evaluate("() => { KE_HUD.state.startAt = 'departure'; KE_HUD.save();"
                        " KE_REC.state.idx = 0; KE_REC.save(); }")
            press_play()
            pg.wait_for_timeout(600)
            check(pg.evaluate("KE_REC.state.playing") is False,
                  "조회 화면이 아니면 ▶ 재생 이 헛돌지 않는다")
            toast = pg.evaluate("document.getElementById('ke-toast')?.textContent || ''")
            check("조회 화면에서 시작할 수 없습니다" in toast, "왜 안 되는지 말해준다", toast)

            # ---------- 09:00 정각: 좌석이 조금 늦게 열린다 ----------
            # 정각에 새로고침해도 서버가 좌석을 몇 백 밀리초 늦게 푸는 경우가 있다.
            # 여기서 20초를 기다리다 포기하면 그날 좌석은 그대로 날아간다.
            # 달력에서 목표 날짜를 기다릴 때처럼 다시 불러와서 봐야 한다.
            land(DEP + "?depDate=20270822&opensAfter=3", fresh_loads=True)
            setup("08-22")
            pg.evaluate("() => { KE_REC.state.stepTimeoutMs = 4000; KE_REC.save(); }")
            check(pg.evaluate("!document.getElementById('seat')"),
                  "첫 화면에는 좌석이 아직 없다 (상황 재현)")

            pg.evaluate("() => KE_HUD.fire('정각 테스트')")
            pg.wait_for_function("() => (window.__clicks||[]).includes('next')", timeout=30000)
            loads = pg.evaluate("window.__loads")
            check(loads >= 3, f"좌석이 나올 때까지 다시 불러왔다 (로드 {loads}회)")
            check("departure" in pg.url, "그 사이에도 달력으로 새지 않는다", pg.url)
            check(pg.evaluate("KE_REC.state.problem") is False,
                  "포기하지 않고 끝까지 갔다",
                  str(pg.evaluate("KE_REC.state.message")))

            # ---------- 끝내 안 나오면 사람을 부른다 ----------
            land(DEP + "?depDate=20270822&opensAfter=999", fresh_loads=True)
            setup("08-22")
            pg.evaluate("""() => {
              KE_REC.state.openWaitMaxMs = 2500;   // 실제로는 180초
              KE_REC.state.openWaitSince = 0;
              KE_REC.save();
            }""")
            pg.evaluate("() => KE_HUD.fire('안 열리는 경우')")
            pg.wait_for_function("() => window.KE_REC && !window.KE_REC.state.playing"
                                 " && window.KE_REC.state.problem", timeout=40000)
            msg = pg.evaluate("KE_REC.state.message")
            check("안 나왔습니다" in (msg or ""),
                  "무한히 새로고침하지 않고 사람을 부른다", msg)
            print(f"      {msg}")
            pg.evaluate("() => { KE_REC.state.openWaitMaxMs = 180000; KE_REC.save(); }")

            # ---------- 프레스티지 매진: 180초 헛돌지 말고 바로 멈춘다 ----------
            # 실전(2026-09-01 CDG): 좌석 등급 화면까지 갔는데 프레스티지가 매진이라,
            # 예전엔 '아직 안 열렸나' 하고 180초를 새로고침만 돌았다. 이제 서버가
            # soldout:true 로 말하면 짧게(soldOutGraceMs) 지켜보고 즉시 멈춘다.
            land(DEP + "?depDate=20270822&soldout=1", fresh_loads=True)
            setup("08-22")
            pg.evaluate("() => { KE_REC.state.soldOutGraceMs = 1200;"
                        " KE_REC.state.openWaitMaxMs = 60000; KE_REC.save(); }")
            check(pg.evaluate("!document.getElementById('seat')"),
                  "매진이라 누를 좌석 버튼이 없다 (상황 재현)")
            pg.evaluate("() => KE_HUD.fire('매진 테스트')")
            pg.wait_for_function(
                "() => window.KE_REC && !window.KE_REC.state.playing"
                " && /매진/.test(window.KE_REC.state.message || '')", timeout=30000)
            msg = pg.evaluate("KE_REC.state.message")
            check("매진" in (msg or ""), "프레스티지 매진이면 '매진' 이라고 멈춘다", msg)
            check("일반석 9석" in (msg or ""), "남은 일반석 수를 함께 알려준다", msg)
            check("next" not in (pg.evaluate("window.__clicks || []")),
                  "매진이면 다음 단계로 넘어가지 않는다 (오클릭 없음)",
                  str(pg.evaluate("window.__clicks || []")))
            check(pg.evaluate("window.__loads") < 40,
                  "180초 헛돌지 않고 몇 번 만에 멈춘다 (재고침 폭주 없음)",
                  str(pg.evaluate("window.__loads")))
            print(f"      {msg}")

            # ---------- 날짜 띠에서 목표 날짜 찾기 ----------
            # 실측(2026-08-28): #flexible-date > li > button.flexible-date__link,
            # 라벨 "출발일 21 (토) 선택 가능". 라벨에 월이 없어 일자만 맞춰 본다.
            # 위쪽 위젯(날짜칸 → 달력 → [항공편 검색])은 복불복이라 버렸다 -
            # 달력 페이지로 되돌아갈 때가 있는데 그게 건너뛰려던 그 페이지다.
            land(DEP + "?depDate=20270818&openTo=21", fresh_loads=True)

            got = pg.evaluate("() => { var r = KE_UTIL.findStripDate('08-21');"
                              " return r && {found: !!r.el, sel: r.selectable, lab: r.label}; }")
            check(got and got["found"] and got["sel"] is True,
                  "열려 있는 날짜를 띠에서 집는다", str(got))

            got = pg.evaluate("() => { var r = KE_UTIL.findStripDate('08-22');"
                              " return r && {found: !!r.el, sel: r.selectable, why: r.why}; }")
            check(got and got["found"] and got["sel"] is False
                  and "운항편 없음" in (got["why"] or ""),
                  "아직 안 열린 날짜는 화면 문구 그대로 못 고른다고 한다", str(got))

            got = pg.evaluate("() => KE_UTIL.findStripDate('12-25')")
            check(got and not got["el"] and "다른 달" in (got["why"] or ""),
                  "다른 달의 같은 일자를 집지 않는다 (띠 라벨에는 월이 없다)", str(got))

            # ---------- 09:00 상황: 새 날짜를 화면 달력에서 골라 맞춘다 ----------
            # 새로 열리는 날짜는 09:00 에야 예약 가능 창에 들어오므로 미리 맞춰둘 수
            # 없다. 08-21 을 보고 있는 화면에서 08-22 로 바꿔 들어가야 한다.
            land(DEP + "?depDate=20270821&openTo=22", fresh_loads=True)
            setup("08-22")
            check("달력에서 08-22 선택" in why(), "무엇을 할지 미리 보여준다", why())
            # 패널 안내와 실제 발사가 어긋나면 안 된다. 실제로 어긋난 적이 있다 -
            # 안내는 "08-22 선택" 인데 발사는 날짜를 안 바꾸고 어제 좌석을 눌렀다.
            plan = pg.evaluate("() => KE_HUD.startPlan()")
            check(plan.get("fix") == "08-22",
                  "발사 계획에도 그 날짜가 실려 있다 (안내만 그럴듯하면 안 된다)", str(plan))

            pg.evaluate("() => KE_HUD.fire('날짜 맞추기 테스트')")

            pg.wait_for_function("() => (window.__clicks||[]).includes('next')", timeout=30000)
            check("depDate=20270822" in pg.url, "목표 날짜로 조회가 바뀌었다", pg.url)
            clicked = pg.evaluate("window.__clicks || []")
            check(clicked[-2:] == ["seat", "next"],
                  f"그 다음 좌석 단계로 이어졌다 (실제 {clicked})")
            check(pg.evaluate("KE_REC.state.problem") is False,
                  "문제 없이 끝났다", str(pg.evaluate("KE_REC.state.message")))
            check(pg.evaluate("KE_REC.state.fixDate") == "",
                  "맞추기가 끝나면 표시가 지워진다")

            # ---------- 서버가 목표 날짜를 말하지 않으면 좌석을 누르지 않는다 ----------
            # 화면만 보고 넘어가면 엉뚱한 날 마일리지가 빠진다. 응답이 근거다.
            land(DEP + "?depDate=20270821&openTo=21", fresh_loads=True)
            setup("08-22")          # 아직 안 열린 날 - 달력에서 고를 수 없다
            pg.evaluate("""() => {
              KE_REC.state.openWaitMaxMs = 3000;   // 실제로는 180초
              KE_REC.save();
            }""")
            pg.evaluate("() => KE_HUD.fire('안 열린 날')")
            pg.wait_for_function("() => window.KE_REC && !window.KE_REC.state.playing"
                                 " && window.KE_REC.state.problem", timeout=40000)
            msg = pg.evaluate("KE_REC.state.message")
            check("바꾸지 못했습니다" in (msg or ""),
                  "못 맞추면 좌석을 누르지 않고 멈춘다", msg)
            check("seat" not in (pg.evaluate("window.__clicks") or []),
                  "엉뚱한 날 좌석을 누르지 않았다",
                  str(pg.evaluate("window.__clicks")))
            print(f"      {msg}")
            pg.evaluate("() => { KE_REC.state.openWaitMaxMs = 180000; KE_REC.save(); }")

            # ---------- 새로고침 직후 첫 클릭이 먹혀도 결국 누른다 ----------
            # ▶ 재생 은 이미 안정된 화면에서 누르지만 ▶ 대기 시작 은 새로고침 직후에
            # 누른다. 그때는 페이지가 아직 클릭 핸들러를 안 붙여서 첫 클릭이 사라진다.
            # 한 번만 누르고 기다리면 영원히 안 넘어간다 - 실측에서 그랬다.
            land(DEP + "?depDate=20270818&openTo=21&deaf=2500", fresh_loads=True)
            setup("08-21")
            pg.evaluate("() => KE_HUD.fire('첫 클릭이 먹히는 경우')")
            pg.wait_for_function("() => (window.__clicks||[]).includes('next')", timeout=30000)
            check("depDate=20270821" in pg.url,
                  "첫 클릭이 먹혀도 다시 눌러서 결국 바꾼다", pg.url)

            # ---------- 결과가 늦게 그려질 때 새로고침하면 안 된다 ----------
            # 실측(2026-08-28): 조회 화면이 무한 새로고침만 했다. 페이지가 뜨지도
            # 않았는데 "고른 등급이 없다" 로 읽고 새로고침해서, 뜰 틈이 없었다.
            # 달력에서 이미 겪은 사고인데 좌석 쪽은 안 고쳐져 있었다.
            land(DEP + "?depDate=20270821&openTo=21&drawAfter=2000", fresh_loads=True)
            setup("08-21")
            check(pg.evaluate("() => KE_UTIL.cabinListReady()") is False,
                  "운임 카드가 아직 없으면 '목록 안 그려짐' 으로 본다")
            pg.evaluate("() => KE_HUD.fire('결과가 늦게 그려짐')")
            pg.wait_for_function("() => (window.__clicks||[]).includes('next')", timeout=30000)
            loads = pg.evaluate("window.__loads")
            # 발사 자체가 새로고침 한 번(1 -> 2)이다. 그 뒤로 더 부르면 안 된다 -
            # 고치기 전에는 1.2초마다 계속 불러 페이지가 뜰 틈이 없었다.
            check(loads <= 2,
                  f"결과가 늦게 그려져도 그 사이에 다시 부르지 않는다 (로드 {loads}회)")
            check(pg.evaluate("KE_REC.state.problem") is False, "문제 없이 끝났다")

            # ---------- 재생 / 연습 / 대기 시작이 똑같이 동작해야 한다 ----------
            # 셋은 시작하는 방식이 다르다(그 자리에서 / 10초 뒤 / 정시). 하지만
            # "무엇을 누르는가" 는 같아야 한다. 실측에서 ▶ 재생 만 되고 ▶ 대기 시작 은
            # 안 되는 일이 있었고, 그건 코드가 두 갈래였기 때문이다.
            def run_via(how: str) -> list:
                land(DEP + "?depDate=20270818&openTo=21", fresh_loads=True)
                setup("08-21")
                if how == "play":
                    press_play()
                elif how == "rehearse":
                    pg.evaluate("() => KE_HUD.rehearse(1)")
                else:
                    pg.evaluate("() => KE_HUD.fire('대기 시작')")
                pg.wait_for_function("() => (window.__clicks||[]).includes('next')",
                                     timeout=30000)
                return [pg.url, pg.evaluate("window.__clicks || []")]

            base = run_via("play")
            for how, name in (("rehearse", "연습"), ("arm", "대기 시작")):
                got = run_via(how)
                check(got[0].split("?")[1].split("&")[0] == base[0].split("?")[1].split("&")[0]
                      and got[1] == base[1],
                      f"{name} 이 ▶ 재생 과 같은 결과를 낸다",
                      f"재생={base} / {name}={got}")
            check("depDate=20270821" in base[0], "셋 다 목표 날짜로 바꿔서 진행한다", base[0])
            print(f"      셋 다: {base[1]} @ {base[0].split('?')[1]}")

            # ---------- 꺼두면 원래대로 ----------
            land(DEP)
            pg.wait_for_timeout(400)
            setup("08-21", skip=False)
            check("달력 화면이 아닙니다" in why(),
                  "달력 모드로 되돌리면 '여기는 달력이 아니다' 라고 말한다", why())
            land(CAL)
            setup("08-21", skip=False)
            check("준비됨" in why() and "1단계" in why(),
                  "달력 모드로 달력에 서 있으면 준비됨", why())
        finally:
            ctx.close()

    print()
    print("FAILED: " + ", ".join(fails) if fails else "달력 건너뛰기 테스트 통과")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
