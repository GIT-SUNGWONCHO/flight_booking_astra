"""9시에 돌 것을, 9시와 같은 상태에서, 발사까지 통째로 돌려본다.

왜 있나 (2026-09-04 에 09:00 을 통째로 잃고 만들었다)
  그때까지의 '검증' 은 두 군데가 틀려 있었다.
    1) preflight(점검기)만 돌리고, 정작 발사하는 autorun/watch_seats 는
       끝까지 돌려본 적이 없었다. 관측자를 재고 주자를 안 쟀다.
    2) 이미 달궈진 크롬에서 쟀다. 실제 조건은 '부팅 직후 새 크롬' 이고,
       그 상태에서는 첫 셋업이 실패한다(FACTS 에 적혀 있던 사실이다).
  morning.ps1 -NoDaily 로 "18초 완주" 라고 보고했는데, 그 -NoDaily 가
  하필 오늘 실패한 부분을 건너뛰는 스위치였다.

그래서 이 스크립트는 타협하지 않는다
  - 전용 크롬을 재시작한다 (OS 재부팅 검증은 별도로 필요)
  - 09:00 에 도는 것과 **같은 진입점**(daily.py)을 쓴다
  - 발사 시각만 '지금+N분' 으로 바꾼다. 기본은 실제 결제창까지 진행한다
  - --partial-dry 는 주문 직전 부분 점검이며 전체 리허설 통과가 아니다
  - 리포트를 읽어 실제로 발사했는지 확인한다. '오류 없음' 은 통과가 아니다

사용:
  .venv/Scripts/python.exe dev/rehearse.py                 (day.ps1 의 노선을 쓴다)
  .venv/Scripts/python.exe dev/rehearse.py --route ICN --from FCO --minutes 9
"""
from __future__ import annotations
import argparse, json, os, re, shutil, subprocess, sys, time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from runtime import new_run, output_dir, validate_rehearsal, atomic_json

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dev-shots"
KST = timezone(timedelta(hours=9))


def log(m):
    print(f"[{datetime.now(KST).strftime('%H:%M:%S')}] {m}", flush=True)


def find_shell():
    """파워셸 실행 파일을 찾는다.

    PATH 만 믿으면 안 된다. pwsh 는 스토어 앱이라 PATH 에 있고 없고가 **부르는
    환경마다 다르다** - 파워셸에서 부르면 보이고 Git Bash 에서 부르면 안 보인다.
    09-07 에 그것 때문에 리허설이 두 번 죽었다.
    마지막 후보는 윈도우에 항상 있는 절대경로라 여기까지 오면 반드시 찾는다.
    """
    for c in ("pwsh", "powershell"):
        p = shutil.which(c)
        if p:
            return p
    root = os.environ.get("SystemRoot", r"C:\Windows")
    for p in (Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe",
              Path(r"C:\Program Files\PowerShell\7\pwsh.exe")):
        if p.exists():
            return str(p)
    return None


def run_quiet(cmd):
    """외부 도구를 부른다. 출력 인코딩 때문에 죽지 않게 한다.

    윈도우 콘솔 도구(taskkill 등)는 cp949 로 쓰는데 파이썬은 utf-8 로 읽는다.
    한글 한 글자에 UnicodeDecodeError 가 나서 리허설이 통째로 죽었다. (09-07)
    """
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def day_args() -> list[str]:
    """day.ps1 의 $DailyArgs 를 읽는다. 리허설과 실전이 다른 노선이면 의미가 없다."""
    try:
        txt = (ROOT / "dev" / "day.ps1").read_text(encoding="utf-8-sig")
        m = re.search(r"^\$DailyArgs\s*=\s*@\((.*?)\)", txt, re.M)
        return re.findall(r"'([^']*)'", m.group(1)) if m else []
    except Exception:
        return []


ROME_DAYS = {0, 2, 5}      # FACTS: 로마는 양방향 모두 월·수·토


