"""여러 노선을 한 시각에 동시에 발사한다.

사용:
  .venv/bin/python dev/autorun_multi.py --at 09:00 --routes ZRH,CDG,FCO \
      --date 2027-08-25 --cabin 프레스티지
  .venv/bin/python dev/autorun_multi.py --at +90s --routes ZRH,CDG,FCO \
      --date 2027-08-24 --cabin 일반석 --dry

노선마다 탭을 하나씩 쓴다 (같은 프로필로 된다는 것은 실측으로 확인했다).
--dry 는 7단계(첫 주문) 앞에서 멈춘다 - 주문을 만들지 않으므로 반복해도 안전하다.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
OUT = ROOT / "dev-shots"
CDP = "http://localhost:9232"
KST = timezone(timedelta(hours=9))


def log(m): print(f"  [{datetime.now(KST).strftime('%H:%M:%S')}] {m}", flush=True)


def target_time(spec: str) -> datetime:
    now = datetime.now(KST)
    if spec.startswith("+"):
        return now + timedelta(seconds=int(spec[1:].rstrip("s")))
    h, m = (spec.split(":") + ["0"])[:2]
    t = now.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="+90s")
    ap.add_argument("--routes", required=True, help="쉼표로 구분: ZRH,CDG,FCO")
    ap.add_argument("--date", default="", help="달력을 그 달로 맞추기 위한 날짜 YYYY-MM-DD")
    ap.add_argument("--cabin", default="일반석")
    ap.add_argument("--expect", default="", help="목표 날짜 MM-DD (다르면 누르지 않고 멈춤)")
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--skip-setup", action="store_true", help="탭이 이미 서 있으면 준비를 건너뛴다")
    ap.add_argument("--setup-at", default="", help="준비를 시작할 시각 (09:00 / +60s). 생략하면 바로 시작")
    ap.add_argument("--mode", default="calendar", choices=["calendar", "departure"],
                    help="calendar=달력에서 시작(검증됨) / departure=조회 화면에서 시작(달력 한 장을 건너뜀)")
    ap.add_argument("--tag", default="", help="결과 파일 이름에 붙일 꼬리표 (모드별 비교용)")
    a = ap.parse_args()

    routes = [r.strip().upper() for r in a.routes.split(",") if r.strip()]
    fire_at = target_time(a.at)
    log(f"발사 {fire_at.strftime('%H:%M:%S')} / 노선 {routes} / {a.cabin} / dry={a.dry}")

    if a.setup_at:
        su = target_time(a.setup_at)
        w = (su - datetime.now(KST)).total_seconds()
        if w > 0:
            log(f"준비 시작까지 {w:.0f}초 대기 ({su.strftime('%H:%M:%S')})")
            while (su - datetime.now(KST)).total_seconds() > 0.5:
                time.sleep(min(10, max(0.1, (su - datetime.now(KST)).total_seconds())))

    if not a.skip_setup:
        for i, r in enumerate(routes):
            cmd = [sys.executable, str(ROOT / "dev" / "setup.py"), r, "--tab", str(i)]
            if a.date:
                cmd += ["--date", a.date]
            if a.mode == "departure":
                cmd += ["--departure"]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            tail = (p.stdout or "").strip().splitlines()
            try: st = json.loads(tail[-1]) if tail else {}
            except Exception: st = {}
            log(f"준비 {r}: {'OK' if st.get('ok') else '실패 - ' + str(st.get('why'))[:60]}")
            if not st.get("ok"):
                print(json.dumps({"ok": False, "why": f"{r} 준비 실패"}, ensure_ascii=False))
                return 2

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        js = USER.read_text(encoding="utf-8")
        ctx.add_init_script(js)
        # 지난 실행에서 남은 탭을 치운다. 결제창은 물론이고 결제 게이트에 멈춰 있는
        # 탭도 자원을 잡아먹고, 노선 탭을 URL 로 골라낼 때 섞여 들어온다.
        for p in list(ctx.pages)[1:]:
            u = p.url
            if ("pay.naver" in u or "payment-loading" in u
                    or "/payment/gate" in u or u.strip() in ("about:blank", "")):
                try: p.close()
                except Exception: pass

        mark = "select-award-flight" if a.mode == "departure" else "calendar-fare-bonus"
        tabs = [p for p in ctx.pages if mark in p.url][:len(routes)]
        if len(tabs) < len(routes):
            print(json.dumps({"ok": False,
                              "why": f"{a.mode} 탭이 {len(tabs)}개뿐 (노선 {len(routes)}개)"},
                             ensure_ascii=False))
            return 3

        popups = []
        ctx.on("page", lambda p: popups.append((time.time(), p)))

        for i, p in enumerate(tabs):
            p.evaluate(js)
            p.evaluate("""({cabin, expect, dry, mode}) => {
              const R = window.KE_REC, H = window.KE_HUD;
              H.state.startAt = mode;
              R.pause('multi'); R.state.playAfterReload = false;
              R.loadBaked();
              if (dry) R.state.steps = R.state.steps.slice(0, 6);
              R.state.cabin = cabin;
              R.state.expectDate = expect || '';
              R.state.allowPay = !dry;
              R.state.byCause = {}; R.state.problem = false;
              R.reset(); R.save();
            }""", {"cabin": a.cabin, "expect": a.expect, "dry": a.dry, "mode": a.mode})
            log(f"탭{i} 준비됨 ({routes[i]})")

        wait = (fire_at - datetime.now(KST)).total_seconds()
        if wait > 0:
            log(f"{wait:.0f}초 대기")
            while (fire_at - datetime.now(KST)).total_seconds() > 0.05:
                time.sleep(min(3, max(0.02, (fire_at - datetime.now(KST)).total_seconds())))

        t0 = time.time()
        for i, p in enumerate(tabs):
            try: p.evaluate("() => window.KE_HUD.fire('multi')")
            except Exception as e: log(f"탭{i} 발사 실패 {str(e)[:50]}")
        log(f"{len(tabs)}개 동시 발사")

        done = [None] * len(tabs)
        last = [-1] * len(tabs)
        while time.time() - t0 < 200 and not all(done):
            time.sleep(0.25)
            for i, p in enumerate(tabs):
                if done[i]: continue
                try:
                    if not p.evaluate("() => !!window.KE_REC"):
                        p.evaluate(js)
                    s = p.evaluate("""() => ({idx: KE_REC.state.idx, n: KE_REC.state.steps.length,
                      playing: KE_REC.state.playing, msg:(KE_REC.state.message||'').slice(0,160),
                      problem: KE_REC.state.problem, times: KE_REC.state.times,
                      cause: KE_REC.state.byCause, url: location.pathname})""")
                except Exception:
                    continue
                if s["idx"] != last[i]:
                    log(f"{routes[i]} {s['idx']}/{s['n']} +{time.time()-t0:.1f}s")
                    last[i] = s["idx"]
                if not s["playing"] and s["idx"] > 0:
                    s["seconds"] = round(time.time() - t0, 2)
                    done[i] = s
                    log(f"{routes[i]} 종료 +{s['seconds']}s: {s['msg'][:80]}")

        time.sleep(5)
        pays = [p for _, p in popups if "pay.naver" in p.url or "payment-loading" in p.url]
        report = {"firedAt": fire_at.isoformat(), "routes": routes, "cabin": a.cabin,
                  "mode": a.mode, "dry": a.dry, "payWindows": len(pays), "results": {}}
        print("\n=== 결과 ===")
        for i, s in enumerate(done):
            ok = bool(s and s["idx"] >= s["n"] and not s["problem"])
            report["results"][routes[i]] = s or {"why": "시간초과"}
            mark = "완주" if ok else ("중단" if s else "시간초과")
            print(f"  {routes[i]}: {mark}  {(s or {}).get('seconds','?')}s  {(s or {}).get('msg','')[:70]}")
        print(f"  결제창: {len(pays)}개")
        for p in pays: print("   ", p.url[:80])

        OUT.mkdir(exist_ok=True)
        for i, p in enumerate(tabs):
            try: p.screenshot(path=str(OUT / f"{a.tag or a.mode}_{routes[i]}.png"))
            except Exception: pass
        (OUT / f"autorun_multi_{a.tag or a.mode}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
