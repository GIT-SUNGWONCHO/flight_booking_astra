"""실사이트 검증: 로그인된 크롬에 붙어 진짜 대한항공에서 매크로를 돌린다(결제 OFF).

가짜 픽스처가 아니라 실제 조회 응답으로 도는지 본다. 특히 매진 정지가 실제
KEBONUSPR.soldout 로 발동하는지. 매진인 날짜(예: 오늘 아침 열려 이미 매진된 08-27)로
돌리면 좌석 단계에서 멈추므로 주문이 생기지 않는다. allowPay 는 강제로 끈다.

전제:  dev-browser.cmd 로 크롬이 떠 있고 스카이패스 로그인이 된 상태.
사용:  .venv/Scripts/python.exe dev/realtest.py --route CDG --date 2027-08-27
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
OUT = ROOT / "dev-shots"
CDP = "http://localhost:9232"
KST = timezone(timedelta(hours=9))


def log(m): print(f"  [{datetime.now(KST).strftime('%H:%M:%S')}] {m}", flush=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", default="CDG")
    ap.add_argument("--date", default="", help="목표 날짜 YYYY-MM-DD (비우면 달력의 최신 오픈일)")
    ap.add_argument("--cabin", default="프레스티지")
    ap.add_argument("--no-setup", action="store_true", help="이미 달력에 서 있으면 셋업 건너뜀")
    ap.add_argument("--dry", action="store_true",
                    help="7단계(첫 주문) 앞에서 멈춘다 - 좌석 선점(hold)도 안 만든다")
    ap.add_argument("--pay", action="store_true",
                    help="끝까지 간다: 결제하기까지 눌러 결제창(네이버페이)을 연다. "
                         "7단계에서 진짜 좌석 hold 가 생긴다 - 매진 아닌 좌석으로만 쓸 것")
    a = ap.parse_args()
    mmdd = a.date[5:] if a.date else ""   # YYYY-MM-DD -> MM-DD (비우면 최신 오픈일)

    if not a.no_setup:
        log(f"셋업: {a.route} {a.date} 달력까지")
        r = subprocess.run([sys.executable, str(ROOT / "dev" / "setup.py"), a.route,
                            "--date", a.date], capture_output=True, text=True, timeout=420)
        tail = (r.stdout or "").strip().splitlines()
        try: st = json.loads(tail[-1]) if tail else {}
        except Exception: st = {}
        if not st.get("ok"):
            log(f"셋업 실패: {st.get('why') or (r.stderr or '')[:120]}")
            return 2
        log("달력 준비됨")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        js = USER.read_text(encoding="utf-8")
        pages = [p for p in ctx.pages if "koreanair" in p.url]
        if not pages:
            log("대한항공 탭이 없다"); return 3
        page = pages[-1]
        try: page.bring_to_front()
        except Exception: pass
        page.evaluate(js)

        # 결제는 강제로 끈다. 달력 모드, 목표 등급/날짜로 세팅하고 발사.
        page.evaluate("""({cabin, date, dry, pay}) => {
          const R = window.KE_REC, H = window.KE_HUD;
          R.pause('realtest'); R.state.playAfterReload = false;
          R.loadBaked();
          if (dry) R.state.steps = R.state.steps.slice(0, 6);   // 7단계(첫 주문) 앞까지만
          R.state.cabin = cabin;
          R.state.expectDate = date;         // 비우면 최신 오픈일
          R.state.allowPay = !!pay;          // --pay 일 때만 결제창까지
          R.state.byCause = {}; R.state.problem = false; R.state.openReloads = 0;
          R.reset(); R.save();
          H.state.startAt = 'calendar'; H.state.armed = false; H.save();
        }""", {"cabin": a.cabin, "date": mmdd, "dry": a.dry, "pay": a.pay})
        log(f"발사 (목표 {mmdd or '최신 오픈일'} {a.cabin}, 결제 {'ON(결제창까지)' if a.pay else 'OFF'}, dry={a.dry})")
        t0 = time.time()
        page.evaluate("() => window.KE_HUD.fire('realtest')")

        last = -1
        while time.time() - t0 < 120:
            time.sleep(0.25)
            try:
                if not page.evaluate("() => !!window.KE_REC"):
                    page.evaluate(js)
            except Exception:
                continue
            try:
                s = page.evaluate("""() => ({idx: KE_REC.state.idx, n: KE_REC.state.steps.length,
                  playing: KE_REC.state.playing, msg: (KE_REC.state.message||'').slice(0,260),
                  problem: KE_REC.state.problem, reloads: KE_REC.state.openReloads,
                  url: location.pathname})""")
            except Exception:
                continue
            if s["idx"] != last:
                log(f"{s['idx']}/{s['n']}  [{s['url']}]")
                last = s["idx"]
            if not s["playing"] and s["idx"] > 0:
                log(f"멈춤: {s['msg']}")
                popup = any(("pay.naver" in p.url) or ("payment-loading" in p.url)
                            or ("npay" in p.url) for p in ctx.pages)
                if popup:
                    log("★ 결제창(네이버페이) 열림 확인 - 끝까지 갔다")
                report = {"at": datetime.now(KST).isoformat(), "route": a.route, "date": a.date,
                          "cabin": a.cabin, "idx": s["idx"], "total": s["n"],
                          "message": s["msg"], "problem": s["problem"], "reloads": s["reloads"],
                          "payWindow": popup, "seconds": round(time.time() - t0, 2)}
                try:
                    report["blocks"] = page.evaluate("() => (KE_REC.state.blocks || [])")
                except Exception: pass
                try:
                    report["seats"] = page.evaluate("() => window.KE_PROBE ? KE_PROBE.seatTimeline() : []")
                    report["keCabin"] = page.evaluate(
                        "(d) => window.KE_PROBE ? KE_PROBE.keCabin('프레스티지', d) : null", mmdd)
                except Exception: pass
                OUT.mkdir(exist_ok=True)
                (OUT / "realtest_report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
                try: page.screenshot(path=str(OUT / "realtest_end.png"))
                except Exception: pass
                log(f"리포트 -> dev-shots/realtest_report.json / 재고침 {s['reloads']}회 / {report['seconds']}초")
                print(json.dumps({"idx": s["idx"], "message": s["msg"],
                                  "keCabin": report.get("keCabin")}, ensure_ascii=False))
                b.close()
                return 0
        log("120초 안에 안 끝남")
        b.close()
        return 4


if __name__ == "__main__":
    sys.exit(main())
