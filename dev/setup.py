"""개발/실전 준비: 어떤 상태에서든 '마일리지 예매 달력 화면' 까지 알아서 데려간다.

사용:  .venv/bin/python dev/setup.py [도착지코드] [--tab N] [--date YYYY-MM-DD]
        예) dev/setup.py CDG --date 2027-08-25
            dev/setup.py FCO --tab 1 --date 2027-08-25   (두 번째 탭을 그 노선으로)

--date 는 달력이 그 달을 보여주게 하려는 것이다. 달력은 고른 날 언저리를 보여주므로
목표일과 먼 날을 고르면 엉뚱한 달의 달력이 뜬다.

노선마다 탭을 따로 두면 한 브라우저로 여러 노선을 동시에 준비할 수 있다.

돌아오는 값(마지막 줄 JSON): {"ok":bool, "url":..., "why":...}
  ok=false 이고 why="로그인 필요" 면 사람이 로그인해야 한다 (비밀번호는 다루지 않는다).
"""
from __future__ import annotations
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
USER = ROOT / "userscript" / "ke-award-macro.user.js"
CDP = "http://localhost:9232"

# 로그인 방식(사용자 확정 2026-09-12). 기본은 본인 네이버 연동이다.
# skypass 복귀는 9232 에만 적용한다 - 9233 계측과 9242 본인 예매는 언제나 본인 네이버다.
# 9242 는 2026-09-22 사용자 요청으로 '본인 계정 두 번째 예매'에 쓴다(프로필 .api-profile).
PROFILE_BY_PORT = {9232: ".debug-profile", 9233: ".debug-profile2", 9242: ".api-profile"}


def login_mode(env, port):
    return "skypass" if ((env.get("KE_LOGIN_MODE") or "").strip().lower() == "skypass"
                         and port == 9232) else "naver"


def mode_marker(port):
    """이 프로필의 현재 세션을 어느 방식으로 만들었는지 적어 둔다.

    살아 있는 세션만 보고는 어느 계정인지 알 수 없다. 표시가 정책과 다르면
    예전 계정으로 예매될 수 있으므로 9232 는 준비를 중단한다.
    """
    name = PROFILE_BY_PORT.get(port)
    return (ROOT / name / ".ke-login-mode") if name else None


def read_mode_marker(port):
    path = mode_marker(port)
    try:
        return path.read_text(encoding="utf-8").strip() if path else ""
    except OSError:
        return ""


def write_mode_marker(port, mode):
    """성공 여부를 돌려준다. 삼키면 확인 명령이 거짓 성공을 보고한다."""
    path = mode_marker(port)
    if path is None or not path.parent.is_dir():
        return False
    try:
        path.write_text(mode + "\n", encoding="utf-8")
        return True
    except OSError as exc:
        log(f"로그인 방식 표시 저장 실패: {exc}")
        return False
CAL = "/booking/calendar-fare-bonus"


def log(m): print(f"  {m}", flush=True)


def load_env() -> dict:
    """저장소 루트의 .env 를 읽는다. 없으면 빈 dict.

    비밀번호는 여기에만 있고 저장소에는 절대 안 들어간다(.gitignore).
    값을 로그에 찍지 않는다 - 있다/없다만 말한다.
    """
    env = {}
    f = ROOT / ".env"
    if not f.exists():
        return env
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        if v:
            env[k.strip()] = v
    return env


def login_naver(page, inject) -> bool:
    """네이버 연동으로 로그인한다 (9233 본인 계정).

    네이버 쪽 세션(NID_AUT/NID_SES)이 살아 있으면 버튼 두 번으로 끝난다 -
    비밀번호를 치는 게 아니다. 네이버가 비밀번호를 물으면 사람이 해야 한다.
    """
    page.evaluate("""() => {
      const U = window.KE_UTIL;
      const b = U.candidates(document).find(e => U.visible(e) && /^로그인$/.test(U.label(e)));
      if (b) U.fireClick(b);
    }""")
    page.wait_for_timeout(6000)
    inject()
    page.evaluate("""() => {
      const U = window.KE_UTIL;
      const b = U.candidates(document).find(e => U.visible(e) && /네이버|NAVER/i.test(U.label(e)));
      if (b) U.fireClick(b);
    }""")
    for _ in range(16):
        page.wait_for_timeout(2500)
        try:
            # 대한항공 /login 페이지에도 자체 아이디·비밀번호 칸이 있다.
            # 그걸 보고 멈추면 네이버로 넘어가기도 전에 포기한다 (실측:
            # 그래서 자동 로그인이 실패했다). 네이버 화면에서 물을 때만 멈춘다.
            if "naver" in page.url and page.evaluate("""() => {
              const es = document.querySelectorAll('input[type=password]');
              for (const e of es) { const r = e.getBoundingClientRect();
                if (r.width > 1 && r.height > 1) return true; }
              return false;
            }"""):
                log("네이버가 비밀번호를 요구합니다 - 사람이 로그인해야 함")
                return False
            if "koreanair" in page.url:
                inject()
                if page.evaluate("""() => {
                  const U = window.KE_UTIL;
                  return U.candidates(document).some(e => U.visible(e) && /로그아웃/.test(U.label(e)));
                }"""):
                    return True
        except Exception:
            pass
    return False


