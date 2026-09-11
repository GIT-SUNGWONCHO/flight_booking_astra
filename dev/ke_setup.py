"""셋업을 '시간이 남는 한 다시 해보는' 방식으로 돌린다.

왜 있나 (2026-09-04 에 하루를 잃고 만들었다)
  FACTS: 크롬을 갓 띄운 직후 첫 셋업은 자주 실패하고 두 번째에 된다.
  이 사실을 알고도 재시도를 preflight.py(점검기)에만 넣었다. 정작 09:00 에
  발사하는 autorun.py 와 watch_seats.py 는 한 번 해보고 그냥 죽었다.
  09-04 아침, PC 재부팅으로 크롬이 새것이 되자 둘 다 08:40 에 포기했다.
  사람이 08:46 에 손으로 고쳐도 프로세스는 이미 없었다.

  ※ 진짜 원인은 그 뒤에 따로 찾았다 - 로그인 직후 위젯이 현금 모드로 남는 것.
    setup.py 가 그 자리에서 고친다. 여기 재시도는 그래도 남겨 둔 안전망이다.

규칙
  - 한 번 실패했다고 하루를 버리지 않는다. 마감까지 남는 시간을 다 쓴다.
  - 실패 이유가 '로그인' 이면 사람이 필요하다. 더 해봐야 소용없으니 즉시 멈춘다.
  - 마감은 발사 시각보다 앞이어야 한다. 발사 직전까지 붙잡고 있으면 안 된다.
  - **실패한 시도의 로그를 버리지 않는다.** 09-04 에 왜 실패했는지 알 수 없어
    사람이 손으로 재현할 때까지 원인을 몰랐고, 그 사이 09:00 이 지나갔다.
"""
from __future__ import annotations
import json, subprocess, sys, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
LOGDIR = Path(__file__).resolve().parent.parent / "dev-shots"
NL = "\n"


def nearest_future(mmdd: str) -> date:
    """MM-DD 를 '앞으로 올 그 날짜' 로 읽는다.

    **여기 한 곳에만 둔다.** 예전엔 autorun 과 watch_seats 가 각자 계산했고,
    watch_seats 만 고쳤다가 09-07 에 autorun 이 그대로 터졌다.

    틀렸던 규칙: `올해 + (목표월 < 이번달 ? 1 : 0)`.
    2026-09-07 에 목표가 09-02(=2027-09-02)면 `9 < 9` 가 거짓이라 2026 을 골라
    **8개월 과거 날짜**를 잡는다. 달력이 2026년 9월을 그리고, 매크로는 2027년
    날짜를 찾다가 영원히 단계 0 에 머문다. 09-07 리허설이 세 번 다 이것으로 죽었다.
    """
    today = datetime.now(KST).date()
    mm, dd = (int(x) for x in mmdd.split("-"))
    for y in (today.year, today.year + 1, today.year + 2):
        try:
            d = date(y, mm, dd)
        except ValueError:
            continue
        if d > today:
            return d
    raise ValueError(f"날짜를 못 읽음: {mmdd}")


def run_setup(cmd, deadline: datetime, log=print, gap: float = 3.0, min_tries: int = 0):
    """setup.py 를 마감까지 반복 실행한다. 마지막 결과 dict 를 돌려준다.

    cmd       setup.py 실행 인자 리스트
    deadline  이 시각을 넘기면 더 시도하지 않는다 (보통 발사 90초 전)
    min_tries retained for callers; it never overrides the hard deadline.
    """
    st, n = {}, 0
    while True:
        left = (deadline - datetime.now(KST)).total_seconds()
        if left <= 0:
            return {"ok": False, "why": "준비 마감시간 초과", "attempts": n}
        n += 1
        out = ""
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=min(420, left))
            out = (r.stdout or "")
            if r.stderr:
                out += NL + "[stderr] " + r.stderr
            tail = (r.stdout or "").strip().splitlines()
            st = json.loads(tail[-1]) if tail else {}
            if not isinstance(st, dict) or r.returncode != 0:
                st = {"ok": False, "why": st.get("why", "setup 비정상 종료") if isinstance(st, dict) else "setup 결과 형식 오류"}
        except subprocess.TimeoutExpired as e:
            def decoded(value):
                return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')
            out = decoded(e.stdout) + NL + decoded(e.stderr)
            st = {"ok": False, "why": f"setup 제한시간 초과 ({min(420, left):.1f}초)"}
        except Exception as e:
            st = {"ok": False, "why": f"setup 실행 실패: {e}"[:90]}

        # 실패했으면 그 실행이 무슨 말을 했는지 통째로 남긴다.
        if not st.get("ok") and out:
            try:
                LOGDIR.mkdir(exist_ok=True)
                with (LOGDIR / "setup_failures.log").open("a", encoding="utf-8") as f:
                    f.write(NL + "===== " + datetime.now(KST).isoformat()
                            + "  시도 " + str(n) + NL)
                    f.write("  $ " + " ".join(str(c) for c in cmd) + NL)
                    f.write(out.rstrip() + NL)
            except Exception:
                pass
            for line in out.strip().splitlines()[-6:]:
                log("    | " + line.rstrip())

        if st.get("ok"):
            if datetime.now(KST) >= deadline:
                return {"ok": False, "why": "준비 완료가 마감시간을 넘김"}
            if n > 1:
                log(f"  셋업 {n}회째에 성공")
            return st

        why = st.get("why") or "알 수 없음"
        if "로그인" in why:
            log(f"  셋업 실패({why}) - 사람이 로그인해야 한다. 재시도 안 함")
            return st

        left = (deadline - datetime.now(KST)).total_seconds()
        if left <= 0:
            log(f"  셋업 {n}회 모두 실패({why}) - 마감")
            return st
        log(f"  셋업 {n}회 실패({why}) - 다시 (마감까지 {max(left, 0):.0f}초)")
        if gap:
            time.sleep(min(gap, max(0, left)))