def open_target(args: list[str]):
    """리허설이 쏠 출발일 = '이미 열려 있는' 가장 최신 날짜.

    08:20 에 도는 리허설은 오늘 09:00 에 열릴 날짜를 쓸 수 없다. 아직 없다.
    (09-04 아침에 이걸 놓칠 뻔했다 - 없는 날짜로 쏘면 리허설이 엉뚱하게 실패한다.)

    오픈 규칙: 출발일 = 실행일 + 360. 그러니 09:00 전이면 오늘+359 가 최신이고,
    09:00 이 지났으면 오늘+360 이 최신이다.

    로마(FCO)는 월·수·토만 뜨므로 그 날이 아니면 뒤로 물러선다. 운항 안 하는 날로
    쏘면 매크로가 정상적으로 실패하는데 리허설은 그걸 고장으로 읽는다.
    """
    now = datetime.now(KST)
    d = now.date() + timedelta(days=360 if now.hour >= 9 else 359)
    joined = " ".join(args).upper()
    if "FCO" in joined:
        for _ in range(7):
            if d.weekday() in ROME_DAYS:
                break
            d -= timedelta(days=1)
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=int, default=9, help="지금부터 몇 분 뒤에 발사할까")
    ap.add_argument("--route", default="")
    ap.add_argument("--from", dest="origin", default="")
    ap.add_argument("--cabin", default="일반석")
    ap.add_argument("--start", default="calendar", choices=["calendar", "departure"])
    ap.add_argument('--date', default='', help='리허설 목표 MM-DD, 생략하면 열린 날짜 자동 선택')
    ap.add_argument('--prepare-krw', action='store_true')
    ap.add_argument('--no-reload', action='store_true')
    ap.add_argument('--prepare-date', default='')
    ap.add_argument('--refresh-date', default='')
    ap.add_argument("--max-seconds", type=float, default=90)
    ap.add_argument("--partial-dry", action="store_true", help="주문 직전까지만 부분 점검; 전체 리허설 통과로 간주하지 않음")
    ap.add_argument("--no-watch", action="store_true", help="계측용 계정 없이 매크로 dry와 수동 네트워크 기록만 시험")
    ap.add_argument("--keep-browsers", action="store_true",
                    help="크롬을 죽이지 않는다. 차가운 상태가 아니게 되므로 권하지 않는다")
    a = ap.parse_args()
    if (a.prepare_krw or a.no_reload or a.prepare_date or a.refresh_date) and a.start != 'departure':
        ap.error('Fast rehearsal options require --start departure')
    mode = 'dry' if a.partial_dry else 'payment-window'

    args = ["--route", a.route] + (["--from", a.origin] if a.origin else []) \
        if a.route else day_args()
    log(f"리허설 노선: {' '.join(args) or '(자동)'}")

    # --- 1) 차가운 크롬. 이게 이 스크립트의 존재 이유다 ---
    if a.keep_browsers:
        log("크롬 유지 (차가운 상태 아님 - 실전과 다르다)")
    else:
        log("전용 크롬을 재시작합니다 (OS 재부팅 검증은 별도)")
        # text=True 만 주면 파이썬이 utf-8 로 읽는데 윈도우 콘솔 도구는 cp949 로 쓴다.
        # taskkill 의 한글 출력에서 UnicodeDecodeError 가 났다. (09-07)
        log("이 worktree의 9232/9233 프로필만 재시작합니다")

    shell = find_shell()
    if not shell:
        log("!! 파워셸을 찾지 못했다 - 크롬을 띄울 수 없다")
        return 1
    boot = run_quiet([shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "dev" / "astra_browsers.ps1")]
                     + ([] if a.keep_browsers else ["-Restart"])
                     + (["-Port", "9232"] if a.no_watch else []))
    if boot.returncode != 0:
        log("브라우저 준비 실패: " + boot.stderr[-500:])
        return 1

    # --- 2) 9시에 도는 것과 같은 진입점 ---
    identity = new_run()
    folder = output_dir()

    from ke_setup import nearest_future
    tgt = nearest_future(a.date) if a.date else open_target(args)
    fire = datetime.now(KST) + timedelta(minutes=a.minutes)
    log(f"발사 예정 {fire.strftime('%H:%M:%S')} (지금+{a.minutes}분) / 출발일 {tgt} / {mode}")
    cmd = [sys.executable, str(ROOT / "dev" / "daily.py")] + args + \
          ["--at", f"+{a.minutes * 60}s", "--setup-at", "+5s",
           "--date", tgt.strftime("%m-%d"), "--cabin", a.cabin, "--start", a.start, "--mode", mode]
    if a.no_watch:
        cmd.append("--no-watch")
    if a.prepare_krw: cmd.append('--prepare-krw')
    if a.no_reload: cmd.append('--no-reload')
    if a.prepare_date: cmd += ['--prepare-date', a.prepare_date]
    if a.refresh_date: cmd += ['--refresh-date', a.refresh_date]
    r = subprocess.run(cmd, capture_output=True, text=True,
                       timeout=(a.minutes + 8) * 60)
    print(r.stdout[-3000:])

    # Strict contract: no missing, empty, stale or partial result can pass.
    def read_result(name):
        try:
            value = json.loads((folder / name).read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}
    w, m = read_result("watch_seats.json"), read_result("autorun_report.json")
    notes = [f"매크로 {m.get('idx')}단계, {m.get('seconds')}초 / {m.get('why') or ''}"]
    if not a.no_watch:
        notes.append(f"계측기 {w.get('samples')}건")
    fails = validate_rehearsal(w, m, identity, a.max_seconds, require_watch=not a.no_watch, mode=mode)
    if r.returncode:
        fails.append(f"daily 비정상 종료: {r.returncode}")
    atomic_json(folder / "rehearsal_report.json", {"runId": identity, "ok": not fails,
                "coldBrowser": not a.keep_browsers, "cabin": a.cabin, "failures": fails,
                "partial": a.partial_dry,
                "coverage": "dry 6단계 부분 점검" if a.partial_dry else "실제 결제창 표시; 최종 결제 승인 제외"})
    print()
    print("=" * 64)
    if fails:
        print("  리허설 실패 - 이 상태로 9시를 맞으면 진다")
        for f in fails: print("   X " + f)
        for n in notes: print("   . " + n)
        print("=" * 64)
        return 1
    print("  부분 dry 점검 통과 (전체 리허설 아님)" if a.partial_dry else "  리허설 통과 (로그인 -> 발사 -> 실제 결제창 표시)")
    for n in notes: print("   O " + n)
    # 통과를 '다 된다' 로 읽지 않게, 무엇을 안 봤는지 매번 같이 찍는다.
    print("  * 여기까지만 봤다. 실전과 다른 점:")
    print("    - 09:00 이 아니라 이미 열린 날짜로 쐈다 - '경쟁' 은 재지 못한다.")
    print("    - 최종 결제 승인은 수행하지 않았다. 신규 개방일의 실전 경쟁은 별도 검증이다.")
    print(f"    - coldBrowser={not a.keep_browsers}; 결과: {folder}")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    sys.exit(main())
