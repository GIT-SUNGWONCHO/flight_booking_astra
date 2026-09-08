"""9시에 바로 쏠 수 있는 상태인지 확인한다. 저녁(무겁게)과 아침(가볍게) 두 번 쓴다.

왜 저녁에 하나
  08:45 에 "로그인이 풀렸습니다" 를 알아봐야 고칠 시간이 15분뿐이다. 그마저도
  사람이 자리에 없으면 그냥 하루를 잃는다(2026-09-03 이 그랬다).
  전날 저녁에 통째로 세워보면 밤새 고칠 시간이 있다.

  저녁 (--for-tomorrow)  내일 노선으로 로그인·달력까지 실제로 세워본다. 5~8분.
  아침 (--quick)         크롬이 떠 있나만 확인한다. 20초. 셋업은 측정이 스스로 한다.

--quick 이 로그인을 단정하지 않는 이유
  헤더가 shadow DOM 이라 갓 띄운 크롬에서는 로그인돼 있어도 false 로 읽힌다
  (09-03 실측: 두 프로필 다 로그인 상태인데 '안 됨' 으로 찍혔다). 그래서 아침에는
  '확인됨/확인 못 함' 까지만 말하고 막지 않는다. 진짜 판정은 저녁 점검이 한다.

사용:
  .venv/Scripts/python.exe dev/preflight.py --for-tomorrow --route ICN --from FCO
  .venv/Scripts/python.exe dev/preflight.py --quick
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
OUT = ROOT / "dev-shots"
KST = timezone(timedelta(hours=9))
OFFSET = 360
ROME_DAYS = {0, 2, 5}
WD = ['월', '화', '수', '목', '금', '토', '일']

# 로그인 표시를 찾는 말들. '로그아웃' 이 가장 확실하지만 헤더가 늦게 그려지므로
# 마이페이지/스카이패스도 같이 본다 (09-03 에 셋 다 같이 나타나는 것을 확인했다).
LOGIN_JS = """() => {
  const U = window.KE_UTIL;
  const c = U.candidates(document).filter(e => U.visible(e));
  const lab = c.map(e => U.label(e)).filter(Boolean);
  return {out: lab.some(t => /로그아웃/.test(t)),
          my:  lab.some(t => /마이페이지|스카이패스/.test(t)),
          inb: lab.some(t => /^로그인$/.test(t))};
}"""


def log(m):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {m}", flush=True)


def check_login(page, tries: int) -> bool:
    """헤더가 그려질 때까지 몇 번 본다. 한 번만 보면 로그인돼 있어도 놓친다."""
    page.evaluate(USER.read_text(encoding="utf-8"))
    for _ in range(tries):
        st = page.evaluate(LOGIN_JS)
        if st.get("out") or st.get("my"):
            return True
        page.wait_for_timeout(700)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="")
    ap.add_argument("--from", dest="origin", default="")
    ap.add_argument("--ports", default="9232,9233")
    ap.add_argument("--quick", action="store_true",
                    help="크롬이 떠 있나만 본다 (아침용, 20초). 셋업은 안 한다")
    ap.add_argument("--for-tomorrow", action="store_true",
                    help="내일 09:00 기준으로 계산한다 (저녁용)")
    a = ap.parse_args()

    base = datetime.now(KST).date() + timedelta(days=1 if a.for_tomorrow else 0)
    opens = base + timedelta(days=OFFSET)
    rome_ok = opens.weekday() in ROME_DAYS
    route = a.route.upper() if a.route else ("FCO" if rome_ok else "CDG")
    origin = a.origin.upper()
    # 9시에 열리는 날은 아직 못 고른다. 하루 전 날짜로 화면을 세워두면 그 언저리
    # 달력이 떠서, 9시에 열리자마자 매크로가 바로 찾는다.
    # (저녁에 돌면 이 날짜는 오늘 아침 09:00 에 열린 날이라 항상 고를 수 있다.)
    stand = opens - timedelta(days=1)
    when = "내일" if a.for_tomorrow else "오늘"

    if a.quick:
        log("아침 점검 (크롬이 살아 있나만) - 셋업은 측정이 스스로 한다")
    else:
        log(f"{when} {base}({WD[base.weekday()]}) 9시에 열리는 날: "
            f"{opens}({WD[opens.weekday()]})  로마운항={'O' if rome_ok else 'X'}")
        log(f"노선 {origin or 'SEL'} -> {route} / 화면은 {stand} 로 세워둔다")

    result = {"at": datetime.now(KST).isoformat(), "mode": "quick" if a.quick else "full",
              "for": str(base), "opens": str(opens), "route": route,
              "origin": origin or "SEL", "ports": {}}
    problems: list = []
    warns: list = []

    from playwright.sync_api import sync_playwright
    for port in [int(p) for p in a.ports.split(",")]:
        tag = "실전" if port == 9232 else "계측"
        info = {"chrome": False, "loggedIn": False, "ready": False, "why": ""}
        result["ports"][str(port)] = info

        # 1) 크롬이 떠 있나 + 로그인돼 있나
        try:
            with sync_playwright() as pw:
                b = pw.chromium.connect_over_cdp(f"http://localhost:{port}")
                info["chrome"] = True
                ctx = b.contexts[0]
                pages = [p for p in ctx.pages if "koreanair" in p.url] or ctx.pages
                if not pages:
                    page = ctx.new_page()
                    page.goto("https://www.koreanair.com/kr/ko",
                              wait_until="domcontentloaded", timeout=60000)
                else:
                    page = pages[-1]
                # 아침엔 갓 띄운 크롬이라 헤더가 늦다. 좀 더 오래 기다려 준다.
                info["loggedIn"] = check_login(page, 20 if a.quick else 8)
                b.close()
        except Exception as e:
            info["why"] = f"연결 실패: {str(e)[:70]}"
            problems.append(f"★ {tag}({port}) 크롬이 없습니다 - dev/browsers.ps1 로 띄우세요")
            log(f"  {tag}({port}): 크롬 없음")
            continue

        if a.quick:
            # 아침엔 여기까지. 로그인은 못 읽어도 막지 않는다(위 주석 참고).
            if info["loggedIn"]:
                log(f"  {tag}({port}): 크롬 OK / 로그인 확인됨")
            else:
                log(f"  {tag}({port}): 크롬 OK / 로그인 확인 못 함 (측정이 다시 시도한다)")
                warns.append(f"{tag}({port}) 로그인을 읽지 못했습니다 - 그 창을 눈으로 확인해 주세요")
            continue

        log(f"  {tag}({port}): 크롬 OK / 로그인 "
            f"{'확인됨' if info['loggedIn'] else '아직 확인 안 됨 (셋업에서 다시 본다)'}")

        # 2) 달력까지 실제로 세워본다. setup 은 로그아웃이면 네이버 연동을 스스로 시도한다.
        cmd = [sys.executable, str(ROOT / "dev" / "setup.py"), route,
               "--port", str(port), "--date", stand.isoformat()]
        if origin:
            cmd += ["--from", origin]
        # 크롬을 갓 띄운 직후에는 첫 시도가 자주 실패하고 두 번째에 된다(09-03 에
        # 두 번, 09-03 저녁 리허설에서 또 두 번). 저녁이라 재시도할 시간이 있다.
        st2 = {}
        for attempt in (1, 2):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
                tail = (r.stdout or "").strip().splitlines()
                st2 = json.loads(tail[-1]) if tail else {}
            except Exception as e:
                st2 = {"ok": False, "why": f"setup 실패: {e}"[:80]}
            if st2.get("ok") or "로그인" in (st2.get("why") or ""):
                break
            if attempt == 1:
                log(f"  {tag}({port}): 1차 실패({st2.get('why')}) - 다시 시도")
        info["ready"] = bool(st2.get("ok"))
        info["why"] = st2.get("why", "")
        info["url"] = st2.get("url", "")
        if info["ready"]:
            info["loggedIn"] = True   # 로그인 없이는 마일리지 달력까지 못 간다
            log(f"  {tag}({port}): 화면 준비됨")
        else:
            why = info["why"] or "알 수 없음"
            log(f"  {tag}({port}): 화면 준비 실패 - {why}")
            if "로그인" in why:
                problems.append(f"★ {tag}({port}) 로그인이 필요합니다 - 그 창에서 직접 로그인해 주세요")
            else:
                problems.append(f"{tag}({port}) 준비 실패: {why}")

    result["problems"] = problems
    result["warns"] = warns
    result["ok"] = not problems
    OUT.mkdir(exist_ok=True)
    name = "preflight_quick.json" if a.quick else "preflight.json"
    (OUT / name).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    print()
    if problems:
        print("=" * 60)
        print("  준비 안 됨 - 사람이 손봐야 합니다")
        for p in problems:
            print("   - " + p)
        print("=" * 60)
        return 1
    print("=" * 60)
    if a.quick:
        print("  크롬 살아 있음 - 측정을 시작합니다")
        for w in warns:
            print("   (참고) " + w)
    else:
        print(f"  {when} 9시 준비 완료 - {origin or 'SEL'} -> {route}, {opens} 를 노린다")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
