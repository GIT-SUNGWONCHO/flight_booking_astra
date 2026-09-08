"""관찰 전용: 예약 흐름의 각 단계가 어떤 요청을 쏘고, 그중 서버 시간이 얼마인지 잰다.

왜 필요한가
  2026-09-02 실전에서 6단계 '페이지 이동 대기 3.8초' 때문에 3초 차이로 졌다.
  그 3.8초가 (a) 서버가 처리하는 시간이면 줄일 수 없고,
  (b) 페이지 로딩·렌더 시간이면 요청만 직접 쏘아 줄일 수 있다.
  어느 쪽인지 모르고 만들면 헛수고다. 그래서 먼저 잰다.

무엇을 하나 (코드 변경 없음, 관찰만)
  - 네트워크 요청/응답을 시각과 함께 전부 기록 (페이지 이동을 넘어)
  - 매크로의 단계 전환 시각을 기록
  - 둘을 겹쳐 '단계별 총 시간 vs 그 안의 서버 시간' 을 뽑는다

안전
  결제는 누르지 않는다(allowPay=false). 일반석으로 돌리면 좌석이 넉넉해 경쟁이 없고,
  7단계에서 생기는 hold 는 결제를 안 하면 자동 해제된다. 계측용 계정(9233)에서 돈다.

사용:
  .venv/Scripts/python.exe dev/observe.py --route CDG --date 08-11 --port 9233
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
OUT = ROOT / "dev-shots"
KST = timezone(timedelta(hours=9))

# 관심 있는 것만. 정적 파일·추적기는 빼야 표가 읽힌다.
KEEP = ("koreanair.com/api/", "/booking/", "/payment/")
SKIP = ("analytics", "/hit", "log-tracking", "predict", "gtm", "doubleclick",
        ".js", ".css", ".png", ".jpg", ".svg", ".woff", ".gif", ".ico")


def log(m):
    print(f"  [{datetime.now(KST).strftime('%H:%M:%S.%f')[:-3]}] {m}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="CDG")
    ap.add_argument("--from", dest="origin", default="")
    ap.add_argument("--date", default="", help="목표 날짜 MM-DD (비우면 최신 오픈일)")
    ap.add_argument("--cabin", default="일반석", help="경쟁 없는 등급으로 관찰하는 게 안전하다")
    ap.add_argument("--port", type=int, default=9233)
    ap.add_argument("--no-setup", action="store_true")
    ap.add_argument("--secs", type=int, default=90)
    a = ap.parse_args()

    if not a.no_setup:
        yr = datetime.now(KST).year + 1
        cmd = [sys.executable, str(ROOT / "dev" / "setup.py"), a.route,
               "--port", str(a.port)]
        if a.date:
            cmd += ["--date", f"{yr}-{a.date}"]
        if a.origin:
            cmd += ["--from", a.origin]
        log("달력 준비")
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        tail = (r.stdout or "").strip().splitlines()
        try: st = json.loads(tail[-1]) if tail else {}
        except Exception: st = {}
        if not st.get("ok"):
            log(f"준비 실패: {st.get('why')}")
            return 2
        log("달력 준비됨")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f"http://localhost:{a.port}")
        ctx = b.contexts[0]
        js = USER.read_text(encoding="utf-8")
        ctx.add_init_script(js)
        page = [p for p in ctx.pages if "koreanair" in p.url][-1]
        try: page.bring_to_front()
        except Exception: pass

        net: list = []          # {url, method, t0, t1}
        pending: dict = {}

        def want(u: str) -> bool:
            return any(k in u for k in KEEP) and not any(s in u.lower() for s in SKIP)

        def on_req(req):
            try:
                if want(req.url):
                    pending[req] = time.time()
            except Exception:
                pass

        def on_res(res):
            try:
                t0 = pending.pop(res.request, None)
                if t0 is None:
                    return
                t1 = time.time()
                net.append({"url": res.url.split("?")[0][-70:], "method": res.request.method,
                            "at": t0, "end": t1, "ms": round((t1 - t0) * 1000)})
            except Exception:
                pass

        ctx.on("request", on_req)
        ctx.on("response", on_res)

        page.evaluate(js)
        page.evaluate("""({cabin, date}) => {
          const R = window.KE_REC, H = window.KE_HUD;
          R.pause('observe'); R.state.playAfterReload = false;
          R.loadBaked();
          R.state.cabin = cabin;
          R.state.expectDate = date;
          R.state.steps = R.state.steps.slice(0, 6); // 관찰도 주문 전 정지
          R.state.allowPay = false;
          R.state.byCause = {}; R.state.problem = false; R.state.openReloads = 0;
          R.reset(); R.save();
          H.state.startAt = 'calendar'; H.state.armed = false; H.save();
        }""", {"cabin": a.cabin, "date": a.date})

        log(f"발사 (관찰, {a.cabin}, 결제 OFF)")
        t0 = time.time()
        steps: list = []        # {idx, at}
        last = -1
        page.evaluate("() => window.KE_HUD.fire('observe')")

        while time.time() - t0 < a.secs:
            time.sleep(0.15)
            try:
                if not page.evaluate("() => !!window.KE_REC"):
                    page.evaluate(js)
                s = page.evaluate("""() => ({idx: KE_REC.state.idx, n: KE_REC.state.steps.length,
                  playing: KE_REC.state.playing, msg: (KE_REC.state.message||'').slice(0,120)})""")
            except Exception:
                continue
            if s["idx"] != last:
                steps.append({"idx": s["idx"], "at": time.time()})
                log(f"{s['idx']}/{s['n']}  (+{time.time()-t0:.2f}s)")
                last = s["idx"]
            if not s["playing"] and s["idx"] > 0:
                log(f"멈춤: {s['msg'][:100]}")
                break

        # 단계 구간마다 그 안에서 오간 요청을 묶는다
        rows = []
        for i, st in enumerate(steps):
            end = steps[i + 1]["at"] if i + 1 < len(steps) else time.time()
            inside = [n for n in net if n["at"] < end and n.get("end", n["at"]) > st["at"]]
            # 요청은 병렬로 나간다. 단순 합산하면 벽시계를 넘어(실측 463%) 의미가 없다.
            # '요청이 하나라도 떠 있던 시간' = 구간들의 합집합을 서버 시간으로 본다.
            iv = sorted(((max(n["at"], st["at"]), min(n.get("end", n["at"]), end))
                         for n in inside), key=lambda x: x[0])
            server_s, cur_s, cur_e = 0.0, None, None
            for s0, s1 in iv:
                if s1 <= s0:
                    continue
                if cur_s is None:
                    cur_s, cur_e = s0, s1
                elif s0 <= cur_e:
                    cur_e = max(cur_e, s1)
                else:
                    server_s += cur_e - cur_s
                    cur_s, cur_e = s0, s1
            if cur_s is not None:
                server_s += cur_e - cur_s
            server = round(server_s * 1000)
            rows.append({
                "step": st["idx"], "sinceFire": round(st["at"] - t0, 2),
                "stepMs": round((end - st["at"]) * 1000),
                "networkOccupiedMs": server, "requests": len(inside),
                "slowest": sorted(inside, key=lambda x: -x["ms"])[:3],
            })

        OUT.mkdir(exist_ok=True)
        (OUT / "observe_report.json").write_text(
            json.dumps({"at": datetime.now(KST).isoformat(), "route": a.route,
                        "cabin": a.cabin, "rows": rows, "net": net[-80:]},
                       ensure_ascii=False, indent=1), encoding="utf-8")

        print("\n=== 단계별: 총 시간 vs 네트워크 관측 구간 합집합 (서버 처리 시간이 아님) ===")
        print(f"{'단계':>4} {'발사후':>7} {'총ms':>7} {'서버ms':>7} {'서버%':>6}  느린 요청")
        for r in rows:
            pct = round(r["networkOccupiedMs"] / r["stepMs"] * 100) if r["stepMs"] else 0
            slow = ", ".join(f"{x['url'][-34:]} {x['ms']}ms" for x in r["slowest"][:2])
            print(f"{r['step']:>4} {r['sinceFire']:>7.2f} {r['stepMs']:>7} "
                  f"{r['networkOccupiedMs']:>7} {pct:>5}%  {slow}")
        log("리포트 -> dev-shots/observe_report.json")
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
