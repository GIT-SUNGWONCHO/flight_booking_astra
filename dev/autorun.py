"""무인 실행: 정해진 시각에 알아서 준비하고, 발사하고, 결과를 남긴다.

사용:
  .venv/bin/python dev/autorun.py --at 09:00 --route CDG --cabin 프레스티지 --date 08-24
  .venv/bin/python dev/autorun.py --at +90s --route CDG --cabin 일반석 --dry   (리허설)

--dry 는 7단계(첫 주문) 앞에서 멈춘다. 주문을 만들지 않으므로 반복해도 안전하다.

사람이 없어도 되게 하는 것이 목적이라, 막히면 '왜' 를 파일에 남기고 끝낸다.
고칠 수 없는 것(로그인 없음, 브라우저 없음)은 그대로 보고한다 - 추측해서 진행하지 않는다.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ke_setup import nearest_future, run_setup
from runtime import output_dir, run_id, atomic_json, resolve_time, heartbeat, build_hash, measure_clock, runtime_hash

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
OUT = output_dir()
CDP = "http://localhost:9232"
CAL = "/booking/calendar-fare-bonus"
KST = timezone(timedelta(hours=9))

report: dict = {"runId": run_id()}
active_page = None


def log(m):
    print(f"  [{datetime.now(KST).strftime('%H:%M:%S')}] {m}", flush=True)


def finish(ok: bool, why: str, code: int) -> int:
    if active_page is not None:
        try:
            active_page.evaluate("""() => {
              const R=window.KE_REC, H=window.KE_HUD;
              if (R) { if (R.state.playing) R.pause('실행 종료'); R.state.allowPay=false; R.state.playAfterReload=false; R.save(); }
              if (H) { H.state.armed=false; H.save(); }
            }""")
        except Exception:
            pass
    report.update(ok=ok, why=why, endedAt=datetime.now(KST).isoformat())
    OUT.mkdir(exist_ok=True)
    atomic_json(OUT / "autorun_report.json", report)
    heartbeat("macro", "finished", ok=ok, why=why)
    log(("성공: " if ok else "실패: ") + why)
    print(json.dumps({"ok": ok, "why": why}, ensure_ascii=False))
    return code


def target_time(spec: str) -> datetime:
    return resolve_time(spec)


def ensure_browser() -> bool:
    """브라우저가 없으면 띄운다. 뜨는 데 시간이 걸리므로 넉넉히 기다린다."""
    import urllib.request
    for attempt in range(2):
        try:
            urllib.request.urlopen(CDP + "/json/version", timeout=4).read()
            return True
        except Exception:
            pass
        if attempt == 0:
            log("브라우저가 없어 새로 띄운다")
            try:
                if sys.platform == "win32":
                    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "dev/astra_browsers.ps1"), "-Port", "9232"],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    subprocess.Popen(["bash", str(ROOT / "dev-browser.sh")],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
            except Exception as e:
                log(f"띄우기 실패: {e}")
                return False
            time.sleep(18)
    return False


def main() -> int:
    global active_page
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="+60s", help="발사 시각 (09:00 또는 +90s)")
    ap.add_argument("--route", default="", help="도착지 코드 (CDG/FCO/ZRH/ICN). 생략하면 현재 설정")
    # 유럽발(로마->인천 등). 9/9 목표가 FCO->ICN 이라 필요하다. setup 으로 넘긴다.
    ap.add_argument("--from", dest="origin", default="", help="출발지 코드 (유럽발이면 FCO/CDG)")
    ap.add_argument("--cabin", default="프레스티지")
    ap.add_argument("--date", default="", help="목표 날짜 MM-DD (비우면 검사 안 함)")
    ap.add_argument("--dry", action="store_true", help="7단계(첫 주문) 앞에서 멈춘다")
    ap.add_argument("--hold", action="store_true", help="명시적으로 주문 생성까지 진행, 결제는 수동")
    ap.add_argument("--payment-window", action="store_true", help="실제 결제창을 열고 멈춤. 최종 결제 승인 금지")
    ap.add_argument("--max-late", type=float, default=1.0)
    ap.add_argument("--mode", choices=["ui", "hybrid-calendar"], default="ui")
    # 시작 화면. calendar = 달력에서 새로고침(검증됨), departure = 조회 화면에서 시작
    # (달력 한 장을 건너뛰어 앞단이 빨라질 수 있다는 가설. 실효를 재려고 넣었다).
    ap.add_argument("--start", default="calendar", choices=["calendar", "departure"])
    ap.add_argument("--lead", type=int, default=2500,
                    help="선발사(ms). 오픈시각보다 이만큼 일찍 새로고침해 조회가 09:00 직후 도착하게 한다")
    a = ap.parse_args()
    if sum([a.dry, a.hold, a.payment_window]) > 1:
        ap.error("--dry, --hold, --payment-window are mutually exclusive")
    a.dry = not (a.hold or a.payment_window)
    if not a.dry and a.mode != "ui":
        ap.error("혼합 모드는 실사이트 비교 검증 전까지 dry 전용입니다")

    fire_at = target_time(a.at)
    clock = measure_clock()
    offset = clock["offset"]
    report["clock"] = clock
    if not a.dry and not clock["ok"]:
        return finish(False, "실전 시계 오차 측정 실패", 6)
    report.update(startedAt=datetime.now(KST).isoformat(), fireAt=fire_at.isoformat(),
                  route=a.route, origin=a.origin, cabin=a.cabin, date=a.date, dry=a.dry,
                  buildHash=build_hash(), runtimeHash=runtime_hash(), hold=a.hold,
                  paymentWindowTest=a.payment_window,
                  departureDate=nearest_future(a.date).isoformat() if a.date else None)
    heartbeat("macro", "preparing", date=a.date)
    log(f"발사 예정 {fire_at.strftime('%H:%M:%S')} / 노선 {a.route or '(현재)'} / {a.cabin} / dry={a.dry}")

    if not ensure_browser():
        return finish(False, "브라우저를 띄우지 못함 (PC 가 켜져 있는지 확인)", 1)

    # --- 준비: 달력까지 ---
    # 목표 날짜의 '월' 로 달력을 옮겨야 그 날짜가 보인다. setup 은 YYYY-MM-DD 를 받는다.
    # 마일리지는 ~1년 뒤를 열므로, 목표 월이 이번 달보다 이르면 내년으로 본다.
    setup_cmd = [sys.executable, str(ROOT / "dev" / "setup.py")] + ([a.route] if a.route else [])
    if a.origin:
        setup_cmd += ["--from", a.origin]
    if a.start == "departure":
        setup_cmd += ["--departure"]   # 달력이 아니라 조회 화면까지 가서 선다
    if a.date:
        # 연도 계산은 ke_setup.nearest_future 한 곳에만 둔다.
        # 여기서 따로 계산하다가 09-07 에 2026년 달력을 잡아 세 번 연속 죽었다.
        setup_cmd += ["--date", nearest_future(a.date).isoformat()]
    # 한 번 실패했다고 하루를 버리지 않는다. 발사 90초 전까지 다시 해본다.
    # (09-04: 새 크롬에서 1차 실패하고 그대로 죽어 09:00 을 통째로 놓쳤다.
    #  이 사실은 FACTS 에 있었는데 재시도를 preflight 에만 넣어 두었다.)
    st = run_setup(setup_cmd, fire_at - timedelta(seconds=90), log)
    if not st.get("ok"):
        return finish(False, f"달력 준비 실패: {st.get('why')}", 2)
    log("달력 준비됨")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(CDP)
        ctx = b.contexts[0]
        from browser_identity import mark_context
        mark_context(ctx, 9232)
        js = USER.read_text(encoding="utf-8")
        ctx.add_init_script("if (location.hostname === 'www.koreanair.com') {\n" + js + "\n}")
        page = [p for p in ctx.pages if "koreanair" in p.url][0]
        active_page = page
        original_pages = set(ctx.pages)
        from network_trace import NetworkTrace
        trace = NetworkTrace(page, OUT, a.date)
        try: page.bring_to_front()      # 뒤에 있으면 브라우저가 타이머를 늦춘다
        except Exception: pass
        page.evaluate(js)

        page.evaluate("""({cabin, date, dry, hold, paymentWindow, start}) => {
          const R = window.KE_REC, H = window.KE_HUD;
          R.pause('autorun'); R.state.playAfterReload = false;
          R.loadBaked();
          if (dry) R.state.steps = R.state.steps.slice(0, 6);   // 7단계(첫 주문) 전까지
          else if (hold) R.state.steps = R.state.steps.slice(0, 7);
          for (const step of R.state.steps) if (step.sel === '#submit-contact') step.noRetry = true;
          R.state.cabin = cabin;
          R.state.expectDate = date || '';
          R.state.allowPay = paymentWindow;
          R.state.byCause = {}; R.state.problem = false;
          R.reset(); R.save();
          H.state.startAt = start;
          H.state.armed = false; H.save();
        }""", {"cabin": a.cabin, "date": a.date, "dry": a.dry, "hold": a.hold,
                 "paymentWindow": a.payment_window, "start": a.start})
        hybrid = None
        if a.mode == "hybrid-calendar":
            from hybrid import CalendarPrefetch
            hybrid = CalendarPrefetch(page, (fire_at.timestamp() - offset) * 1000)
            hybrid.prepare()
            report["hybrid"] = hybrid.report

        # 선발사: 오픈시각보다 lead 만큼 일찍 새로고침한다. 페이지가 뜨는 데 ~2.5초가
        # 걸려서, 08:59:57.5 에 쏘면 조회가 09:00:01 경 (오픈 직후) 도착해 재고침을 피한다.
        lead_at = fire_at - timedelta(milliseconds=a.lead)
        report["leadMs"] = a.lead
        report["leadFireAt"] = lead_at.isoformat()
        wait = (lead_at - datetime.now(KST)).total_seconds() - offset
        monotonic_fire = time.monotonic() + wait
        if wait > 0:
            log(f"{wait:.0f}초 대기 (발사 {lead_at.strftime('%H:%M:%S.%f')[:-3]}, 오픈 {fire_at.strftime('%H:%M:%S')} - 선발사 {a.lead}ms)")
            shown = False
            while True:
                left = monotonic_fire - time.monotonic()
                heartbeat("macro", "ready", fireAt=fire_at.isoformat(), date=a.date,
                          cabin=a.cabin, route=a.route, origin=a.origin, url=page.url)
                if left <= 0:
                    break
                # 발사 30초 전에 창을 앞으로 가져온다 - 사람이 눈으로 지켜볼 수 있게
                # (겸사겸사 탭이 뒤에 있어 크롬이 타이머를 늦추는 것도 막는다)
                if not shown and left <= 30:
                    shown = True
                    try:
                        page.bring_to_front()
                        log("창을 앞으로 (발사 30초 전)")
                    except Exception:
                        pass
                page.wait_for_timeout(min(3000, max(1, left * 1000)))

        if time.monotonic() - monotonic_fire > a.max_late:
            return finish(False, "발사 마감 초과; 늦은 실행을 중단합니다", 6)
        heartbeat("macro", "firing", fireAt=fire_at.isoformat())
        log("발사")
        t0 = time.time()
        try: page.bring_to_front()      # 발사 순간에도 한 번 더 (그새 뒤로 갔을 수 있다)
        except Exception: pass
        if not page.evaluate("() => window.KE_HUD.fire('autorun')"):
            return finish(False, "HUD가 발사를 거절했습니다", 6)

        last, popup = -1, False
        ctx.on("page", lambda p: None)
        while time.time() - t0 < 180:
            page.wait_for_timeout(200)
            # 페이지가 넘어가면 스크립트가 사라질 수 있다 (Tampermonkey 없이 붙여 쓰는 구조).
            # 없으면 그 자리에서 다시 넣는다 - 재생 위치는 localStorage 에 있어 이어진다.
            try:
                if not page.evaluate("() => !!window.KE_REC"):
                    page.evaluate(js)
                    log("스크립트 재주입")
            except Exception:
                continue
            try:
                s = page.evaluate("""() => ({idx: KE_REC.state.idx, n: KE_REC.state.steps.length,
                  playing: KE_REC.state.playing, msg: (KE_REC.state.message||'').slice(0,220),
                  cause: KE_REC.state.byCause, problem: KE_REC.state.problem,
                  times: KE_REC.state.times, url: location.pathname})""")
            except Exception:
                continue
            if s["idx"] != last:
                log(f"{s['idx']}/{s['n']} [{s['url']}]")
                last = s["idx"]
            if not s["playing"] and s["idx"] > 0:
                popup = any("pay.naver" in p.url or "payment-loading" in p.url for p in ctx.pages)
                report.update(idx=s["idx"], total=s["n"], msg=s["msg"], problem=s["problem"],
                              byCause=s["cause"], times=s["times"], payWindow=popup,
                              seconds=round(time.time() - t0, 2))
                break
        else:
            return finish(False, "180초 안에 끝나지 않음", 4)

        OUT.mkdir(exist_ok=True)
        try:
            report["seats"] = page.evaluate("() => window.KE_PROBE ? KE_PROBE.seatTimeline() : []")
        except Exception: pass

        done = report.get("idx", 0) == report.get("total", 99)
        if a.dry and done:
            readiness = """() => {
              const e = document.querySelector('#submit-contact');
              return location.pathname.includes('/payment/gate/') && !!e && KE_UTIL.visible(e) && !e.disabled;
            }"""
            # The final dry click can finish while its asynchronous UI is still
            # loading. Count that bounded wait in the measured time, too.
            ready_started = time.monotonic()
            try:
                page.wait_for_function(readiness, timeout=5000)
                report["dryReady"] = True
            except Exception:
                report["dryReady"] = False
            report["dryReadinessMs"] = round((time.monotonic() - ready_started) * 1000)
            report["seconds"] = round(time.time() - t0, 2)
            done = done and report["dryReady"]
        why = report.get("msg", "")
        if a.dry and not report.get("dryReady"):
            why = "주문 직전 화면 준비 조건 미충족: " + why
        if a.hold:
            # A click or HTTP 200 alone is not evidence of an order. Observe the
            # site's actual response, without persisting its identifier or PII.
            until = time.monotonic() + 10
            while time.monotonic() < until and not trace.order_created:
                page.wait_for_timeout(100)
            report["orderCreated"] = trace.order_created
            report["holdVerified"] = False
            report["manualPaymentRequired"] = trace.order_created
            report["seconds"] = round(time.time() - t0, 2)
            done = done and trace.order_created
            why = "주문 응답 확인; 결제는 수동, 좌석 보장 여부는 별도 확인" if done else "주문 성공 응답을 확인하지 못함; 중복 주문 방지를 위해 자동 재시도하지 않음"
        if a.payment_window and done:
            from payment_window import inspect_payment_window
            observed = {'ready': False}
            until = time.monotonic() + 90
            notified = False
            while time.monotonic() < until:
                for candidate in ctx.pages:
                    if candidate in original_pages or candidate.is_closed():
                        continue
                    observed = inspect_payment_window(candidate)
                    if observed.get('ready'):
                        break
                if observed.get('ready'):
                    break
                if not notified:
                    log('결제창 로딩/로그인 확인 대기. 이 창에는 자동 클릭을 수행하지 않습니다.')
                    notified = True
                page.wait_for_timeout(500)
            report['paymentWindow'] = observed
            report['payWindowReady'] = observed.get('ready') is True
            report['seconds'] = round(time.time() - t0, 2)
            report['paymentApprovalClicked'] = False
            done = done and report['payWindowReady']
            why = '결제창 표시 확인; 최종 결제 승인하지 않음' if done else '실제 결제창 표시를 확인하지 못함'
        ok = done and not report.get("problem")
        try: page.screenshot(path=str(OUT / "autorun_end.png"))
        except Exception: pass
        trace.save()
        if hybrid:
            report["hybrid"] = hybrid.report
            hybrid.close()
        return finish(ok, why, 0 if ok else 5)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        sys.exit(finish(False, f"실행 예외: {type(e).__name__}: {str(e)[:160]}", 9))
