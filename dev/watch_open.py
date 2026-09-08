"""목표 날짜가 언제 열리는지 지켜본다.

09:00 에 정말 딱 열리는지, 조금 이르게 열리는지를 재기 위한 것이다.
전용 탭 하나만 쓰고 읽기만 한다 - 예매는 하지 않는다.

사용:
  .venv/bin/python dev/watch_open.py --route CDG --day 08-25 \
      --setup-at 08:55 --from 08:57 --until 09:01

09:00 직전에는 촘촘히, 그 전에는 성기게 본다 (페이지 로드가 4초쯤 걸려서
너무 자주 새로고침해봐야 서버만 두드리고 얻는 게 없다).
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dev-shots"
CDP = "http://localhost:9232"
KST = timezone(timedelta(hours=9))

CHECK = """(day) => {
  // 달력 셀에서 그 날짜를 찾는다. 스크립트를 넣지 않고 순수 DOM 만 본다.
  const cells = [...document.querySelectorAll('[id^="dep-fare-"]')];
  const seen = [];
  let hit = null;
  for (const c of cells) {
    const r = c.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const t = (c.innerText || '').replace(/\\s+/g, ' ').trim();
    const m = t.match(/(\\d{1,2})\\s*월\\s*(\\d{1,2})\\s*일/);
    if (!m) continue;
    const md = ('0'+m[1]).slice(-2) + '-' + ('0'+m[2]).slice(-2);
    seen.push(md);
    if (md === day) hit = { text: t.slice(0, 46),
                            disabled: c.getAttribute('aria-disabled') === 'true',
                            cls: (c.className||'').toString().slice(0, 60) };
  }
  return { cells: cells.length, dates: seen.length, last: seen[seen.length-1] || null,
           found: hit, loading: /진행중|잠시만/.test(document.body.innerText || '') };
}"""


def wait_until(when: datetime) -> None:
    """그 시각까지 기다린다.

    time.sleep(남은초) 한 번으로 기다리면 안 된다 - macOS 가 잠든 동안 그 타이머는
    멈춘다. 노트북 덮개를 닫아두면 깨어난 뒤에야 뒤늦게 깨어나 정작 그 시각을
    한참 지나 시작한다. 벽시계를 계속 다시 보고 짧게짧게 나눠 기다린다.
    """
    while True:
        left = (when - datetime.now(KST)).total_seconds()
        if left <= 0.05:
            return
        time.sleep(min(10, max(0.05, left)))


def at(spec: str) -> datetime:
    now = datetime.now(KST)
    if spec.startswith("+"):
        return now + timedelta(seconds=int(spec[1:].rstrip("s")))
    h, m = (spec.split(":") + ["0"])[:2]
    t = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="CDG")
    ap.add_argument("--day", required=True, help="지켜볼 날짜 MM-DD (예: 08-25)")
    ap.add_argument("--tab", type=int, default=4, help="쓸 탭 번호 (노선 탭과 겹치지 않게)")
    ap.add_argument("--setup-at", default="")
    ap.add_argument("--from", dest="start", default="+0s")
    ap.add_argument("--until", default="+300s")
    ap.add_argument("--date", default="", help="셋업용 날짜 YYYY-MM-DD")
    a = ap.parse_args()

    t_start, t_end = at(a.start), at(a.until)
    rows = []

    def log(m): print(f"  [{datetime.now(KST).strftime('%H:%M:%S')}] {m}", flush=True)

    if a.setup_at:
        su = at(a.setup_at)
        w = (su - datetime.now(KST)).total_seconds()
        if w > 0:
            log(f"셋업까지 {w:.0f}초 대기 ({su:%H:%M:%S})")
            wait_until(su)
        cmd = [sys.executable, str(ROOT / "dev" / "setup.py"), a.route, "--tab", str(a.tab)]
        if a.date:
            cmd += ["--date", a.date]
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        tail = (p.stdout or "").strip().splitlines()
        try: st = json.loads(tail[-1]) if tail else {}
        except Exception: st = {}
        log(f"감시탭 셋업: {'OK' if st.get('ok') else st.get('why')}")
        if not st.get("ok"):
            return 2

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        # 탭 번호로 찾으면 안 된다 - 결제 팝업도 koreanair.com 도메인이라 번호가 밀린다.
        # 셋업이 방금 만든 달력 탭은 목록의 마지막에 있으므로 그걸 쓴다.
        cals = [p for p in ctx.pages if "calendar-fare-bonus" in p.url]
        if not cals:
            log("감시할 달력 탭이 없다"); return 3
        page = cals[-1]
        log(f"감시 탭: {page.url[:60]}")

        w = (t_start - datetime.now(KST)).total_seconds()
        if w > 0:
            log(f"감시 시작까지 {w:.0f}초 대기 ({t_start:%H:%M:%S})")
            wait_until(t_start)

        log(f"감시 시작 - {a.day} 가 열리는 순간을 찾는다")
        opened_at = None
        while datetime.now(KST) < t_end:
            now = datetime.now(KST)
            try:
                page.reload(wait_until="domcontentloaded", timeout=40000)
            except Exception:
                pass
            # 달력이 그려질 때까지 짧게 기다렸다가 본다
            r = None
            for _ in range(60):
                time.sleep(0.25)
                try: r = page.evaluate(CHECK, a.day)
                except Exception: continue
                if r and r["dates"]: break
            r = r or {"cells": 0, "dates": 0, "last": None, "found": None, "loading": True}
            stamp = datetime.now(KST)
            rows.append({"at": stamp.isoformat(), "dates": r["dates"],
                         "last": r["last"], "found": bool(r["found"]),
                         "disabled": (r["found"] or {}).get("disabled")})
            mark = "열림" if r["found"] and not r["found"].get("disabled") else (
                   "있으나 막힘" if r["found"] else "없음")
            log(f"{a.day}: {mark}  (마지막 날 {r['last']}, 날짜 {r['dates']}개)")
            if r["found"] and not r["found"].get("disabled") and not opened_at:
                opened_at = stamp
                log(f"★ {a.day} 가 열렸다: {stamp:%H:%M:%S.%f}"[:60])
                try: page.screenshot(path=str(OUT / "open_moment.png"))
                except Exception: pass
                break
            # 09:00 가까울수록 촘촘히
            gap = 4 if abs((stamp - stamp.replace(hour=9, minute=0, second=0,
                                                  microsecond=0)).total_seconds()) < 90 else 18
            time.sleep(gap)

        OUT.mkdir(exist_ok=True)
        (OUT / "watch_open.json").write_text(json.dumps(
            {"day": a.day, "route": a.route,
             "openedAt": opened_at.isoformat() if opened_at else None,
             "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"기록 {len(rows)}건 -> dev-shots/watch_open.json")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