def login_idpw(page, inject, user: str, pw: str, tab: str = "") -> bool:
    """대한항공 자체 로그인 (9232 와이프 계정).

    **로그인 화면에는 탭이 두 개다** - `아이디` / `스카이패스 번호`.
    기본은 `아이디` 탭이라, 스카이패스 번호를 그냥 넣으면
    "일치하는 회원정보가 없습니다" 가 뜬다. (09-04 실측으로 확인)

    탭은 `button[role=tab]` 이고 고른 것에 `aria-selected=true` 와 `-active` 가 붙는다.
    입력칸의 id 는 매번 바뀌는 해시(textinput-051db3f4...)라 잡으면 안 된다.
    보이는 text/password 칸이 각각 하나뿐이라 타입으로 잡는다.

    tab  "스카이패스" 또는 "아이디". 비우면 값이 숫자뿐일 때 스카이패스로 본다.
    """
    if "/login" not in page.url:
        page.goto("https://www.koreanair.com/login",
                  wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        inject()

    # A new profile has a cookie-consent overlay; old warm profiles did not.
    # The label and submit class were observed on the isolated live login page.
    consent = page.get_by_text("필수 쿠키만 허용", exact=True)
    if consent.count() and consent.first.is_visible():
        consent.first.click(timeout=10000)
    want = tab or ("스카이패스" if user.isdigit() else "아이디")

    # 탭이 그려질 때까지 기다린다. 로그인 페이지로 **리다이렉트된 직후**에는 탭이
    # 아직 없어서 'tab못찾음:' (콜론 뒤가 빈 문자열)이 뜨고, 그대로 기본 '아이디'
    # 탭에 스카이패스 번호를 넣어 "일치하는 회원정보가 없습니다" 로 끝난다.
    # 09-08 아침 자동 실행이 이것으로 죽었다 - 어제는 페이지가 이미 떠 있어 통했다.
    picked = None
    for _ in range(20):
        picked = page.evaluate("""(want) => {
          const tabs = [...document.querySelectorAll('button[role=tab]')]
            .filter(e => { const r = e.getBoundingClientRect(); return r.width > 1; });
          if (!tabs.length) return null;                       // 아직 안 그려짐
          const hit = tabs.find(e => (e.innerText || '').replace(/\\s+/g, ' ').includes(want));
          if (!hit) return 'tab못찾음:' + tabs.map(e => (e.innerText||'').trim()).join('/');
          if (hit.getAttribute('aria-selected') === 'true') return 'already:' + want;
          hit.click();
          return 'clicked:' + want;
        }""", want)
        if picked:
            break
        page.wait_for_timeout(700)
    log(f"로그인 탭: {picked or '탭이 끝내 안 나타남'}")
    if not picked or picked.startswith("tab못찾음"):
        # 여기서 멈춘다. 엉뚱한 탭에 번호를 넣으면 사이트가 '회원정보 없음' 으로
        # 답하고, 그 문구만 보면 비밀번호가 틀린 줄 안다. 09-08 에 그렇게 헤맸다.
        return False
    page.wait_for_timeout(1500)   # 탭을 바꾸면 입력칸이 새로 그려진다(id 도 바뀐다)

    # 고른 탭이 실제로 선택됐는지 확인한다. 클릭이 먹지 않았는데 진행하면 같은 일이 난다.
    sel = page.evaluate("""() => {
      const t = [...document.querySelectorAll('button[role=tab]')]
        .find(e => e.getAttribute('aria-selected') === 'true');
      return t ? (t.innerText || '').replace(/\\s+/g, ' ').trim() : '';
    }""")
    log(f"  선택된 탭: {sel or '(없음)'}")
    if want not in sel:
        log(f"  탭이 '{want}' 로 안 바뀌었다 - 중단")
        return False

    try:
        page.fill("input[type=text]:visible", user, timeout=15000)
        page.fill("input[type=password]:visible", pw, timeout=15000)
        page.locator("input[type=password]:visible").blur()
        if (page.locator("input[type=text]:visible").input_value() != user
                or page.locator("input[type=password]:visible").input_value() != pw):
            log("로그인 폼 재렌더로 입력값이 유지되지 않았습니다")
            return False
    except Exception as e:
        log(f"로그인 입력칸을 채우지 못함: {str(e)[:60]}")
        return False
    page.locator("button.login__submit-act").click(timeout=10000)
    for _ in range(16):
        page.wait_for_timeout(1500)
        try:
            if "/login" not in page.url:
                inject()
                if page.evaluate("""() => {
                  const U = window.KE_UTIL;
                  return U.candidates(document).some(e => U.visible(e) && /로그아웃/.test(U.label(e)));
                }"""):
                    return True
        except Exception:
            pass
    # 왜 안 됐는지 화면이 말해 준다. 이걸 안 남기면 사람이 손으로 재현할 때까지
    # 원인을 모른다 - 09-04 에 그렇게 09:00 이 지나갔다. 비밀번호는 찍지 않는다.
    try:
        msg = page.evaluate("""() => {
          const t = document.body.innerText || '';
          const hits = t.split('\\n').map(s => s.trim()).filter(s =>
            s && /올바르지|일치하지|확인해|잠[겨금]|정지|오류|실패|다시 시도|보안문자|인증/.test(s));
          return hits.slice(0, 4);
        }""")
        log(f"로그인 화면이 말한 것: {msg or '(별다른 안내 없음)'}")
    except Exception:
        pass
    return False


def goal_now(url: str, departure: bool) -> bool:
    return ("/booking/select-award-flight" in url) if departure else (CAL in url)


def main() -> int:
    from playwright.sync_api import sync_playwright
    args = sys.argv[1:]
    tab = 0
    if "--tab" in args:
        i = args.index("--tab")
        tab = int(args[i + 1])
        del args[i:i + 2]
    want_date = ""
    if "--date" in args:
        i = args.index("--date")
        want_date = args[i + 1]
        del args[i:i + 2]
    # 붙을 크롬. 계측기는 2번 크롬(9233)을 쓴다 - 실전 예약(9232)과 프로필을 나눠야
    # 쿠키·localStorage 가 안 섞이고 9시에 둘을 동시에 돌릴 수 있다.
    cdp = CDP
    if "--port" in args:
        i = args.index("--port")
        cdp = f"http://localhost:{int(args[i + 1])}"
        del args[i:i + 2]
    # 사람이 직접 로그인한 세션은 프로그램이 계정을 알 수 없다. 사용자가 어느
    # 방식으로 들어갔는지 여기서 명시하면 표시만 남기고 끝낸다. 브라우저에
    # 접속하지 않고 로그인도 하지 않는다.
    if "--confirm-login" in args:
        i = args.index("--confirm-login")
        confirmed = args[i + 1].strip().lower()
        del args[i:i + 2]
        try:
            confirm_port = int(cdp.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            confirm_port = 9232
        if confirmed not in ("naver", "skypass"):
            print(json.dumps({"ok": False, "why": "로그인 방식은 naver 또는 skypass"},
                             ensure_ascii=False))
            return 2
        if mode_marker(confirm_port) is None:
            print(json.dumps({"ok": False, "why": f"포트 {confirm_port} 는 표시 대상이 아니다"},
                             ensure_ascii=False))
            return 2
        if not mode_marker(confirm_port).parent.is_dir():
            print(json.dumps({"ok": False, "why": f"프로필 폴더가 없다: {mode_marker(confirm_port).parent}"},
                             ensure_ascii=False))
            return 2
        if not write_mode_marker(confirm_port, confirmed):
            print(json.dumps({"ok": False, "port": confirm_port,
                              "why": f"로그인 방식 표시를 저장하지 못했다: {mode_marker(confirm_port)}"},
                             ensure_ascii=False))
            return 2
        print(json.dumps({"ok": True, "port": confirm_port, "loginMode": confirmed,
                          "why": "사용자 확인으로 로그인 방식 표시만 기록. 로그인을 수행하지 않았다"},
                         ensure_ascii=False))
        return 0
    # 출발지. 유럽발(로마->인천 등) 목표가 생겨서 필요해졌다 - 지금까지는 늘 SEL 이었다.
    want_from = ""
    if "--from" in args:
        i = args.index("--from")
        want_from = args[i + 1].upper()
        del args[i:i + 2]
    departure = "--departure" in args
    if departure:
        args.remove("--departure")
    want = (args[0].upper() if args else "")

    with sync_playwright() as pw:
        try:
            b = pw.chromium.connect_over_cdp(cdp)
        except Exception as e:
            print(json.dumps({"ok": False, "why": f"브라우저 없음 ({e})"[:80]}, ensure_ascii=False))
            return 1
        ctx = b.contexts[0]
        from browser_identity import mark_context
        mark_context(ctx, int(cdp.rsplit(':',1)[1]))
        js = USER.read_text(encoding="utf-8")
        ctx.add_init_script("if (location.hostname === 'www.koreanair.com') {\n" + js + "\n}")
        pages = [p for p in ctx.pages if "koreanair" in p.url]
        while len(pages) <= tab:            # 원하는 번째 탭이 없으면 만든다
            pages.append(ctx.new_page())
        page = pages[tab]
        try: page.bring_to_front()          # 뒤에 있으면 브라우저가 타이머를 늦춘다
        except Exception: pass

        def inject():
            try: page.evaluate(js)
            except Exception: pass

        did_login = False

        # 로그인 정책 대조를 먼저 한다. 아래 달력 조기 반환이 이 검사를 건너뛰면
        # 이전 계정 세션이 그대로 준비 완료로 인정된다.
        env = load_env()
        port = 9232
        try:
            port = int(cdp.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            pass
        mode = login_mode(env, port)
        # 9232 는 표시가 현재 정책과 같을 때만 아래 조기 반환을 허용한다.
        # 표시가 없거나 다르면 정상 흐름으로 내려가 로그인 상태를 실제로 확인한다.
        # (로그아웃 상태면 그대로 정책대로 재로그인하고 표시를 새로 쓴다.)
        marker_ok = (port != 9232) or (read_mode_marker(port) == mode)

        # 이미 달력이면 끝 (도착지를 바꿔야 하면 그대로 진행한다)
        if goal_now(page.url, departure) and not want and marker_ok:
            inject()
            log(f"이미 달력 화면: {page.url[:60]}")
            print(json.dumps({"ok": True, "url": page.url, "why": "이미 달력"}, ensure_ascii=False))
            return 0

        log("홈으로 이동")
        page.goto("https://www.koreanair.com/kr/ko", wait_until="load", timeout=60000)
        page.wait_for_timeout(11000)
        inject()
        # 이미 로그인된 계측 프로필에도 동의 창이 뜬다. 로그인 함수 안에서만
        # 처리하면 준비는 통과하지만 발사 때 달력 버튼을 가린다.
        consent = page.get_by_text('필수 쿠키만 허용', exact=True)
        if consent.count() and consent.first.is_visible():
            consent.first.evaluate('(button) => button.click()')
            page.wait_for_timeout(500)

        # 로그인 확인. 헤더가 shadow DOM 안이고 늦게 그려져서, 한 번만 보고 판단하면
        # 멀쩡히 로그인돼 있는데도 '로그인 필요' 로 읽는다 (실측 2026-08-30 01:25:
        # 같은 세션을 두 번 봤는데 한 번은 로그아웃, 한 번은 로그인으로 나왔다).
        # 09:00 에 이걸로 중단되면 전부 헛일이므로, 나타날 때까지 기다렸다 판단한다.
        logged = False
        for _ in range(25):
            st = page.evaluate("""() => {
              const U = window.KE_UTIL;
              const c = U.candidates(document).filter(e => U.visible(e));
              return {out: c.some(e => /로그아웃/.test(U.label(e))),
                      inb: c.some(e => /^로그인$/.test(U.label(e)))};
            }""")
            if st["out"]:
                logged = True
                break
            page.wait_for_timeout(1000)
            inject()
        if logged and port == 9232:
            seen = read_mode_marker(port)
            if seen != mode:
                # 살아 있는 세션이 어느 계정인지는 화면만 보고 알 수 없다.
                # 로그아웃 상태였다면 여기 오지 않고 아래에서 정책대로 재로그인한다.
                where = f"'{seen}' 방식" if seen else "기록 없음"
                log(f"9232 세션 표시가 {where} 이고 현재 정책은 '{mode}' 다 - 사용자 확인 필요")
                print(json.dumps({"ok": False, "url": page.url,
                    "why": f"로그인 확인 필요: 현재 9232 세션이 {where} 인데 정책은 '{mode}' 다. "
                           f"그 계정이 맞으면 `dev/setup.py --port 9232 --confirm-login {mode}` 로 확인하고, "
                           f"아니면 사이트에서 로그아웃한 뒤 다시 준비한다"}, ensure_ascii=False))
                return 2
        if not logged:
            # 세션이 만료됐으면 네이버 연동으로 들어간다. 네이버 쪽 세션이 살아
            # 있으면 버튼 두 번으로 끝난다 - 비밀번호를 치는 게 아니다.
            # 비밀번호 입력칸이 뜨면 거기서 멈춘다. 그건 사람이 해야 한다.
            #
            # 로그인 방식(사용자 확정 2026-09-12): 9232·9233 모두 기본은 본인 네이버
            # 연동이다. 이전에는 9232만 와이프 스카이패스 아이디/비밀번호였고 네이버로
            # 넘어가지 못하게 막아 두었다. 그 제한을 사용자 지시로 해제했다.
            # **결과: 9232 도 본인 계정으로 로그인된다. 와이프 마일리지로 예매하려면
            # KE_LOGIN_MODE=skypass 로 되돌리고 .env 자격정보가 있어야 한다.**
            use_idpw = (mode == "skypass") and env.get("KE_SKYPASS_ID") and env.get("KE_SKYPASS_PW")
            if mode == "skypass" and not use_idpw:
                print(json.dumps({"ok": False, "why": "로그인 필요: KE_LOGIN_MODE=skypass 인데 자격정보 누락; 다른 로그인 방식으로 전환하지 않음"}, ensure_ascii=False))
                return 2
            if use_idpw:
                log("로그아웃 상태 - 스카이패스 아이디/비밀번호로 로그인 시도 (.env)")
                did_login = True
                if login_idpw(page, inject, env["KE_SKYPASS_ID"], env["KE_SKYPASS_PW"],
                              env.get("KE_LOGIN_TAB", "")):
                    logged = True
                    write_mode_marker(port, "skypass")
                    log("스카이패스 로그인 성공")
                else:
                    # 명시적으로 스카이패스를 고른 실행이므로 네이버로 넘어가지 않는다.
                    # 계정이 다르고, 화면이 바뀌면 실패 이유를 잃는다.
                    log("스카이패스 로그인 실패 - 네이버로 넘어가지 않는다(계정이 다르다)")
                    print(json.dumps({"ok": False, "url": page.url,
                                      "why": "로그인 필요: 스카이패스 로그인 실패"},
                                     ensure_ascii=False))
                    return 2

            if not logged:
                log(f"로그아웃 상태 - 네이버 연동으로 로그인 시도 (포트 {9232 if '9232' in cdp else 9233}, 본인 계정)")
                did_login = True
                if login_naver(page, inject):
                    logged = True
                    write_mode_marker(port, "naver")
                    log("네이버 연동 로그인 성공")
        if not logged:
            log("로그인 안 됨")
            print(json.dumps({"ok": False, "url": page.url, "why": "로그인 필요"}, ensure_ascii=False))
            return 2
        log("로그인 확인됨")

        if did_login:
            # 로그인 직후의 위젯은 '마일리지 예매' 를 눌러도 붙지 않는다.
            # click 은 요소를 찾아 누르므로 '성공' 이라고 찍히는데, 검색은 현금
            # 달력(/booking/calendar-fare)으로 간다. 좌석등급도 일반석, 값도 원화다.
            #
            # 09-04 실측으로 확정: 완전히 새 크롬에서
            #   1차(로그인 있음) -> calendar-fare      실패
            #   2차(로그인 없음) -> calendar-fare-bonus 성공
            # 두 실행의 차이는 이 로그인 단계 하나뿐이었다.
            #
            # 재부팅한 아침에만 로그인이 필요하므로, 정확히 실전에서만 터졌다.
            # 09-04 09:00 을 이것으로 통째로 잃었다.
            log("로그인 직후라 홈을 다시 연다 (위젯이 현금 모드로 남는다)")
            page.goto("https://www.koreanair.com/kr/ko",
                      wait_until="load", timeout=60000)
            page.wait_for_timeout(8000)
            inject()

        # 위젯의 토글들은 클릭 가능 목록에 안 걸리는 SPAN/LABEL 이라 직접 훑는다.
        CLICK_TEXT = """(txt) => {
          const U = window.KE_UTIL;
          let hit = null;
          const walk = (root, d) => {
            if (d > 12 || hit) return;
            let els; try { els = root.querySelectorAll('*'); } catch (e) { return; }
            for (const e of els) {
              if (hit) return;
              const t = (e.innerText||e.textContent||'').replace(/\\s+/g,' ').trim();
              if (t === txt && e.children.length === 0 && U.visible(e)) { hit = e; return; }
              if (e.shadowRoot) walk(e.shadowRoot, d+1);
            }
          };
          walk(document, 0);
          if (!hit) return null;
          U.fireClick(hit);
          return true;
        }"""

        def click_text(txt):
            return page.evaluate(CLICK_TEXT, txt)

        log(f"마일리지 예매 전환: {'성공' if click_text('마일리지 예매') else '못 찾음'}")
        page.wait_for_timeout(2500)

        # 편도. 왕복이면 오는 날까지 넣어야 검색이 넘어가지 않는다
        # (실측: 왕복 상태에서 가는 날만 고르면 검색이 날짜 선택기만 다시 연다).
        log(f"편도 전환: {'성공' if click_text('편도') else '못 찾음'}")
        page.wait_for_timeout(2000)

        # 가까운 날짜 함께 조회 - 이게 달력 화면과 조회 화면을 가르는 스위치다.
        # 켜면 검색이 달력(calendar-fare-bonus)으로, 끄면 조회(select-award-flight)로 간다.
        # 우리는 달력에서 그날 새로 열린 날짜를 찾아야 하므로 켜야 한다.
        # 토글이라 이미 켜져 있는데 또 누르면 꺼진다 - 실측에서 그 바람에 조회 화면으로
        # 튕겼다. 상태를 보고 꺼져 있을 때만 누른다.
        # 조회 모드는 이 체크박스를 꺼야 검색이 조회 화면으로 간다 (켜면 달력으로 감).
        flex = page.evaluate("""(wantOn) => {
          const U = window.KE_UTIL;
          /* 체크박스 자체에는 글자가 없다. '가까운 날짜 함께 조회' 라고 쓰인 라벨을 먼저
           * 찾고, 거기서 for=/조상 순으로 실제 input 을 찾아간다. */
          let lab = null;
          const walk = (root, d) => {
            if (d > 12 || lab) return;
            let els; try { els = root.querySelectorAll('*'); } catch (e) { return; }
            for (const e of els) {
              if (lab) return;
              const t = (e.innerText || e.textContent || '').replace(/\\s+/g, ' ').trim();
              if (t === '가까운 날짜 함께 조회' && e.children.length === 0 && U.visible(e)) { lab = e; return; }
              if (e.shadowRoot) walk(e.shadowRoot, d + 1);
            }
          };
          walk(document, 0);
          if (!lab) return 'notfound';

          /* 진짜 input 은 <kds-checkbox> 의 shadow root 안에 있다. 라이트 DOM 만 뒤지면
           * 못 찾아서 상태를 모른 채 누르게 되고, 이미 켜져 있으면 꺼버린다
           * (실측: 그 바람에 달력 대신 조회 화면으로 갔다). 조상들의 shadow 까지 본다. */
          let box = null, n = lab;
          for (let i = 0; i < 6 && !box && n; i++) {
            if (n.shadowRoot) {
              try { box = n.shadowRoot.querySelector('input[type=checkbox]'); } catch (e) {}
            }
            if (!box && n.querySelector) {
              try { box = n.querySelector('input[type=checkbox]'); } catch (e) {}
            }
            n = n.parentElement;
          }
          if (!box) return 'input못찾음';
          if (!!box.checked === !!wantOn) return 'already';
          U.fireClick(box);
          return box.checked ? 'on' : 'off';
        }""", not departure)
        log(f"가까운 날짜 함께 조회: {flex}")
        page.wait_for_timeout(1500)

        # 출발지/도착지 공용. 유럽발 목표(로마->인천)가 생겨 출발지도 바꿔야 한다.
        # order1 = 출발지, order3 = 도착지. 값이 있으면 "도착지 CDG 파리", 비어 있으면
        # "To 도착지" 로 라벨이 바뀌므로 라벨이 아니라 order 클래스로 잡는다.
        def set_fromto(order, code, name):
            log(f"{name} {code} 로 변경 시도")
            page.evaluate("""(o) => {
              const U = window.KE_UTIL;
              const btn = U.candidates(document).find(e => U.visible(e)
                && /ui-fromto__button/.test((e.className||'').toString())
                && new RegExp('-order' + o + '(\\\\s|$)').test((e.className||'').toString()));
              if (btn) U.fireClick(btn);
            }""", order)
            page.wait_for_timeout(2500)

            # 선택기는 '최근 검색' 만 보여준다. 처음 가는 도시는 목록에 없으므로
            # '도시, 공항' 검색칸에 코드를 쳐 넣어야 후보가 나온다.
            typed = page.evaluate("""(code) => {
              const U = window.KE_UTIL;
              let box = null;
              const walk = (root, d) => {
                if (d > 10 || box) return;
                let els; try { els = root.querySelectorAll('input[type=text]'); } catch (e) { els = []; }
                for (const e of els) {
                  if (U.visible(e) && /도시|공항/.test(e.placeholder || '')) { box = e; return; }
                }
                let all; try { all = root.querySelectorAll('*'); } catch (e) { return; }
                for (const e of all) { if (box) return; if (e.shadowRoot) walk(e.shadowRoot, d + 1); }
              };
              walk(document, 0);
              if (!box) return false;
              box.focus();
              box.value = code;
              box.dispatchEvent(new Event('input', { bubbles: true }));
              box.dispatchEvent(new Event('change', { bubbles: true }));
              return true;
            }""", code)
            if typed:
                log("  검색칸에 코드 입력")
                page.wait_for_timeout(2500)

            # 후보 고르기. 자동완성 항목을 '먼저' 본다.
            #   실측(2026-09-02): 코드가 든 아무 요소나 고르면 "여정 1 출발지 SEL -
            #   도착지 FCO 로마" 같은 여정 요약을 눌러버려 출발지가 안 바뀌었다.
            #   자동완성 항목은 도시명과 코드가 다른 요소로 쪼개져 있어(예: <em>FCO</em>)
            #   항목 행까지 올라가 행 전체 글자로 맞춘다.
            #   자동완성이 없으면 최근검색 목록으로 떨어진다 - 둘 다 없으면 아래에서 멈춘다.
            ok = page.evaluate("""(code) => {
              const U = window.KE_UTIL;
              let row = null;
              const walk = (root, d) => {
                if (d > 12 || row) return;
                let els; try { els = root.querySelectorAll('*'); } catch (e) { return; }
                for (const e of els) {
                  if (row) return;
                  if (/ui-autocomplete__option/.test((e.className||'').toString())) {
                    let n = e;
                    for (let i = 0; i < 4 && n; i++) {
                      const t = (n.innerText || n.textContent || '').replace(/\\s+/g,' ');
                      if (t.indexOf(code) !== -1 && U.visible(n)) { row = n; return; }
                      n = n.parentElement;
                    }
                  }
                  if (e.shadowRoot) walk(e.shadowRoot, d + 1);
                }
              };
              walk(document, 0);
              if (row) { U.fireClick(row); return (row.innerText||'').replace(/\\s+/g,' ').slice(0,34); }

              const hit = U.candidates(document).find(e => U.visible(e)
                && U.label(e).indexOf(code) !== -1
                && !/ui-fromto__button/.test((e.className||'').toString())
                && !/여정/.test(U.label(e)));
              if (hit) { U.fireClick(hit); return U.label(hit).replace(/\\s+/g,' ').slice(0,34); }
              return null;
            }""", code)
            page.wait_for_timeout(2500)
            now = page.evaluate("""(o) => {
              const U = window.KE_UTIL;
              const b = U.candidates(document).find(e => U.visible(e)
                && /ui-fromto__button/.test((e.className||'').toString())
                && new RegExp('-order' + o + '(\\\\s|$)').test((e.className||'').toString()));
              return b ? U.label(b).replace(/\\s+/g,' ').slice(0,30) : null;
            }""", order)
            log(f"  {name} 선택: {ok or '후보 못 찾음'} -> 현재 {now}")
            # 진짜 안전장치: 눌렀다고 믿지 않고 칸이 실제로 바뀌었는지 본다.
            return (bool(now) and code in now), now

        def read_fromto(order):
            return page.evaluate("""(o) => {
              const U = window.KE_UTIL;
              const b = U.candidates(document).find(e => U.visible(e)
                && /ui-fromto__button/.test((e.className||'').toString())
                && new RegExp('-order' + o + '(\\\\s|$)').test((e.className||'').toString()));
              return b ? U.label(b).replace(/\\s+/g,' ').slice(0,30) : null;
            }""", order)

        # 출발지를 먼저 바꾼다 (도착지보다 먼저여야 목록이 노선에 맞게 나온다).
        #
        # --from 이 없으면 SEL 이 기본이다. 그냥 두면 안 된다 - 유럽발(로마->인천)을 한 번
        # 쓰고 나면 위젯에 FCO 가 남아, 다음 실행이 조용히 'FCO -> CDG' 같은 엉뚱한 노선으로
        # 돈다. 지금 값이 이미 맞으면 건드리지 않는다(불필요한 8초를 아낀다).
        target_from = want_from or "SEL"
        cur_from = read_fromto(1)
        if not cur_from or target_from not in cur_from:
            log(f"출발지 정정 필요: 현재 '{cur_from}' -> {target_from}")
            good, now = set_fromto(1, target_from, "출발지")
            if not good:
                print(json.dumps({"ok": False, "url": page.url,
                                  "why": f"출발지를 {target_from} 로 못 바꿈 (현재: {now})"},
                                 ensure_ascii=False))
                return 6
        else:
            log(f"출발지 그대로: {cur_from}")

        if want:
            good, now = set_fromto(3, want, "도착지")
            if not good:
                print(json.dumps({"ok": False, "url": page.url,
                                  "why": f"도착지를 {want} 로 못 바꿈 (현재: {now})"},
                                 ensure_ascii=False))
                return 6

        # 출발일. 아무 날짜나 고르면 안 된다 - 달력 화면은 고른 날 언저리를 보여주므로,
        # 2026년 9월을 고르면 2027년 8월 달력이 아니라 2026년 9월 달력이 뜬다.
        # 목표일이 있으면 그 달까지 이동해서 그 날을 고른다.
        # 날짜 버튼 클래스에 -small / -large 가 창 크기에 따라 다르게 붙는다.
        # 크기에 기대면 어떤 창에서는 아예 못 찾는다 (실측: -small 로만 찾다가
        # -large 로 렌더된 창에서 날짜를 못 골라 준비가 통째로 실패했다).
        # 크기 표시는 무시하고 '출발일' 이라고 쓰인 버튼을 찾는다.
        DATEBTN = """() => {
          const U = window.KE_UTIL;
          const d = U.candidates(document).find(e => U.visible(e)
            && /ui-booking-tool__button/.test((e.className||'').toString())
            && /^출발일/.test(U.label(e)));
          return d ? U.label(d).replace(/\\s+/g,' ').slice(0,34) : null;
        }"""
        cur = page.evaluate(DATEBTN)
        need_date = bool(want_date) or (cur and "가는 날" in cur)
        if need_date:
            log(f"출발일 설정: 목표 {want_date or '(아무 날짜)'} / 현재 '{cur}'")
            page.evaluate("""() => {
              const U = window.KE_UTIL;
              const d = U.candidates(document).find(e => U.visible(e)
                && /ui-booking-tool__button/.test((e.className||'').toString())
                && /^출발일/.test(U.label(e)));
              if (d) U.fireClick(d);
            }""")
            page.wait_for_timeout(2500)

            if want_date:
                y, mo, dy = want_date.split("-")
                head = f"{int(y)}년 {int(mo)}월"
                # 화면에 보이는 달은 ui-datepicker__calendar-month-text 에 적혀 있다
                # (본문 전체에서 정규식으로 긁으면 다른 글자에 걸려 엉뚱한 달로 읽는다).
                MONTHS = """() => {
                  const U = window.KE_UTIL;
                  const out = [];
                  const walk = (root, d) => {
                    if (d > 10) return;
                    let els; try { els = root.querySelectorAll('*'); } catch (e) { return; }
                    for (const e of els) {
                      if (/ui-datepicker__calendar-month-text/.test((e.className||'').toString())
                          && U.visible(e)) out.push((e.innerText||'').replace(/\\s+/g,' ').trim());
                      if (e.shadowRoot) walk(e.shadowRoot, d + 1);
                    }
                  };
                  walk(document, 0);
                  return out;
                }"""
                moved, seen = 0, []
                for _ in range(24):          # 한 번에 한 달씩. 1년 반이면 18번이면 닿는다
                    seen = page.evaluate(MONTHS)
                    if head in seen:
                        break
                    first_month = re.search(r'(\d{4})년\s*(\d+)월', seen[0]) if seen else None
                    if not first_month:
                        break
                    before = (int(y), int(mo)) < (int(first_month[1]), int(first_month[2]))
                    if not page.evaluate("""direction => {
                      const U = window.KE_UTIL;
                      const n = U.candidates(document).find(e => U.visible(e)
                        && /ui-datepicker__calendar-button/.test((e.className||'').toString())
                        && (e.className||'').toString().includes(direction));
                      if (!n || n.disabled) return false;
                      U.fireClick(n); return true;
                    }""", '-prev' if before else '-next'):
                        break
                    moved += 1
                    page.wait_for_timeout(320)
                log(f"  달 이동 {moved}회 -> 보이는 달 {seen} (목표 {head})")

                # 달력은 두 달을 나란히 그린다. 보이는 날짜 전체에서 일자만 맞춰 첫 일치를
                # 고르면 '왼쪽(이른 달)' 의 같은 일자를 집는다 - 2027-08-28(토) 을 달라고 했는데
                # 2027-07-28(수) 을 골랐다(실측 2026-09-03). 그러면 그 날짜로 조회가 나가
                # 운항일이 아니면 "정상적으로 처리되지 않았습니다" 로 끝난다.
                # util.findPickerDate 는 #month{YYYYMM} 안에서만 찾으므로 달을 안 넘는다.
                got = page.evaluate("""({mmdd, year}) => {
                  const U = window.KE_UTIL;
                  const r = U.findPickerDate ? U.findPickerDate(mmdd, year) : null;
                  if (!r || !r.el || !r.available) return null;
                  U.fireClick(r.el);
                  return U.label(r.el).replace(/\\s+/g,' ').slice(0,30);
                }""", {"mmdd": f"{int(mo):02d}-{int(dy):02d}", "year": int(y)})
                # 그 날이 아직 안 열렸을 수 있다 (09:00 에 열리는 날을 08:50 에 세울 때가
                # 그렇다). 여기서 필요한 것은 "달력이 그 달을 보여주는 것" 뿐이므로,
                # 같은 달의 고를 수 있는 마지막 날로 대신한다.
                if not got:
                    # 폴백도 '그 달 안' 에서 고른다. 전체에서 마지막을 집으면 오른쪽 달의
                    # 엉뚱한 날로 새거나, 운항하지 않는 요일을 골라 조회가 실패한다.
                    got = page.evaluate("""({year, mo}) => {
                      const U = window.KE_UTIL;
                      const box = document.getElementById('month' + year + mo);
                      if (!box) return null;
                      let tds; try { tds = box.querySelectorAll('td'); } catch (e) { return null; }
                      const ok = [...tds].filter(t => U.visible(t)
                        && (t.className||'').toString().indexOf('-available') !== -1);
                      if (!ok.length) return null;
                      const t = ok[ok.length - 1];
                      U.fireClick(t);
                      return U.label(t).replace(/\\s+/g,' ').slice(0,30);
                    }""", {"year": int(y), "mo": f"{int(mo):02d}"})
                    log(f"  목표일이 아직 없어 그 달 마지막 날로 대신: {got or '가능한 날 없음'}")
                else:
                    log(f"  목표일 선택: {got}")
                if not got:
                    print(json.dumps({"ok": False, "url": page.url,
                                      "why": f"달력에서 {want_date} 달의 날짜를 못 고름"},
                                     ensure_ascii=False))
                    return 7
            else:
                got = page.evaluate("""() => {
                  const U = window.KE_UTIL;
                  const tds = U.candidates(document).filter(e => U.visible(e)
                    && /ui-datepicker__td/.test((e.className||'').toString())
                    && /-available/.test((e.className||'').toString()));
                  if (!tds.length) return null;
                  const t = tds[Math.min(5, tds.length - 1)];
                  U.fireClick(t);
                  return U.label(t).replace(/\\s+/g,' ').slice(0,26);
                }""")
                log(f"  날짜 선택: {got or '가능한 날짜 없음'}")
            page.wait_for_timeout(2500)

        log("항공편 검색")
        page.evaluate("""() => {
          const U = window.KE_UTIL;
          const b = U.candidates(document).filter(e => U.visible(e) && U.label(e) === '항공편 검색');
          if (b.length) U.fireClick(b[b.length-1]);
        }""")
        DEP = "/booking/select-award-flight"
        # 마일리지 예매는 검색하면 '항상' 달력으로 간다. 조회 화면은 달력에서 날짜를
        # 눌러야 도달한다 (실측 2026-09-03: '가까운 날짜 함께 조회' 를 꺼도 달력으로 갔다.
        # 그 체크박스는 달력/조회를 가르는 스위치가 아니었다).
        # 그래서 조회 화면이 목표면, 달력에 도착한 뒤 목표 날짜를 눌러 한 걸음 더 간다.
        for _ in range(60):
            time.sleep(0.5)
            if CAL in page.url or DEP in page.url: break
        page.wait_for_timeout(6000)
        inject()

        if departure and DEP not in page.url and CAL in page.url:
            log("조회 화면으로: 달력에서 날짜를 누른다")
            # 목표일이 달력 창에 없으면 '가장 최신 오픈일' 로 대신 들어간다.
            # 조회 화면은 선택일 ±3일 날짜 띠를 들고 있으므로, 목표 근처에만 서면
            # 띠에서 목표일을 집을 수 있다. 못 들어가는 것보다 훨씬 낫다.
            picked = page.evaluate("""(want) => {
              const U = window.KE_UTIL;
              let cell = want ? U.findOpenDate('dep-fare-', want) : null;
              let how = 'want';
              if (!cell) { cell = U.findOpenDate('dep-fare-', ''); how = 'latest'; }
              if (!cell) return null;
              const lb = U.label(cell).replace(/\\s+/g, ' ').slice(0, 30);
              U.fireClick(cell);
              return how + ': ' + lb;
            }""", want_date[5:] if want_date else "")
            log(f"  누른 날짜: {picked or '못 찾음'}")
            page.wait_for_timeout(2500)
            # 날짜만 눌러서는 화면이 안 넘어간다. 달력 아래쪽 [검색] 을 눌러야 조회 화면으로
            # 간다. 위젯의 [항공편 검색] 과 다른 버튼이다 - 달력에는 그 라벨이 없어서
            # 그걸 찾다가 계속 실패했다(실측). 녹화된 2단계도 라벨이 '검색' 이다.
            inject()
            clicked = page.evaluate("""() => {
              const U = window.KE_UTIL;
              const pick = (lb) => U.candidates(document)
                .filter(e => U.visible(e) && U.label(e).replace(/\\s+/g,' ').trim() === lb);
              let b = pick('검색');
              if (!b.length) b = pick('항공편 검색');
              if (!b.length) return null;
              U.fireClick(b[b.length - 1]);
              return U.label(b[b.length - 1]).slice(0, 20);
            }""")
            log(f"  검색 버튼: {clicked or '못 찾음'}")
            for _ in range(40):
                time.sleep(0.5)
                if DEP in page.url: break
            page.wait_for_timeout(4000)
            inject()

        goal = DEP if departure else CAL
        ok = goal in page.url
        why = "" if ok else f"검색이 {'조회' if departure else '달력'} 화면으로 가지 않음"

        # 주소만 보고 '준비됨' 이라 하면 안 된다. 09-07 에 달력이 **2026년 9월**을
        # 그리고 있는데도 주소가 calendar-fare-bonus 라서 "달력 도착" 으로 보고했고,
        # 매크로는 2027년 날짜를 찾다가 단계 0 에서 180초를 흘려보냈다. 세 번 그랬다.
        # 목표 달(#month{YYYYMM})이 실제로 그려져 있는지 확인한다.
        if ok and want_date and not departure:
            ym = want_date[:4] + want_date[5:7]
            shown = page.evaluate("() => [...document.querySelectorAll('[id^=month]')].map(e => e.id)")
            if f"month{ym}" not in (shown or []):
                ok = False
                why = f"달력이 목표 달을 안 그린다 (원한 month{ym} / 화면 {shown})"

        log((("조회" if departure else "달력") + (" 도착: " if ok else " 실패: ")) +
            (page.url[:70] if ok else why))
        print(json.dumps({"ok": ok, "url": page.url, "why": why}, ensure_ascii=False))
        return 0 if ok else 3


if __name__ == "__main__":
    sys.exit(main())
