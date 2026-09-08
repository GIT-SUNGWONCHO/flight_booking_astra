"""개발용: 원격 디버깅으로 띄운 Chrome 에 붙어서 유저스크립트를 주입하고 조작한다.

전제:  ./dev-browser.sh 로 Chrome 이 떠 있고, 그 창에서 한 번 로그인해둔 상태.
사용:  .venv/bin/python dev/drive.py <명령>
  state              현재 URL / 재생 상태 / 단계 진행도
  shot [파일명]      스크린샷
  inject             빌드된 유저스크립트를 현재 페이지에 주입
  eval "<JS>"        임의 JS 평가 결과 출력
  find "<라벨>"      그 라벨로 매칭되는 후보들의 태그/id/구조 덤프
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
USERSCRIPT = ROOT / "userscript" / "ke-award-macro.user.js"
SHOTS = ROOT / "dev-shots"


def get_page(pw):
    browser = pw.chromium.connect_over_cdp("http://localhost:9232")
    ctx = browser.contexts[0]
    # koreanair 탭을 우선 고른다
    for p in ctx.pages:
        if "koreanair" in p.url:
            return browser, p
    return browser, (ctx.pages[0] if ctx.pages else ctx.new_page())


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    cmd = sys.argv[1]
    arg = sys.argv[2] if len(sys.argv) > 2 else None

    with sync_playwright() as pw:
        browser, page = get_page(pw)

        if cmd == "state":
            print("url:", page.url)
            st = page.evaluate("""() => {
              const r = window.KE_REC, u = window.KE_UTIL;
              if (!r) return {injected: false};
              return {
                injected: true,
                steps: r.state.steps.length,
                idx: r.state.idx,
                playing: r.state.playing,
                message: r.state.message || '',
                latestDate: u && u.findLatestOpenDate ?
                  (u.findLatestOpenDate() ? u.label(u.findLatestOpenDate()) : null) : 'n/a',
              };
            }""")
            print(json.dumps(st, ensure_ascii=False, indent=2))

        elif cmd == "inject":
            # 각 모듈이 "이미 로드됐으면 return" 가드를 갖고 있어서, 코드를 고친 뒤에는
            # 리로드로 전역을 비우지 않으면 옛 버전이 그대로 남는다.
            src = USERSCRIPT.read_text(encoding="utf-8")
            if arg != "noreload":
                page.reload(wait_until="domcontentloaded")
                page.wait_for_timeout(2500)
            page.evaluate(src)
            print("injected. KE_REC:", page.evaluate("!!window.KE_REC"),
                  "url:", page.url)

        elif cmd == "shot":
            SHOTS.mkdir(exist_ok=True)
            out = SHOTS / (arg or "shot.png")
            page.screenshot(path=str(out))
            print("saved:", out)

        elif cmd == "eval":
            print(json.dumps(page.evaluate(f"() => ({arg})"), ensure_ascii=False, indent=2, default=str))

        elif cmd == "find":
            res = page.evaluate("""(text) => {
              const U = window.KE_UTIL;
              if (!U) return 'KE_UTIL 없음 - 먼저 inject';
              return U.candidates(document)
                .filter(el => U.label(el) === text)
                .map(el => ({
                  tag: el.tagName, id: el.id, cls: (el.className || '').toString().slice(0, 60),
                  visible: U.visible(el), inChrome: U.inChrome ? U.inChrome(el) : null,
                  rect: (r => ({x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}))(el.getBoundingClientRect()),
                  html: el.outerHTML.slice(0, 200),
                }));
            }""", arg)
            print(json.dumps(res, ensure_ascii=False, indent=2))

        elif cmd == "replay":
            allow_pay = page.evaluate("window.KE_REC ? window.KE_REC.state.allowPay : null")
            if allow_pay is None:
                print("중단: KE_REC 가 없습니다 - 먼저 inject")
                return 1
            if allow_pay:
                print("  ! allowPay=True - 결제하기까지 누릅니다 (결제창이 열림)")
            page.evaluate("() => { window.KE_REC.reset(); window.KE_REC.play(); }")
            secs = int(arg or 60)
            last = None
            for _ in range(secs * 2):
                page.wait_for_timeout(500)
                st = page.evaluate("""() => ({
                  idx: KE_REC.state.idx, n: KE_REC.state.steps.length,
                  playing: KE_REC.state.playing, msg: KE_REC.state.message || '',
                  url: location.pathname,
                })""")
                cur = (st["idx"], st["playing"], st["msg"])
                if cur != last:
                    print(f"  {st['idx']}/{st['n']}  playing={st['playing']}  {st['url']}  {st['msg'][:90]}")
                    last = cur
                if not st["playing"] and st["idx"] > 0:
                    break
            SHOTS.mkdir(exist_ok=True)
            page.screenshot(path=str(SHOTS / "replay_end.png"))
            print("최종 스크린샷:", SHOTS / "replay_end.png")

        else:
            print("모르는 명령:", cmd)
            return 1

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
