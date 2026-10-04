"""목표 날짜가 아직 안 열렸으면 멈추지 말고 새로고침하며 기다린다.

사용자 요청(2026-08-27): "내가 정한 목표날이 화면에 없으면 뭔가 잘못된거니까
새로고침이든 계속 시도해야함". 예전에는 그 자리에서 멈췄는데, 09:00 경쟁에서는
최악이다 - 좌석이 열리는 그 순간을 놓친다.

실행:  .venv/Scripts/python.exe test/test_openwait.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ke_award.runner import launch_with_retry  # noqa: E402

USERSCRIPT = ROOT / "userscript" / "ke-award-macro.user.js"
FIXTURE = (ROOT / "test" / "fixture" / "openwait.html").as_uri()

fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {label}{('  <- ' + detail) if detail and not ok else ''}")
    if not ok:
        fails.append(label)


STEPS = """(expect) => {
  const R = window.KE_REC;
  R.state.steps = [
    {dynamicDate:true, idPrefix:'dep-fare-', sel:'', text:'(날짜)', tag:'td', url:'/openwait.html'},
    {sel:'#next', text:'다음', tag:'button', url:'/openwait.html'}
  ];
  R.state.expectDate = expect;
  R.state.idx = 0; R.state.startedAt = 0; R.state.problem = false;
  R.state.openWaitSince = 0;
  R.state.openRetryMs = 300;
  R.state.openWaitMaxMs = 15000;
  R.state.stepTimeoutMs = 8000;
  R.play();
}"""


def main() -> int:
    from playwright.sync_api import sync_playwright

    js = USERSCRIPT.read_text(encoding="utf-8")

    with sync_playwright() as p:
        ctx = launch_with_retry(p, user_data_dir=str(ROOT / ".test-profile-ow"), headless=True)
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.set_default_timeout(30000)
            ctx.add_init_script(js)

            # 21일은 세 번째 로드부터 열린다 - 그때까지 새로고침하며 기다려야 한다
            pg.goto(FIXTURE, timeout=60000)
            pg.evaluate("sessionStorage.setItem('loads','0')")
            pg.reload()
            pg.wait_for_timeout(300)
            pg.evaluate(STEPS, "08-21")
            pg.wait_for_function("() => window.KE_REC && !KE_REC.state.playing && !KE_REC.state.playAfterReload", timeout=40000)
            st = pg.evaluate("""() => ({loads: window.__loads, picked: window.__picked || null,
              next: !!window.__next, idx: KE_REC.state.idx, total: KE_REC.state.steps.length,
              problem: KE_REC.state.problem, msg: KE_REC.state.message})""")
            check(st["loads"] >= 3, f"열릴 때까지 새로고침함 (로드 {st['loads']}회)")
            check(st["picked"] is not None and "21" in st["picked"],
                  f"21일을 골랐다 (실제 {st['picked']})")
            check(st["next"], "그 뒤 단계까지 진행")
            check(st["idx"] == st["total"] and not st["problem"],
                  f"끝까지 재생 ({st['idx']}/{st['total']}, problem={st['problem']})", st["msg"])
            print(f"      메시지: {st['msg']}")

            # 영영 안 열리는 날짜면 무한 새로고침하지 않고 사람을 부른다
            pg.goto(FIXTURE, timeout=60000)
            pg.evaluate("sessionStorage.setItem('loads','5')")
            pg.wait_for_timeout(300)
            pg.evaluate(STEPS.replace("openWaitMaxMs = 15000", "openWaitMaxMs = 2500"), "12-25")
            pg.wait_for_function("() => window.KE_REC && !KE_REC.state.playing && !KE_REC.state.playAfterReload", timeout=40000)
            st2 = pg.evaluate("() => ({problem: KE_REC.state.problem, msg: KE_REC.state.message})")
            check(st2["problem"] is True, "안 열리면 문제로 표시하고 멈춤")
            check("안 열렸습니다" in (st2["msg"] or ""), "왜 멈췄는지 알림", st2["msg"])
        finally:
            ctx.close()

    print()
    print("FAILED: " + ", ".join(fails) if fails else "목표 날짜 대기 테스트 통과")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
