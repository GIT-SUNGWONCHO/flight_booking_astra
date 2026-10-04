"""내장 단계(steps.json) vs 브라우저 녹화 우선순위 테스트.

실제로 난 사고: 정리한 16단계를 커밋하고 새 스크립트를 붙여넣었는데, 브라우저에
남아있던 22단계 옛 녹화가 계속 이겨서 정리 전 순서(마일리지 적용 전에 결제하기)로
돌 뻔했다. 이제 내장본이 이기되, 같은 빌드 안에서 녹화한 건 보존되어야 한다.

실행:  .venv/Scripts/python.exe test/test_precedence.py
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ke_award.runner import launch_with_retry  # noqa: E402

FIXTURE = (ROOT / "test" / "fixture" / "booking.html").as_uri() + "?p=cal"

fails: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"{'ok  ' if ok else 'FAIL'}  {label}{('  <- ' + detail) if detail and not ok else ''}")
    if not ok:
        fails.append(label)


def build_with(steps: list[dict]) -> str:
    """단계를 바꿔 빌드한 스크립트를 돌려준다. 추적 파일(steps.json·빌드 결과)은 건드리지 않는다."""
    with tempfile.TemporaryDirectory() as tmp:
        sj = Path(tmp) / "steps.json"
        out = Path(tmp) / "ke-award-macro.user.js"
        sj.write_text(json.dumps({"note": "test", "steps": steps}, ensure_ascii=False), encoding="utf-8")
        env = {**os.environ, "KE_BUILD_STEPS": str(sj), "KE_BUILD_OUT": str(out)}
        subprocess.run(["node", "build.mjs"], cwd=ROOT, env=env, capture_output=True, check=True)
        return out.read_text(encoding="utf-8")


def step(sel: str, text: str) -> dict:
    return {"sel": sel, "text": text, "tag": "button", "url": "/x", "selectorOnly": False}


def main() -> int:
    from playwright.sync_api import sync_playwright

    v1 = build_with([step("#a", "하나"), step("#b", "둘")])
    v2 = build_with([step("#a", "하나"), step("#b", "둘"), step("#c", "셋")])

    with sync_playwright() as p:
        ctx = launch_with_retry(p, user_data_dir=str(ROOT / ".test-profile-prec"), headless=True)
        try:
            pg = ctx.pages[0] if ctx.pages else ctx.new_page()
            pg.goto(FIXTURE)

            def load(script: str) -> None:
                """새 유저스크립트를 붙여넣고 새로고침한 것과 같은 상태를 만든다."""
                pg.evaluate("() => { delete window.KE_REC; delete window.KE_STEPS_BAKED; }")
                pg.evaluate(script)

            # 1) 최초: 내장 2단계가 적용
            load(v1)
            st = pg.evaluate("() => ({n: KE_REC.state.steps.length, src: KE_REC.state.source})")
            check(st["n"] == 2 and st["src"] == "baked", f"최초 설치는 내장본 적용 (실제 {st})")

            # 2) 브라우저에서 직접 녹화 -> 같은 빌드에서는 보존되어야 한다
            pg.evaluate("""() => {
              KE_REC.record();
              KE_REC.state.steps = [{sel:'#z', text:'내가 녹화', tag:'button', url:'/x'}];
              KE_REC.stop();
            }""")
            load(v1)
            st = pg.evaluate("() => ({n: KE_REC.state.steps.length, t: KE_REC.state.steps[0].text,"
                             " src: KE_REC.state.source})")
            check(st["n"] == 1 and st["t"] == "내가 녹화" and st["src"] == "local",
                  f"같은 빌드에서는 직접 녹화가 보존됨 (실제 {st})")

            # 3) 새 빌드(steps.json 변경) -> 내장본이 옛 녹화를 덮는다  ★핵심
            load(v2)
            st = pg.evaluate("() => ({n: KE_REC.state.steps.length, src: KE_REC.state.source,"
                             " texts: KE_REC.state.steps.map(s => s.text)})")
            check(st["n"] == 3 and st["src"] == "baked",
                  f"새 빌드를 붙여넣으면 내장본이 옛 녹화를 대체 (실제 {st})")
            check("내가 녹화" not in st["texts"], "옛 녹화가 남아있지 않음", str(st["texts"]))

            # 4) 같은 새 빌드를 다시 로드해도 덮어쓰기가 반복되지 않는다
            pg.evaluate("KE_REC.setStep(0, {text: '손으로 고침'})")
            load(v2)
            st = pg.evaluate("() => ({t: KE_REC.state.steps[0].text, src: KE_REC.state.source})")
            check(st["t"] == "손으로 고침" and st["src"] == "local",
                  f"같은 빌드 재로드는 편집 내용을 지키됨 (실제 {st})", )

            # 5) 삭제는 비우지 않고 내장본으로 되돌린다
            pg.evaluate("KE_REC.clear()")
            st = pg.evaluate("() => ({n: KE_REC.state.steps.length, src: KE_REC.state.source})")
            check(st["n"] == 3 and st["src"] == "baked", f"삭제 -> 내장본 복원 (실제 {st})")
        finally:
            ctx.close()

    # ---- 오픈시각 입력 관대해졌는지 (실제로 테스트를 막았던 버그) ----
    hud = (ROOT / "ke_award" / "hud.js").read_text(encoding="utf-8")
    body = re.search(r"function targetMs\(\) \{(.+?)\n  \}", hud, re.S).group(1)
    js = f"""
      var S = {{targetKst: process.argv[2]}};
      function targetMs() {{{body}
      }}
      console.log(String(targetMs()));
    """
    (ROOT / ".tmp_target.js").write_text(js, encoding="utf-8")
    try:
        expect = None
        for raw in ["2026-08-24 09:00:00", "2026-08-24-09:00:00", "2026-08-24  09:00",
                    "2026/08/24 09:00:00", "2026-08-24T09:00:00"]:
            out = subprocess.run([("node"), str(ROOT / ".tmp_target.js"), raw],
                                 capture_output=True, text=True).stdout.strip()
            if expect is None:
                expect = out
            check(out == expect and out != "NaN", f"오픈시각 표기 허용: {raw!r} -> {out}")
    finally:
        (ROOT / ".tmp_target.js").unlink(missing_ok=True)

    print()
    print("FAILED: " + ", ".join(fails) if fails else "우선순위 테스트 통과")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
