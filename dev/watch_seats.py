"""좌석 계측기: 09:00 에 새로 열리는 날짜의 프레스티지 좌석이 몇 석이고
언제 0 이 되는지 잰다. 읽기 전용 - 예약은 절대 하지 않는다.

설계 (2026-09-03 리뷰로 전면 수정)
  좌석 수는 7단계까지 안 가도 조회 응답에 있다
  (awardAvailability -> commercialFareFamilyList -> KEBONUSPR.seatCount/soldout).

  목표일 D 는 09:00:00 에야 고를 수 있다. 그런데 **화면은 저절로 바뀌지 않는다** -
  08:5x 에 그려진 날짜 띠는 D 가 없던 시절의 응답으로 만들어진 것이고, 가만히
  들여다봐도 영원히 D 가 안 나타난다(실측). 그래서 이 저장소에서 이미 검증된 방식,
  즉 **새로고침으로 다시 그리기**를 쓴다 (recorder.js 가 목표 날짜를 기다릴 때 하는 것과 같다).

  한 주기: 새로고침 -> 띠에 D 가 고를 수 있게 나타났나 -> 누른다 -> D 응답 확인
  그 뒤에야 1초 간격 되쏘기(reAsk)로 촘촘히 잰다.

  되쏘기를 D 응답 확인 전에 시작하면 안 된다. reAsk 는 '가장 최근 조회 요청' 을
  되쏘는데 그게 아직 D-1 요청이면, 그 응답이 또 최신이 되어 D-1 을 영원히 되쏘는
  고리에 빠진다. 그러면 콘솔엔 좌석이 살아있는 것처럼 예쁘게 찍히는데 값은 전부
  D-1 것이다 - 비어 있는 것보다 나쁘다.

안전
  조회만 한다. 예약 단계는 밟지 않고 HUD 무장도 꺼둔다.

사용:
  .venv/Scripts/python.exe dev/watch_seats.py --route FCO --date 08-30 --at 09:00 --port 9233
"""
from __future__ import annotations
import argparse, json, subprocess, sys, time, traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ke_setup import nearest_future, run_setup
from runtime import output_dir, run_id, atomic_json, resolve_time, heartbeat, measure_clock

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
OUT = output_dir()
KST = timezone(timedelta(hours=9))
STRIP_SPAN = 3          # 조회 화면 날짜 띠가 품는 범위 (선택일 ±3일)


def log(m):
    print(f"  [{datetime.now(KST).strftime('%H:%M:%S.%f')[:-3]}] {m}", flush=True)


def wait_until(when: datetime) -> None:
    while True:
        left = (when - datetime.now(KST)).total_seconds()
        if left <= 0.02:
            return
        time.sleep(min(5, max(0.02, left)))


def at(spec: str) -> datetime:
    return resolve_time(spec)


# nearest_future 는 ke_setup 에 있다. 여기 복사본을 두었다가 autorun 만 옛 규칙으로
# 남아 09-07 에 세 번 죽었다. 날짜 계산은 저장소에 한 벌만 둔다.


# 날짜 띠에서 목표일을 누른다. findStripDate 는 {el, selectable, why} 를 돌려준다.
PRESS = """(mmdd) => {
  const U = window.KE_UTIL;
  if (!U || !U.findStripDate) return 'no-util';
  const c = U.findStripDate(mmdd);
  if (!c) return 'no-strip';
  if (!c.el) return 'none:' + (c.why || '');
  if (!c.selectable) return 'locked:' + (c.why || '');
  U.fireClick(c.el);
  return 'ok';
}"""

# 지금 화면이 보고 있는 날짜(MM-DD). 엉뚱한 날짜에 서 있는지 확인하는 근거.
SCREEN = """() => {
  const U = window.KE_UTIL;
  return U && U.searchedDate ? U.searchedDate() : null;
}"""

