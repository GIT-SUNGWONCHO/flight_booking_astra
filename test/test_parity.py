"""▶ 재생 / 연습 / ▶ 대기 시작 이 같은 상태에서 출발하는가.

같은 사고가 세 번 났다. ▶ 재생 은 되는데 ▶ 대기 시작 만 안 되는 것이다:

  1) 조회 화면 모드에서 ▶ 재생 만 시작 단계를 맞췄다 (startPlan 이 fire 안에만 있었다)
  2) startPlan 이 계획을 다시 만들며 fix(맞출 날짜)를 떨어뜨렸다
  3) armForReload 가 openWaitSince 를 안 지워서, 지난 실행 값이 남아 시작하자마자
     "180초 동안 안 열렸습니다" 로 끝났다 (실제로는 0.64초)

셋 다 원인이 같다: 두 경로가 각자 상태를 지우고 각자 계획을 세웠다.

그래서 이 테스트는 "지금 되는가" 만 보지 않는다. **한쪽만 지우는 상태가 새로
생기면 잡는다** - 상태를 전부 더럽혀 놓고 두 경로를 각각 태운 뒤, 한쪽이 지운
것을 다른 쪽도 지웠는지 키 단위로 대조한다.

실행:  .venv/Scripts/python.exe test/test_parity.py
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ke_award.runner import launch_with_retry  # noqa: E402

USERSCRIPT = ROOT / "userscript" / "ke-award-macro.user.js"
FIXTURE = (ROOT / "test" / "fixture" / "calendar.html").as_uri()
SLOWCAL = (ROOT / "test" / "fixture" / "slowcal.html").as_uri()

fails: list[str] = []

# 두 경로가 달라도 되는 것들. 시작 방식 자체가 다르니 당연히 다르다.
ALLOWED = {
    "playing",          # 재생은 바로 시작, 대기 시작은 새로고침 뒤에 시작
    "playAfterReload",  # 대기 시작만 예약을 남긴다
    "playFrom", "idx", "message", "fixDate",
    "startedAt", "stepStartedAt",   # 시각 자체는 매번 다르다
    "recordedWidth",
}

# 실행을 시작할 때 반드시 지워져야 하는 것들. 하나라도 남으면 지난 실행이
# 이번 실행을 망친다 - 3)번 사고가 정확히 그것이었다.
MUST_CLEAR = {
    "openWaitSince": 1,
    "endedAt": 1,
    "problem": True,
    "fixSince": 1,
    "fixPhase": 2,
    "fixClickAt": 1,
    "fixOpens": 9,
    "navigation": [{"at": 1, "path": "/previous-run", "step": 9}],
}


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {label}{('  <- ' + detail) if detail and not ok else ''}")
    if not ok:
        fails.append(label)


POISON = """() => {
  const S = window.KE_REC.state;
  /* 실제 1단계와 같은 모양: 고정 셀렉터가 아니라 그날 화면에서 날짜로 찾는다. */
  S.steps = [{sel:'#dep-fare-3-4', text:'22 08월 22일 (일)', tag:'td', url:location.pathname,
              dynamicDate:true}];
  S.playing = false; S.playAfterReload = false; S.idx = 0;
  Object.assign(S, %s);
  S.times = [{n: 9, label: '지난 실행', ms: 12345}];
  window.KE_REC.save();
  return JSON.parse(JSON.stringify(S));
}"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    js = USERSCRIPT.read_text(encoding="utf-8")
    profile = ROOT / ".test-profile-parity"
    shutil.rmtree(profile, ignore_errors=True)
    import json

    with sync_playwright() as p:
        ctx = launch_with_retry(p, user_data_dir=str(profile), headless=True)
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.set_default_timeout(20000)
            ctx.add_init_script(js)
            pg.goto(FIXTURE)
            pg.wait_for_function("() => !!window.KE_REC", timeout=20000)

            poison_js = POISON % json.dumps(MUST_CLEAR)

            # 각 경로는 '자기가 출발한 상태' 와 비교해야 한다. 앞 경로가 재생되며
            # 남긴 흔적(예: 방금 고른 날짜)을 뒤 경로 탓으로 돌리면 안 된다.
            dirty_play = pg.evaluate(poison_js)
            after_play = pg.evaluate("() => { window.KE_REC.play();"
                                     " return JSON.parse(JSON.stringify(KE_REC.state)); }")
            pg.evaluate("() => KE_REC.pause('테스트')")

            dirty_arm = pg.evaluate(poison_js)
            after_arm = pg.evaluate("() => { window.KE_REC.armForReload(0);"
                                    " return JSON.parse(JSON.stringify(KE_REC.state)); }")

            # 1) 반드시 지워져야 하는 것들
            for k, bad in MUST_CLEAR.items():
                check(after_play.get(k) != bad, f"▶ 재생 이 {k} 를 지운다",
                      f"{k}={after_play.get(k)!r}")
                check(after_arm.get(k) != bad, f"▶ 대기 시작 이 {k} 를 지운다",
                      f"{k}={after_arm.get(k)!r}")
            check(after_play.get("times") == [] and after_arm.get("times") == [],
                  "둘 다 지난 실행의 단계별 시간을 지운다",
                  f"play={after_play.get('times')} arm={after_arm.get('times')}")

            # 2) 구조 검사: 한쪽이 지운 것은 다른 쪽도 지워야 한다.
            #    새 상태를 추가하고 한쪽만 지우면 여기서 걸린다.
            diverged = []
            for k in dirty_play:
                if k in ALLOWED:
                    continue
                changed_by_play = after_play.get(k) != dirty_play.get(k)
                changed_by_arm = after_arm.get(k) != dirty_arm.get(k)
                if changed_by_play != changed_by_arm:
                    diverged.append(
                        f"{k}: 재생 {dirty_play.get(k)!r}->{after_play.get(k)!r}"
                        f" / 대기시작 {dirty_arm.get(k)!r}->{after_arm.get(k)!r}")
            check(not diverged,
                  "두 경로가 지우는 상태가 같다 (한쪽만 지우면 여기서 걸린다)",
                  " / ".join(diverged))

            # 3) 실측 재현: 목표가 '가장 나중 날짜' 가 아니어도 대기 시작이 돌아야 한다.
            #    2026-08-28 사용자 화면: 목표 08-18, 달력 최신 08-22 -> 무한 새로고침.
            #    달력에 목표가 있으면 새로고침이 아니라 그 날을 눌러야 한다.
            pg.goto(FIXTURE)
            pg.wait_for_function("() => !!window.KE_HUD", timeout=20000)
            pg.evaluate(poison_js)
            pg.evaluate("""() => {
              window.__clicked = null;
              document.getElementById('dep-fare-3-2')      // 08-20: 최신일이 아니다
                .addEventListener('click', () => { window.__clicked = '20'; });
              /* 새로고침이 일어나면 이 리스너도 __clicked 도 사라진다.
                 그러니 '눌렸다' 는 것 자체가 무한 새로고침을 안 했다는 증거다. */
              KE_REC.state.expectDate = '08-20'; KE_REC.save();
              KE_REC.armForReload(0);
              KE_REC.state.playAfterReload = false;
              KE_REC.state.playing = true;        // 새로고침 뒤와 같은 상태
              KE_REC.save();
            }""")
            pg.wait_for_function("() => !!window.__clicked", timeout=15000)
            st = pg.evaluate("() => ({problem: KE_REC.state.problem,"
                             " msg: KE_REC.state.message, clicked: window.__clicked})")
            check(st["clicked"] == "20",
                  "목표가 최신일이 아니어도 그 날짜를 누른다 (무한 새로고침 안 함)", str(st))
            check(st["problem"] is False,
                  "지난 실행 찌꺼기가 있어도 시작하자마자 실패하지 않는다", str(st))
            print(f"      {st['msg']}")

            # 4) 실측 재현: 달력이 늦게 그려질 때 빈 화면인 채로 새로고침하면 안 된다.
            #    "아직 안 그려졌다" 와 "그려졌는데 그 날이 없다" 는 전혀 다른 상황이다.
            pg.goto(SLOWCAL + "?delay=1500&upto=22")
            pg.wait_for_function("() => !!window.KE_REC", timeout=20000)
            pg.evaluate("() => { sessionStorage.removeItem('loads'); }")
            pg.reload()
            pg.wait_for_function("() => !!window.KE_REC", timeout=20000)
            pg.evaluate("""() => {
              const S = KE_REC.state;
              S.steps = [{sel:'#dep-fare-22', text:'22 08월 22일 (수)', tag:'div',
                          url:location.pathname, dynamicDate:true}];
              S.expectDate = '08-18';      // 늦게 그려지고, 최신일도 아니다
              S.playing = false; S.playAfterReload = false;
              KE_REC.save();
              KE_REC.armForReload(0);
              S.playAfterReload = false; S.playing = true;   // 새로고침 뒤와 같은 상태
              KE_REC.save();
            }""")
            pg.wait_for_function("() => !!window.__clicked", timeout=20000)
            st = pg.evaluate("() => ({clicked: window.__clicked, loads: window.__loads,"
                             " problem: KE_REC.state.problem})")
            check(st["clicked"] == "18",
                  "늦게 그려져도 목표 날짜를 누른다", str(st))
            check(st["loads"] == 1,
                  f"달력이 뜨기도 전에 새로고침하지 않는다 (로드 {st['loads']}회)")
            check(st["problem"] is False, "문제 없이 끝났다", str(st))

            # 5) 달력은 그려졌는데 그 날이 정말 없으면 - 그때는 새로고침이 맞다
            pg.goto(SLOWCAL + "?delay=200&upto=20")
            pg.wait_for_function("() => !!window.KE_REC", timeout=20000)
            pg.evaluate("() => { sessionStorage.removeItem('loads'); }")
            pg.reload()
            pg.wait_for_function("() => !!window.KE_REC", timeout=20000)
            pg.evaluate("""() => {
              const S = KE_REC.state;
              S.steps = [{sel:'#dep-fare-20', text:'20 08월 20일 (수)', tag:'div',
                          url:location.pathname, dynamicDate:true}];
              S.expectDate = '08-22';      // upto=20 이라 달력에 없다
              S.openRetryMs = 400;
              S.playing = false; S.playAfterReload = false;
              KE_REC.save();
              KE_REC.armForReload(0);
              S.playAfterReload = false; S.playing = true;
              KE_REC.save();
            }""")
            pg.wait_for_function("() => (window.__loads || 0) >= 2", timeout=20000)
            check(True, "달력이 그려졌는데 그 날이 없으면 새로고침해서 다시 본다")
            pg.evaluate("() => { KE_REC.state.playing = false; KE_REC.save(); }")
        finally:
            ctx.close()

    print()
    print("FAILED: " + ", ".join(fails) if fails else "시작 경로 일치 테스트 통과")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
