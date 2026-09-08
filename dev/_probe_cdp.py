"""진단용: 이 프로세스에서 루프백(127.0.0.1) '접속(connect)'이 되는지 본다.

이 PC 는 '모르는 프로그램이 붙으러 나가는 것'만 막고 '기다리는 것(listen)'은 허용한다.
그래서 dev 서버(listen)는 되는데 우리 테스트 도구(connect)는 막힌다.
관건: '누가 실행했는가'. 사용자가 직접 실행했을 때 CONNECT 가 OK 면 자동화가 가능하다.

무한정 멈추지 않도록 모든 접속에 타임아웃을 건다. 화면에 진행을 즉시 찍는다.
"""
import json, socket, sys, threading, time, urllib.request
from datetime import datetime
from pathlib import Path

def say(m):
    print(m, flush=True)

say("=== loopback(127.0.0.1) 접속 진단 시작 ===")
res = {"at": datetime.now().isoformat(), "argv": sys.argv[1:]}

# 1) BIND/LISTEN (기다리기) - 이건 보통 된다
srv = socket.socket()
try:
    srv.bind(("127.0.0.1", 0)); srv.listen(1)
    port = srv.getsockname()[1]
    res["listen"] = "OK"
    say(f"[1] LISTEN(기다리기)      : OK  (port {port})")
except Exception as e:
    res["listen"] = f"FAIL: {e}"; port = None
    say(f"[1] LISTEN(기다리기)      : FAIL  {e}")

# 2) CONNECT to self (자기 리스너에 붙기) - 진짜 급소, 타임아웃 3초
if port:
    def accept():
        try: srv.settimeout(4); c, _ = srv.accept(); c.close()
        except Exception: pass
    threading.Thread(target=accept, daemon=True).start()
    c = socket.socket(); c.settimeout(3); t = time.time()
    try:
        c.connect(("127.0.0.1", port))
        res["connect_self"] = "OK"
        say(f"[2] CONNECT(붙기)         : OK  ({time.time()-t:.1f}s)  <- 자동화 가능!")
    except Exception as e:
        res["connect_self"] = f"FAIL: {e}"
        say(f"[2] CONNECT(붙기)         : FAIL  {e}  ({time.time()-t:.1f}s)  <- 이게 막혀서 도구가 멈춘다")
    finally:
        c.close()

# 3) 크롬 CDP(9232) 붙기 - 크롬이 떠 있으면
c = socket.socket(); c.settimeout(3)
try:
    c.connect(("127.0.0.1", 9232)); c.close()
    body = urllib.request.urlopen("http://127.0.0.1:9232/json/version", timeout=3).read().decode()
    res["cdp"] = json.loads(body).get("Browser", "OK")
    say(f"[3] 크롬 CDP(9232)        : OK  ({res['cdp']})")
except Exception as e:
    res["cdp"] = f"FAIL: {e}"
    say(f"[3] 크롬 CDP(9232)        : FAIL  {e}")

out = Path(__file__).resolve().parent.parent / "dev-shots" / "_probe_cdp.json"
out.parent.mkdir(exist_ok=True)
out.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
say("")
say("결과 저장: dev-shots/_probe_cdp.json")