# 목표 날짜의 좌석. lastAt 은 '그 날짜를 담은 응답' 의 도착 시각이라야 한다.
# 아무 응답의 시각을 쓰면 D-1 되쏘기가 새 표본으로 둔갑한다(리뷰 지적).
READ = """(cab) => {
  const P = window.KE_PROBE;
  if (!P || !P.keCabin) return null;
  const pr = P.keCabin('프레스티지', cab), ey = P.keCabin('일반석', cab);
  return {pr, ey, lastAt: pr ? pr.responseAt : (ey ? ey.responseAt : 0)};
}"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--route", required=True)
    ap.add_argument("--from", dest="origin", default="")
    ap.add_argument("--date", required=True, help="지켜볼 출발일 MM-DD (그날 새로 열리는 날)")
    ap.add_argument("--at", default="09:00")
    ap.add_argument("--lead", type=int, default=2500)
    ap.add_argument("--until", type=int, default=90)
    ap.add_argument("--gap", type=float, default=6.0)
    ap.add_argument("--fast-gap", type=float, default=1.0)
    ap.add_argument("--fast-window", type=float, default=20.0)
    ap.add_argument("--reload-gap", type=float, default=1.5, help="D 를 기다리며 새로고침하는 간격")
    ap.add_argument("--port", type=int, default=9233)
    ap.add_argument("--setup-at", default="")
    a = ap.parse_args()

    tgt = nearest_future(a.date)
    stand = tgt - timedelta(days=1)
    open_at = at(a.at)
    clock = measure_clock()
    offset = clock["offset"]
    fire_at = open_at - timedelta(milliseconds=a.lead) - timedelta(seconds=offset)
    end_at = open_at + timedelta(seconds=a.until)

    rows: list = []
    report = {"runId": run_id(), "startedAt": datetime.now(KST).isoformat(), "route": a.route,
              "origin": a.origin or "SEL", "date": a.date, "target": tgt.isoformat(),
              "openAt": open_at.isoformat(), "clock": clock, "ok": False, "why": "시작 전"}

    def save_report():
        # 예외로 죽어도 리포트는 남긴다. 안 남기면 daily 가 '어제 파일' 을 오늘로 읽는다.
        try:
            ever = any(r.get("prListed") for r in rows)
            report["rows"] = rows
            report["samples"] = len(rows)
            report["prestigeEverListed"] = ever
            OUT.mkdir(exist_ok=True)
            atomic_json(OUT / "watch_seats.json", report)
            with (OUT / "seat_history.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "day": datetime.now(KST).strftime("%Y-%m-%d"),
                    "route": a.route, "origin": a.origin or "SEL",
                    "target": tgt.isoformat(), "screen": report.get("screen"),
                    "ok": report.get("ok"), "why": report.get("why"),
                    "maxPrestigeSeats": report.get("maxPrestigeSeats"),
                    "prestigeEverListed": ever,
                    "goneSinceOpen": report.get("goneSinceOpen"),
                    "samples": len(rows), "pressAt": report.get("pressSinceOpen"),
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    # --- 준비: 목표 근처(가급적 D-1)의 조회 화면에 선다 ---
    if a.setup_at:
        wait_until(at(a.setup_at))
    # 달력에 선다.
    #
    # 조회 화면에 서서 날짜 띠를 누르는 방식은 못 쓴다: 조회 페이지를 새로고침하면
    # 세션(pageTicket)이 깨져 "정상적으로 처리되지 않았습니다" 로 뜨고 띠가 사라진다
    # (실측 2026-09-03). 새로고침 없이는 띠가 D 를 알 방법이 없다.
    #
    # 달력은 새로고침을 견딘다. 그리고 09:00 에 열리는 D 는 '가장 최신 오픈일' 이라
    # 달력 창 끝에 있어 찾을 수 있다 - 어제 9시 실전에서 이 경로로 08-28 을 정확히
    # 찾아냈다. (오늘 실패들은 최신일이 아닌 중간 날짜를 목표로 시험한 탓이었다.)
    cmd = [sys.executable, str(ROOT / "dev" / "setup.py"), a.route,
           "--port", str(a.port), "--date", stand.isoformat()]
    if a.origin:
        cmd += ["--from", a.origin]
    log(f"달력 준비 ({a.origin or 'SEL'} -> {a.route}, 목표 {tgt})")
    # 한 번 실패했다고 하루를 버리지 않는다. 발사 90초 전까지 다시 해본다.
    # (09-04: 새 크롬에서 1차 실패하고 그대로 죽어 09:00 을 통째로 놓쳤다)
    st = run_setup(cmd, open_at - timedelta(seconds=90), log)
    if not st.get("ok"):
        report["why"] = f"조회 화면 준비 실패: {st.get('why')}"
        log(report["why"]); save_report(); return 2
    log("조회 화면 준비됨")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.connect_over_cdp(f"http://localhost:{a.port}")
        ctx = b.contexts[0]
        js = USER.read_text(encoding="utf-8")
        ctx.add_init_script(js)
        pages = [p for p in ctx.pages if "koreanair" in p.url]
        if not pages:
            report["why"] = "대한항공 탭이 없다"; log(report["why"]); save_report(); return 3
        page = pages[-1]
        try: page.bring_to_front()
        except Exception: pass
        page.evaluate(js)

        # 예약 쪽이 끼어들지 않게 확실히 재운다. HUD 무장이 남아 있으면 새 문서마다
        # 스스로 발사해 예약 단계를 밟는다 - 읽기 전용 보장이 깨진다.
        page.evaluate("""() => {
          const R = window.KE_REC, H = window.KE_HUD;
          if (R) { R.pause('watch'); R.state.playing = false;
                   R.state.playAfterReload = false; R.state.allowPay = false; R.save(); }
          if (H) { H.state.armed = false; H.save(); }
        }""")

        # 달력에 서 있는지, 그리고 목표 근처인지 남겨둔다 (엉뚱한 달에 서 있어도
        # URL 만 보면 통과하므로, 나중에 결과를 의심할 근거를 기록해둔다).
        screen = None
        try: screen = page.evaluate(SCREEN)
        except Exception: pass
        report["screen"] = screen
        log(f"화면이 보고 있는 날짜: {screen}")

        # 매크로의 1~2단계(달력에서 D 클릭 -> 검색)만 재생한다. 예약 단계는 싣지 않는다.
        # D 를 못 찾으면 recorder 가 스스로 새로고침하며 기다린다(검증된 동작).
        page.evaluate("""({date}) => {
          const R = window.KE_REC, H = window.KE_HUD;
          R.loadBaked();
          R.state.steps = R.state.steps.slice(0, 2);
          R.state.expectDate = date;
          R.state.allowPay = false;
          R.state.byCause = {}; R.state.problem = false; R.state.openReloads = 0;
          R.reset(); R.save();
          H.state.startAt = 'calendar'; H.state.armed = false; H.save();
        }""", {"date": a.date})

        w = (fire_at - datetime.now(KST)).total_seconds()
        if w > 0:
            log(f"{w:.0f}초 대기 (발사 {fire_at.strftime('%H:%M:%S.%f')[:-3]})")
            while datetime.now(KST) < fire_at:
                heartbeat("watch", "ready", date=a.date, fireAt=open_at.isoformat(), url=page.url)
                time.sleep(min(3, max(0, (fire_at - datetime.now(KST)).total_seconds())))

        # --- 1) 발사: 매크로가 달력에서 D 를 찾아 누르고 검색까지 간다 ---
        log(f"발사 - 달력에서 {a.date} 를 찾아 조회 화면으로")
        if (datetime.now(KST) - fire_at).total_seconds() > 1:
            report["why"] = "발사 마감 초과"; save_report(); return 7
        if not page.evaluate("() => window.KE_HUD.fire('watch')"):
            report["why"] = "HUD 발사 거절"; save_report(); return 7
        reached = False
        while datetime.now(KST) < end_at and not reached:
            try:
                if not page.evaluate("() => !!window.KE_REC"):
                    page.evaluate(js)
                s = page.evaluate("""() => ({idx: KE_REC.state.idx,
                  playing: KE_REC.state.playing, msg: (KE_REC.state.message||'').slice(0,90),
                  reloads: KE_REC.state.openReloads})""")
            except Exception:
                time.sleep(0.2); continue
            if s["idx"] >= 2:
                reached = True
                secs = round((datetime.now(KST) - open_at).total_seconds() + offset, 2)
                report["pressSinceOpen"] = secs
                report["openReloads"] = s.get("reloads")
                log(f"조회 화면 도달 (오픈+{secs:.2f}s, 재고침 {s.get('reloads')}회)")
                break
            if not s["playing"] and s["idx"] > 0:
                report["why"] = f"매크로가 멈춤: {s['msg']}"
                log(report["why"]); save_report(); b.close(); return 5
            time.sleep(0.2)
        if not reached:
            report["why"] = f"{a.date} 조회 화면에 도달하지 못함"
            log(report["why"]); save_report(); b.close(); return 5

        # --- 2) D 응답이 올 때까지 기다린다 (되쏘기는 그 뒤에) ---
        seen_at = 0
        got_d = False
        while datetime.now(KST) < end_at and not got_d:
            try:
                d = page.evaluate(READ, a.date)
            except Exception:
                d = None
            if d and (d.get("lastAt") or 0) > 0 and (d.get("pr") or d.get("ey")):
                got_d = True
                break
            time.sleep(0.2)
        if not got_d:
            report["why"] = f"{a.date} 조회 응답이 오지 않았다"
            log(report["why"]); save_report(); b.close(); return 6

        # --- 3) 촘촘히 재기 ---
        gone_at = None
        best = None
        last_ask = 0.0
        while datetime.now(KST) < end_at:
            d = None
            try:
                if not page.evaluate("() => !!window.KE_PROBE"):
                    page.evaluate(js)
                d = page.evaluate(READ, a.date)
            except Exception:
                d = None
            if d and (d.get("lastAt") or 0) > seen_at and (d.get("pr") or d.get("ey")):
                seen_at = d["lastAt"]
                pr, ey = d.get("pr") or {}, d.get("ey") or {}
                now = datetime.now(KST)
                secs = round((now - open_at).total_seconds() + offset, 2)
                rows.append({"at": now.isoformat(), "sinceOpen": secs,
                             "responseAt": seen_at,
                             "prSeats": pr.get("seats"), "prSoldout": pr.get("soldout"),
                             "prListed": pr.get("listed"), "eySeats": ey.get("seats"),
                             "keFlights": pr.get("keFlights")})
                mark = ""
                if pr.get("listed") and not pr.get("soldout"):
                    # 0 도 유효한 값이다. truthiness 로 보면 0 이 falsy 라 1->0 을 놓친다.
                    s = pr.get("seats") or 0
                    best = s if best is None else max(best, s)
                    mark = "  ★있음"
                elif pr.get("soldout") and best is not None and not gone_at:
                    gone_at = now
                    mark = "  <- 방금 0 이 됨"
                log(f"오픈+{secs:6.2f}s  프레스티지 "
                    f"{'매진' if pr.get('soldout') else str(pr.get('seats')) + '석'}"
                    f"  (일반석 {ey.get('seats')}){mark}")
                if gone_at:
                    break
            since_open = (datetime.now(KST) - open_at).total_seconds()
            gap = a.fast_gap if since_open < a.fast_window else a.gap
            if (time.time() - last_ask) >= gap:
                last_ask = time.time()
                try:
                    page.evaluate("() => window.KE_PROBE && KE_PROBE.reAsk()")
                except Exception:
                    pass
            time.sleep(0.15)

        report.update(ok=bool(rows), why="" if rows else "유효 표본 없음", maxPrestigeSeats=best,
                      depletionObserved=gone_at is not None,
                      firstSampleSinceOpen=rows[0]["sinceOpen"] if rows else None,
                      initiallyUnavailable=bool(rows and rows[0].get("prSoldout")),
                      goneAt=gone_at.isoformat() if gone_at else None,
                      goneSinceOpen=round((gone_at - open_at).total_seconds(), 2) if gone_at else None)
        save_report()
        ever = any(r.get("prListed") for r in rows)
        note = (f"프레스티지 최대 {best}석" if best else
                ("첫 관측부터 예약 불가(0석); 개방/소진 시각은 미관측" if ever else "프레스티지 정보 없음"))
        log(f"기록 {len(rows)}건 / {note}"
            + (f" / 오픈+{report['goneSinceOpen']}초에 0" if gone_at else ""))
        b.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(9)
