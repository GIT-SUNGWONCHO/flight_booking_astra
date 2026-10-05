"""유저스크립트(HUD) 경로 통합 테스트.

HUD 가 담당하는 것은 "정시가 되면 재생을 걸고 새로고침한다" 하나뿐이다:
  연습 발사 예약 -> 카운트다운 -> T-0 에 발사 -> 새로고침 -> recorder 가 재생
  -> autoconfirm 이 모달 체인 통과 -> 재생이 멈추면 소리/제목으로 알림

실행:  .venv/bin/python test/test_hud.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ke_award.runner import launch_with_retry  # noqa: E402

USERSCRIPT = ROOT / "userscript" / "ke-award-macro.user.js"
FIXTURE = (ROOT / "test" / "fixture" / "gauntlet.html").as_uri()
EXPECTED = ["확인", "다음 »", "전체 동의", "계속하기"]

fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {label}{('  <- ' + detail) if detail and not ok else ''}")
    if not ok:
        fails.append(label)


def main() -> int:
    from playwright.sync_api import sync_playwright

    js = USERSCRIPT.read_text(encoding="utf-8")
    profile = ROOT / ".test-profile-hud"

    with sync_playwright() as p:
        ctx = launch_with_retry(p, user_data_dir=str(profile), headless=True)
        errors: list[str] = []
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.on("pageerror", lambda e: errors.append(str(e)))
            pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            ctx.add_init_script(js)
            pg.goto(FIXTURE)
            pg.wait_for_timeout(600)

            check(pg.evaluate("typeof window.KE_HUD") == "object", "HUD 로드")
            check(pg.evaluate("!!document.getElementById('ke-hud')"), "패널 렌더")

            # 남아 있어야 하는 컨트롤
            for el, name in (("ke-sync", "시각 동기"),
                             ("ke-rec", "녹화"), ("ke-play", "재생"), ("ke-edit", "단계 편집"),
                             ("ke-export", "내보내기"), ("ke-rehearse", "연습 발사"),
                             ("ke-arm", "대기 시작")):
                check(pg.evaluate(f"!!document.getElementById('{el}')"), f"{name} 버튼 존재")

            # 평소에 안 쓰는 도구는 접혀 있다. 요소는 그대로 있고 펴면 보인다(2026-10-04).
            basic = ("ke-target", "ke-cabin", "ke-expect", "ke-allowpay", "ke-rehearse", "ke-arm")
            advanced = ("ke-lead", "ke-sync", "ke-startat", "ke-probe-dump", "ke-rec", "ke-play",
                        "ke-clear", "ke-edit", "ke-export")
            check(all(pg.is_visible("#" + e) for e in basic), "기본 컨트롤은 바로 보인다")
            check(not any(pg.is_visible("#" + e) for e in advanced), "고급 도구는 접혀 있다")
            pg.click("#ke-adv-toggle")
            check(all(pg.is_visible("#" + e) for e in advanced), "펴면 고급 도구가 모두 보인다")
            check(pg.evaluate("window.KE_HUD.state.adv") is True, "편 상태를 기억한다")
            pg.click("#ke-adv-toggle")
            check(not pg.is_visible("#ke-rec"), "다시 누르면 접힌다")

            # 걷어낸 컨트롤이 되살아나지 않았는지 (죽은 경로 재도입 방지)
            for el, name in (("ke-pick", "조회 지정"), ("ke-pick-seat", "좌석 지정"),
                             ("ke-test", "발사 테스트"), ("ke-scan", "버튼 확인"),
                             ("ke-mode", "동작 전환"), ("ke-retry", "재조회 입력")):
                check(not pg.evaluate(f"!!document.getElementById('{el}')"), f"{name} 제거됨")


            # 단계가 하나도 없으면 발사하지 않고 안내만 한다.
            # clear() 는 이제 내장본을 복원하므로(그게 의도) 빈 상태는 직접 만든다.
            pg.evaluate("""() => {
              window.KE_REC.state.steps = [];
              window.KE_HUD.rehearse(2);
            }""")
            check(pg.evaluate("window.KE_HUD.state.armed") is False,
                  "단계가 없으면 무장하지 않음")

            # clear() 는 비우는 게 아니라 내장본으로 되돌려야 한다
            pg.evaluate("window.KE_REC.clear()")
            st = pg.evaluate("() => ({n: KE_REC.state.steps.length, src: KE_REC.state.source})")
            check(st["n"] == pg.evaluate("KE_REC.bakedCount()") and st["src"] == "baked",
                  f"삭제는 내장본을 복원 (실제 {st})")

            # armForReload 는 "예약"만 해야 한다. 여기서 바로 재생이 돌면 낡은 화면에서
            # 1단계(날짜)를 눌러버리고, 이어지는 새로고침이 그 선택을 날린다.
            pg.evaluate("""() => {
              window.__seatClicked = 0;
              window.KE_REC.state.steps = [
                {sel: '#seat', text: '프레스티지 PR 1석 · KE927 ICN→FCO', tag: 'button',
                 url: location.pathname, selectorOnly: false}
              ];
              window.KE_REC.save();
              window.KE_REC.armForReload();
            }""")
            pg.wait_for_timeout(700)
            arm = pg.evaluate("() => ({playing: KE_REC.state.playing,"
                              " pending: KE_REC.state.playAfterReload,"
                              " seat: !!window.__seatClicked})")
            check(arm["pending"] is True, "새로고침 후 재생이 예약됨")
            check(arm["playing"] is False, "예약 시점에는 아직 재생하지 않음")
            check(arm["seat"] is False, "새로고침 전에 1단계를 눌러버리지 않음")

            # 연습 발사: 2초 뒤 -> 새로고침 -> 좌석 클릭 재생 -> 모달 체인
            pg.evaluate("""() => {
              window.KE_REC.reset();
              window.KE_REC.save();
              window.KE_HUD.rehearse(2);
            }""")
            check(pg.evaluate("window.KE_HUD.state.armed") is True, "녹화가 있으면 무장함")

            t0 = time.time()
            # playing=true 는 재생이 순식간에 끝나면 놓칠 수 있다. 새로고침 뒤 실제로
            # 좌석이 눌린 흔적(픽스처가 남김)을 발사 시점의 관측 지점으로 쓴다.
            pg.wait_for_function("() => !!window.__seatClicked", timeout=15000)
            elapsed = time.time() - t0
            # 의미 있는 쪽은 하한이다: 초 단위 절삭 때문에 T-0 보다 일찍 쏘는 회귀를 잡는다.
            check(elapsed > 1.9, f"T-0 이전에 미리 쏘지 않음 (실제 {elapsed:.2f}s)")

            # 재생이 끝나면 자동클릭은 꺼진 채로 둔다. 그래서 이 픽스처의 모달 체인은
            # 재생 종료 후 아무도 안 누른다 - 의도된 동작이다(결제 직후 뜬 팝업을
            # 사람이 보기 전에 치우지 않기 위함). 여기서는 재생 자체를 검증한다.
            pg.wait_for_function("() => !window.KE_REC.state.playing", timeout=20000)
            st = pg.evaluate("() => ({idx: KE_REC.state.idx, total: KE_REC.state.steps.length,"
                             " problem: KE_REC.state.problem,"
                             " aborted: window.__aborted || null,"
                             " armed: window.KE_HUD.state.armed})")
            check(st["idx"] == st["total"], f"녹화 단계를 끝까지 재생 ({st['idx']}/{st['total']})")
            check(st["problem"] is False, f"문제 없이 완료 (problem={st['problem']})")
            check(st["aborted"] is None, "오클릭 없음", f"눌림: {st['aborted']}")
            check(st["armed"] is False, "재생으로 넘어가며 HUD 무장은 해제됨")

            pg.wait_for_timeout(300)
            pg.wait_for_timeout(300)
            # 끝까지 간 경우와 막힌 경우를 제목으로 구분해야 한다
            # (같게 알리면 막혀서 멈춘 걸 완료로 오해한다)
            check(pg.title().startswith("★완료★"),
                  "끝까지 갔으면 '완료' 로 알림", f"title={pg.title()!r}")

            pg.evaluate("""() => {
              const R = window.KE_REC;
              R.state.steps = [{sel: '#nope-does-not-exist', text: '있을 리 없는 버튼',
                                tag: 'button', url: location.pathname, selectorOnly: true}];
              R.state.stepTimeoutMs = 600;
              R.reset(); R.save(); R.play();
            }""")
            pg.wait_for_function("() => !window.KE_REC.state.playing", timeout=8000)
            pg.wait_for_timeout(300)
            check(pg.title().startswith("⚠멈춤⚠"),
                  "막혀서 멈추면 '멈춤' 으로 다르게 알림", f"title={pg.title()!r}")

            check(not errors, "콘솔 에러 없음", str(errors[:2]))
        finally:
            ctx.close()
        # API 예매·계측 창에는 매크로 엔진과 패널을 싣지 않는다. 로그인 준비가 쓰는 KE_UTIL 은 남는다.
        # (위의 컨텍스트는 표시 없는 스크립트를 이미 걸어 두었으므로 새 브라우저에서 본다.)
        browser = p.chromium.launch(headless=True)
        try:
            api = browser.new_page()
            api.add_init_script("window.KE_API_MODE = true;" + chr(10) + js)
            api.goto(FIXTURE)
            api.wait_for_timeout(1500)        # 패널은 1초마다 다시 붙으려 한다 - 그 뒤에도 없어야 한다
            got = api.evaluate("() => ({util: typeof window.KE_UTIL, rec: typeof window.KE_REC,"
                               " hud: typeof window.KE_HUD, edit: typeof window.KE_EDIT,"
                               " panel: !!document.getElementById('ke-hud')})")
            check(got["util"] == "object", "API 모드: KE_UTIL 은 실린다", str(got))
            check((got["rec"], got["hud"], got["edit"]) == ("undefined",) * 3,
                  "API 모드: 재생 엔진·패널·편집기는 실리지 않는다", str(got))
            check(got["panel"] is False, "API 모드: 패널이 화면에 없다")
        finally:
            browser.close()

    print()
    print("FAILED: " + ", ".join(fails) if fails else "HUD 테스트 통과")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
