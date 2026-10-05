"""ASTRA - 대한항공 마일리지 좌석을 개방 시각에 잡는 프로그램. 이 파일이 유일한 입구다.

설정 한 장(config/run.json)을 읽어 준비·점검·연습·예약·결과 확인을 한다.

  astra setup        처음 한 번. 설정 파일 두 개를 만들고 PC 를 확인한다
  astra login        계정마다 브라우저를 띄워 로그인해 둔다
  astra check        실행 전 점검. --online 을 붙이면 실제로 로그인·달력까지 가 본다
  astra plan         이 설정이면 무엇이 언제 실행되는지 보여 준다
  astra rehearse     이미 열린 날짜로 끝까지 연습한다(미결제 예약 1건이 생긴다)
  astra schedule     실전을 예약한다(Windows 작업 스케줄러). --remove 로 취소
  astra run          지금 바로 실전을 시작한다
  astra status       무슨 일이 있었는지 사람 말로 알려 준다

Windows 에서는 astra.cmd 로 부른다(처음 실행할 때 파이썬 환경을 만든다).
결제는 이 프로그램이 하지 않는다. 좌석을 잡고 결제창을 열어 두는 데까지 한다.
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'dev'))
import astra_config as ac  # noqa: E402

KST = timezone(timedelta(hours=9))
PY = sys.executable
SHOTS = ROOT / 'dev-shots'
REPORTS = SHOTS / 'api-day'
ENV_FILE, ENV_EXAMPLE = ROOT / '.env', ROOT / '.env.example'
SUBENV = {**os.environ, 'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1'}
OK, BAD, NOTE = '[ 확인 ]', '[ 문제 ]', '[ 참고 ]'
LOGIN_WAIT_SECONDS = 600


def say(text=''):
    print(text, flush=True)


def today():
    return datetime.now(KST).date()


# ---------------------------------------------------------------------------
# 공통 도우미
# ---------------------------------------------------------------------------
def load_cfg():
    """설정을 읽는다. 없거나 고칠 곳이 있으면 이유를 말하고 끝낸다."""
    try:
        cfg = ac.load()
    except ac.ConfigError as exc:
        say(f'{BAD} {exc}')
        raise SystemExit(2)
    if cfg is None:
        say(f'{BAD} 설정 파일이 없다: {ac.CONFIG.relative_to(ROOT)}')
        say('         먼저 `astra setup` 을 실행한다.')
        raise SystemExit(2)
    return cfg


def env_filled():
    """.env 에서 값이 채워진 이름들. 값은 읽어서 버린다 - 어디에도 내보내지 않는다."""
    names = set()
    if not ENV_FILE.exists():
        return names
    for line in ENV_FILE.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        if value.strip().strip('"').strip("'"):
            names.add(key.strip())
    return names


def chrome_path():
    if os.name == 'nt':
        roots = [os.environ.get('ProgramFiles'), os.environ.get('ProgramFiles(x86)'), os.environ.get('LOCALAPPDATA')]
        for root in roots:
            if root and (Path(root) / 'Google/Chrome/Application/chrome.exe').exists():
                return Path(root) / 'Google/Chrome/Application/chrome.exe'
        return None
    mac = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    return mac if mac.exists() else None


def powershell(*args, timeout=180):
    """(종료 코드, 출력). Windows 가 아니면 (None, '')."""
    if os.name != 'nt':
        return None, ''
    try:
        p = subprocess.run(['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', *args], cwd=ROOT,
                           capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired:
        return -1, 'timeout'
    return p.returncode, ((p.stdout or '') + (p.stderr or '')).strip()


def start_browser(port, *, restart=False, show=False):
    """그 자리의 전용 Chrome 을 띄운다. 이미 떠 있으면 그대로 쓴다."""
    if os.name != 'nt':
        code = subprocess.call([str(ROOT / 'dev' / 'astra_browsers.sh'), '-Port', str(port)], cwd=ROOT)
        return code == 0, ''
    args = ['-File', str(ROOT / 'dev' / 'astra_browsers.ps1'), '-Port', str(port)]
    args += (['-Restart'] if restart else []) + (['-Show'] if show else [])
    code, out = powershell(*args)
    return code == 0, out.splitlines()[-1] if out else ''


def run_setup(port, cfg, when):
    """로그인하고 달력까지 간다(dev/setup.py). (성공 여부, 실패 이유)."""
    trip = cfg['trip']
    cmd = [PY, str(ROOT / 'dev' / 'setup.py'), trip['destination'], '--from', trip['origin'],
           '--port', str(port), '--date', when, '--api-mode']
    try:
        p = subprocess.run(cmd, cwd=ROOT, env=SUBENV, capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=420)
    except subprocess.TimeoutExpired:
        return False, '7분 안에 끝나지 않았다'
    lines = ((p.stdout or '') + (p.stderr or '')).strip().splitlines()
    try:
        verdict = json.loads(lines[-1]) if lines else {}
    except ValueError:
        verdict = {}
    if isinstance(verdict, dict) and verdict.get('ok'):
        return True, ''
    return False, (verdict.get('why') if isinstance(verdict, dict) else '') or (lines[-1][:200] if lines else '출력 없음')


LOGGED_IN_JS = """() => { let hit=false;
  const deep=(root,d)=>{ if(!root||d>10||hit) return;
    for(const x of root.querySelectorAll('a,button,[role=button]')){
      if((x.textContent||'').trim()==='로그아웃'){ const r=x.getBoundingClientRect();
        if(r.width>1&&r.height>1){ hit=true; return; } } }
    for(const x of root.querySelectorAll('*')) if(x.shadowRoot) deep(x.shadowRoot,d+1); };
  deep(document,0); return hit; }"""


def logged_in(port):
    """그 자리의 Chrome 에 대한항공 로그인 표시(로그아웃 버튼)가 보이는가. 읽기만 한다."""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=8000)
            for context in browser.contexts:
                for page in context.pages:
                    if 'koreanair.com' in (page.url or '') and page.evaluate(LOGGED_IN_JS):
                        return True
    except Exception:  # noqa: BLE001
        return False
    return False


def used_slots(cfg):
    """이 설정이 쓰는 브라우저 자리. [(포트, 계정, 쓰임)]"""
    slots = []
    for index, acc in enumerate(cfg['accounts']):
        slots.append((ac.FIRST_PORTS[index], acc, '1순위'))
        if acc['fallback']:
            slots.append((ac.FALLBACK_PORTS[index], acc, '대체'))
    if cfg['observer']:
        slots.append((ac.OBSERVER_PORT, next(a for a in cfg['accounts'] if a['name'] == cfg['observer']), '계측'))
    return slots


def run_day_of(cfg, given=None):
    return given or ac.open_day(cfg['trip']['date'])


def task_name(run_day, run):
    return f'{run_day[5:7]}{run_day[8:10]}-{run["name"]}'


def cabin_ko(cabin):
    return ac.CABINS[cabin][1]


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
def cmd_setup(a):
    say('ASTRA 처음 설정')
    made = []
    for target, example in ((ENV_FILE, ENV_EXAMPLE), (ac.CONFIG, ac.EXAMPLE)):
        if target.exists():
            say(f'{OK} {target.relative_to(ROOT)} 이미 있다(건드리지 않는다)')
        elif example.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(example, target)
            made.append(target)
            say(f'{OK} {target.relative_to(ROOT)} 를 만들었다')
        else:
            say(f'{BAD} 본보기 파일이 없다: {example.relative_to(ROOT)}')
    say(f'{OK} 파이썬 {sys.version.split()[0]}' if sys.version_info >= (3, 10)
        else f'{BAD} 파이썬 3.10 이상이 필요하다(지금 {sys.version.split()[0]})')
    chrome = chrome_path()
    say(f'{OK} Chrome: {chrome}' if chrome else f'{BAD} Chrome 을 찾지 못했다 - https://www.google.com/chrome 에서 설치한다')
    try:
        import playwright  # noqa: F401
        say(f'{OK} 필요한 패키지가 설치돼 있다')
    except ImportError:
        say(f'{BAD} 패키지가 없다 - `.venv\\Scripts\\python.exe -m pip install -r requirements.lock.txt`')
    if os.name != 'nt':
        say(f'{NOTE} 예약(schedule)·바로 실행(run)은 Windows 작업 스케줄러와 콘솔 창을 쓴다. 다른 OS 에서는 되지 않는다')
    say()
    say('다음 할 일')
    say(f'  1. 메모장으로 {ac.CONFIG.relative_to(ROOT)} 를 열어 노선·날짜·계정·마일리지를 내 것으로 바꾼다')
    say('  2. 스카이패스 아이디로 로그인하는 계정이 있으면 메모장으로 .env 를 열어 아이디·비밀번호를 적는다')
    say('     (비밀번호는 이 파일에만 둔다. 누구에게도, 어떤 채팅창에도 붙여 넣지 않는다)')
    say('  3. astra check      설정이 맞는지 본다')
    say('  4. astra login      브라우저를 띄워 로그인해 둔다')
    say('  5. astra rehearse   이미 열린 날짜로 한 번 연습한다')
    say('  6. astra schedule   실전을 예약한다')
    return 0


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------
def describe_plan(cfg, run_day):
    trip, times = cfg['trip'], cfg['times']
    runs = ac.plan_runs(cfg, run_day)
    lines = [f'잡으려는 편   KE{trip["flight"]}  {trip["origin"]} → {trip["destination"]}  출발 {trip["date"]}',
             f'좌석 개방     {ac.open_day(trip["date"])} {times["open"]} (한국 시각, 출발 {ac.OPEN_DAYS_AHEAD}일 전)',
             f'실행하는 날   {run_day}',
             f'시간표        {times["start"]} 준비 시작 → {times["capture"]} 연습 통과 → {times["health"]} 로그인 점검'
             f' → {times["open"]} 발사',
             f'캡처 날짜     {"자동(달력에서 일반석 있는 가장 가까운 앞 날짜)" if trip["captureDate"] == "auto" else trip["captureDate"]}',
             '']
    for run in runs:
        when = run['at'] if run['role'] == 'first' else f'개방 {cfg["tuning"]["fallbackDelayMs"] / 1000:g}초 뒤'
        rule = (f'{run["account"]} 의 1순위가 거절됐을 때만 주문한다(좌석을 잡았으면 주문하지 않는다)'
                if run['role'] == 'fallback' else
                f'개방 {run["preFireMs"]}ms 전에 조회를 보낸다' + (' · 계측을 함께 띄운다' if run['observer'] else ''))
        lines.append(f'  {run["name"]:<22} 자리 {run["port"]}  {cabin_ko(run["cabin"]):<6} {when:<12} {rule}')
    return runs, lines


def cmd_plan(a):
    cfg = load_cfg()
    run_day = run_day_of(cfg, a.date)
    runs, lines = describe_plan(cfg, run_day)
    for line in lines:
        say(line)
    if a.commands:
        say()
        for run in runs:
            say(f'[{run["name"]}] api_booking/api_day.py ' + ' '.join(ac.api_day_args(run, cfg)))
    return 0


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------
def order_records(run, cfg):
    """그 실행의 상태 폴더에 새 실행을 막는 기록이 있는가. (막힘 여부, 설명 줄들)"""
    cmd = [PY, str(ROOT / 'api_booking' / 'live_order.py'), '--date', cfg['trip']['date'], '--status',
           '--state-dir', run['stateDir']]
    p = subprocess.run(cmd, cwd=ROOT, env=SUBENV, capture_output=True, text=True, encoding='utf-8', errors='replace')
    lines = [l.split('] ', 1)[-1].strip() for l in (p.stdout or '').splitlines() if l.strip()]
    return p.returncode != 0, lines[1:]


def scheduled_tasks():
    code, out = powershell('-Command', "Get-ScheduledTask -TaskName 'Astra-*' -ErrorAction SilentlyContinue | "
                           "ForEach-Object { $i = $_ | Get-ScheduledTaskInfo; '{0}|{1}|{2}' -f $_.TaskName, $_.State, "
                           "$(if ($i.NextRunTime) { $i.NextRunTime.ToString('yyyy-MM-dd HH:mm') } else { '' }) }")
    rows = []
    for line in (out or '').splitlines():
        parts = line.strip().split('|')
        if len(parts) == 3 and parts[0].startswith('Astra-'):
            rows.append({'name': parts[0], 'state': parts[1], 'next': parts[2]})
    return rows


def cmd_check(a):
    problems = 0

    def bad(text):
        nonlocal problems
        problems += 1
        say(f'{BAD} {text}')

    cfg = load_cfg()
    trip = cfg['trip']
    say(f'{OK} 설정 파일을 읽었다 - KE{trip["flight"]} {trip["origin"]}→{trip["destination"]} {trip["date"]}, '
        f'계정 {len(cfg["accounts"])}개')

    filled = env_filled()
    for index, acc in enumerate(cfg['accounts']):
        if acc['login'] == 'skypass':
            need = ac.ENV_KEYS[index][:2]
            missing = [k for k in need if k not in filled]
            if missing:
                bad(f'계정 {acc["name"]}: .env 의 {"·".join(missing)} 가 비어 있다. 메모장으로 채운다')
            else:
                say(f'{OK} 계정 {acc["name"]}: .env 에 아이디·비밀번호가 채워져 있다(값은 보지 않았다)')
        else:
            say(f'{NOTE} 계정 {acc["name"]}: 네이버 연동 - `astra login` 으로 창에서 직접 로그인해 둔다')
    for acc in cfg['accounts']:
        say(f'{NOTE} 계정 {acc["name"]}: 마일리지 {acc["mileage"]:,} 로 적혀 있다. 대한항공 앱의 잔액과 같은지 직접 확인한다'
            ' (필요 마일리지보다 적으면 주문하지 않는다)')

    chrome = chrome_path()
    if chrome:
        say(f'{OK} Chrome 이 있다')
    else:
        bad('Chrome 을 찾지 못했다 - https://www.google.com/chrome 에서 설치한다')

    opens, now = ac.open_day(trip['date']), today()
    if date.fromisoformat(opens) > now:
        say(f'{OK} {trip["date"]} 좌석은 {opens} {cfg["times"]["open"]} 에 열린다({(date.fromisoformat(opens) - now).days}일 뒤)')
    elif date.fromisoformat(opens) == now:
        say(f'{OK} {trip["date"]} 좌석은 오늘 {cfg["times"]["open"]} 에 열린다')
    else:
        say(f'{NOTE} {trip["date"]} 는 {opens} 에 이미 열렸다. 개방 경쟁이 아니라 남은 좌석을 잡는 실행이 된다'
            ' (연습은 `astra rehearse`)')

    for run in ac.plan_runs(cfg, run_day_of(cfg)):
        blocked, lines = order_records(run, cfg)
        if blocked:
            bad(f'{run["name"]}: 최근 12시간 안에 주문을 보낸 기록이 있어 지금은 실행되지 않는다')
            for line in lines:
                say(f'           {line}')
        else:
            say(f'{OK} {run["name"]}: 실행을 막는 주문 기록이 없다')

    if os.name == 'nt':
        tasks = scheduled_tasks()
        if tasks:
            for t in tasks:
                say(f'{NOTE} 예약된 작업 {t["name"]} - {t["state"]}' + (f', 다음 실행 {t["next"]}' if t['next'] else ', 다음 실행 없음'))
        else:
            say(f'{NOTE} 예약된 작업이 없다(`astra schedule` 로 예약한다)')

    if a.online:
        say()
        say('실제로 로그인해서 달력까지 가 본다(모든 자리를 함께, 3~4분). 주문·좌석 선택은 하지 않는다.')
        sys.path.insert(0, str(ROOT / 'api_booking'))
        import capture_date
        target = trip['date']
        probe = min(date.fromisoformat(capture_date.tentative(target)), now + timedelta(days=ac.OPEN_DAYS_AHEAD - 8))
        probe_iso = probe.isoformat() if trip['captureDate'] == 'auto' else trip['captureDate']
        def probe_slot(slot):
            """한 자리를 끝까지 가 본다. [(문제인가, 문장)] 을 돌려준다. 자리끼리는 서로 독립이라 함께 돌린다."""
            port, acc, use = slot
            who = f'자리 {port}({acc["name"]} {use})'
            started, why = start_browser(port)
            if not started:
                return [(True, f'{who}: Chrome 을 띄우지 못했다 - {why}')]
            ok, why = run_setup(port, cfg, probe_iso)
            if not ok:
                return [(True, f'{who}: {why}' + (' → `astra login`' if '로그인' in why else ''))]
            out = [(False, f'{who}: 로그인돼 있고 달력이 열린다')]
            if use == '계측':
                return out
            try:
                cells = capture_date.read_cells(port)
            except Exception as exc:  # noqa: BLE001
                return out + [(True, f'{who}: 달력을 읽지 못했다({type(exc).__name__})')]
            days = capture_date.parse_cells(cells, target)
            if trip['captureDate'] == 'auto':
                picked = capture_date.choose(cells, target)
                out.append((False, f'    캡처 날짜로 쓸 수 있는 날: {picked} (일반석 있음). 실전 아침에 다시 고른다') if picked
                           else (True, f'{who}: {target} 앞쪽에 일반석이 있는 열린 날짜가 달력에 없다'))
            elif days.get(trip['captureDate']) != 'seat:일반석':
                out.append((True, f'{who}: 캡처 날짜 {trip["captureDate"]} 에 일반석이 없다(달력 표시: '
                                  f'{days.get(trip["captureDate"], "칸 없음")}). "auto" 로 두거나 다른 날로 바꾼다'))
            else:
                out.append((False, f'    캡처 날짜 {trip["captureDate"]}: 일반석 있음'))
            return out

        from concurrent.futures import ThreadPoolExecutor
        slots = used_slots(cfg)
        with ThreadPoolExecutor(max_workers=len(slots)) as pool:
            for lines in pool.map(probe_slot, slots):
                for is_bad, text in lines:
                    if is_bad:
                        bad(text)
                    else:
                        say(f'{OK} {text}' if not text.startswith('    ') else f'       {text}')
    else:
        say(f'{NOTE} 로그인과 달력은 확인하지 않았다. 실전 전날에는 `astra check --online` 으로 실제로 가 본다')

    say()
    say('고칠 곳이 없다.' if not problems else f'고칠 곳이 {problems}군데 있다.')
    return 0 if not problems else 2


# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------
def cmd_login(a):
    cfg = load_cfg()
    sys.path.insert(0, str(ROOT / 'api_booking'))
    import capture_date
    probe = min(date.fromisoformat(capture_date.tentative(cfg['trip']['date'])),
                today() + timedelta(days=ac.OPEN_DAYS_AHEAD - 8)).isoformat()
    filled, failed = env_filled(), 0
    slots = [s for s in used_slots(cfg) if not a.account or s[1]['name'] == a.account]
    if not slots:
        say(f'{BAD} 그런 계정이 없다: {a.account}')
        return 2
    say(f'브라우저 {len(slots)}개에 로그인해 둔다. 창을 닫지 않는다 - 로그인은 창(프로필)마다 따로 저장된다.')
    for port, acc, use in slots:
        say()
        say(f'── 자리 {port} · 계정 {acc["name"]} · {use} ──')
        index = cfg['accounts'].index(acc)
        if acc['login'] == 'skypass':
            missing = [k for k in ac.ENV_KEYS[index][:2] if k not in filled]
            if missing:
                say(f'{BAD} .env 의 {"·".join(missing)} 가 비어 있다. 메모장으로 채운 뒤 다시 실행한다')
                failed += 1
                continue
        human = acc['login'] == 'naver'
        started, why = start_browser(port, restart=human, show=human)
        if not started:
            say(f'{BAD} Chrome 을 띄우지 못했다 - {why}')
            failed += 1
            continue
        ok, why = run_setup(port, cfg, probe)
        if not ok and human:
            say('  이 창은 사람이 직접 로그인해야 한다. 방금 뜬 Chrome 창에서:')
            say('    1. 대한항공 화면 오른쪽 위 [로그인]을 누른다')
            say('    2. [네이버 로그인]을 눌러 아이디·비밀번호를 넣는다')
            say('    3. 네이버 로그인 화면의 "로그인 상태 유지"를 반드시 켠다(꺼 두면 실전 아침에 다시 물어본다)')
            say(f'  로그인되면 자동으로 다음으로 넘어간다(최대 {LOGIN_WAIT_SECONDS // 60}분 기다린다)...')
            deadline = time.monotonic() + LOGIN_WAIT_SECONDS
            while time.monotonic() < deadline and not logged_in(port):
                time.sleep(4)
            ok, why = run_setup(port, cfg, probe) if logged_in(port) else (False, '기다리는 동안 로그인되지 않았다')
        if ok:
            say(f'{OK} 로그인됐고 달력까지 열린다')
        else:
            say(f'{BAD} {why}')
            failed += 1
    say()
    if failed:
        say(f'{failed}개 자리가 준비되지 않았다. 위의 이유를 고친 뒤 `astra login` 을 다시 실행한다.')
        return 2
    say('모든 자리가 준비됐다. 네이버 연동 계정은 로그인 유지 기간(약 한 달)이 지나면 다시 해야 한다.')
    return 0


# ---------------------------------------------------------------------------
# rehearse
# ---------------------------------------------------------------------------
def rehearsal_date(cfg):
    """연습에 쓸 기본 날짜: 목표와 같은 요일이면서 이미 열려 있는 가장 가까운 날."""
    target = date.fromisoformat(cfg['trip']['date'])
    latest_open = today() + timedelta(days=ac.OPEN_DAYS_AHEAD - 1)
    day = target - timedelta(days=7)
    while day > latest_open:
        day -= timedelta(days=7)
    return day.isoformat()


def cmd_rehearse(a):
    cfg = load_cfg()
    acc = next((x for x in cfg['accounts'] if x['name'] == (a.account or cfg['accounts'][0]['name'])), None)
    if acc is None:
        say(f'{BAD} 그런 계정이 없다: {a.account}')
        return 2
    when = a.date or rehearsal_date(cfg)
    trip = cfg['trip']
    args = ac.rehearsal_args(cfg, acc, when, fire_in_min=a.fire_in_min)
    say(f'연습: 계정 {acc["name"]} 로 KE{trip["flight"]} {trip["origin"]}→{trip["destination"]} {when} 일반석을 끝까지 잡아 본다.')
    say('  · 실전과 같은 순서다: 로그인 → 연습 통과 → 대기 → 발사 → 주문 → 결제창')
    say(f'  · 약 {a.fire_in_min + 6:.0f}분 걸린다. 그동안 그 Chrome 창을 건드리지 않는다')
    say('  · **실제 예약(미결제)이 1건 생긴다.** 결제창에서 결제하지 않으면 스스로 풀린다(2026-09 실측 10~32분)')
    say('  · 결제창이 뜨면 그냥 닫는다. 결제 버튼은 누르지 않는다')
    if not a.yes:
        try:
            answer = input('계속하려면 yes 를 입력한다: ').strip().lower()
        except EOFError:
            answer = ''
        if answer != 'yes':
            say('그만둔다.')
            return 1
    code = subprocess.call([PY, '-u', str(ROOT / 'api_booking' / 'api_day.py'), *args], cwd=ROOT, env=SUBENV)
    say()
    if code == 0:
        say(f'{OK} 연습이 끝까지 갔다. 결제창이 열려 있으면 닫는다. 예약은 결제하지 않으면 풀린다.')
    else:
        say(f'{BAD} 연습이 끝까지 가지 못했다(종료 코드 {code}). 위 로그에서 마지막으로 통과한 줄을 '
            'docs/samples/console-1-success.log 와 견줘 본다.')
    return code


# ---------------------------------------------------------------------------
# schedule / run
# ---------------------------------------------------------------------------
SCHEDULER = ROOT / 'api_booking' / 'schedule_api_day.ps1'


def check_times(cfg, run_day, *, now_start=False):
    """실행하는 날과 시각이 말이 되는가. 문제 문구를 돌려준다(없으면 None)."""
    now = datetime.now(KST)
    day = date.fromisoformat(run_day)
    if day < now.date():
        return f'{run_day} 는 지난 날이다'
    h, m, s = (int(x) for x in cfg['times']['open'].split(':'))
    opens = datetime(day.year, day.month, day.day, h, m, s, tzinfo=KST)
    if now_start:
        if day != now.date():
            return f'바로 실행은 오늘({now.date()}) 것만 된다. {run_day} 는 `astra schedule` 로 예약한다'
        if opens <= now:
            return f'오늘 발사 시각 {cfg["times"]["open"]} 이 이미 지났다'
        if opens - now < timedelta(minutes=12):
            return (f'발사 시각 {cfg["times"]["open"]} 까지 {int((opens - now).total_seconds() // 60)}분 남았다. '
                    '준비에 12분은 필요하다')
    else:
        sh, sm = (int(x) for x in cfg['times']['start'].split(':'))
        if datetime(day.year, day.month, day.day, sh, sm, tzinfo=KST) <= now + timedelta(minutes=1):
            return f'준비 시작 시각 {run_day} {cfg["times"]["start"]} 이 이미 지났다. 오늘 바로 하려면 `astra run`'
    return None


def cmd_schedule(a):
    if os.name != 'nt':
        say(f'{BAD} 예약은 Windows 작업 스케줄러를 쓴다. 이 OS 에서는 되지 않는다')
        return 2
    cfg = load_cfg()
    if a.list:
        tasks = scheduled_tasks()
        for t in tasks:
            say(f'{t["name"]:<32} {t["state"]:<8} {t["next"] or "다음 실행 없음"}')
        if not tasks:
            say('예약된 작업이 없다.')
        return 0
    run_day = run_day_of(cfg, a.date)
    runs = ac.plan_runs(cfg, run_day)
    if a.remove:
        for run in runs:
            code, out = powershell('-File', str(SCHEDULER), '-Name', task_name(run_day, run), '-Remove')
            say(f'{OK} 예약 취소: Astra-{task_name(run_day, run)}' if code == 0
                else f'{NOTE} Astra-{task_name(run_day, run)}: 예약돼 있지 않았다')
        return 0
    why = check_times(cfg, run_day)
    if why:
        say(f'{BAD} {why}')
        return 2
    _, lines = describe_plan(cfg, run_day)
    for line in lines:
        say(line)
    say()
    failed = 0
    for run in runs:
        line = ' '.join(ac.api_day_args(run, cfg))
        if a.dry_run:
            say(f'(보기만) Astra-{task_name(run_day, run)} {run_day} {cfg["times"]["start"]} :: {line}')
            continue
        code, out = powershell('-File', str(SCHEDULER), '-Name', task_name(run_day, run), '-Date', run_day,
                               '-At', cfg['times']['start'], '-ArgsLine', line)
        if code == 0:
            say(f'{OK} 예약: Astra-{task_name(run_day, run)} - {run_day} {cfg["times"]["start"]}')
        else:
            failed += 1
            say(f'{BAD} 예약 실패: Astra-{task_name(run_day, run)} - {out.splitlines()[-1] if out else ""}')
    if a.dry_run:
        return 0
    if failed:
        return 2
    say()
    say('그날 지킬 것')
    say('  · PC 를 켜 두고 Windows 에 로그인한 채로 둔다(잠금 화면은 괜찮다, 로그아웃·종료는 안 된다)')
    say(f'  · {cfg["times"]["start"]} 부터는 뜨는 Chrome 창을 건드리지 않는다')
    say('  · 알림음이 나면 결제창이 열린 것이다. 그때부터 사람이 결제한다')
    say('  · 결과는 `astra status`, 취소는 `astra schedule --remove`')
    return 0


def cmd_run(a):
    if os.name != 'nt':
        say(f'{BAD} 바로 실행은 Windows 콘솔 창을 쓴다. 이 OS 에서는 되지 않는다')
        return 2
    cfg = load_cfg()
    run_day = today().isoformat()
    why = check_times(cfg, run_day, now_start=True)
    if why:
        say(f'{BAD} {why}')
        return 2
    runs = [r for r in ac.plan_runs(cfg, run_day) if not a.only or r['name'] == a.only]
    if not runs:
        say(f'{BAD} 그런 실행이 없다: {a.only}')
        return 2
    if a.again:
        say(f'{NOTE} --again: 오늘 이미 보낸 주문 기록이 있어도 다시 보낸다. 앞선 주문이 좌석을 잡았다면 하나 더 잡힐 수 있다')
    failed = 0
    for run in runs:
        line = ' '.join(ac.api_day_args(run, cfg, again=a.again))
        code, out = powershell('-File', str(SCHEDULER), '-Name', task_name(run_day, run), '-Now', '-ArgsLine', line)
        if code == 0:
            say(f'{OK} 시작: {run["name"]} (새 콘솔 창)')
        else:
            failed += 1
            say(f'{BAD} 시작 실패: {run["name"]} - {out.splitlines()[-1] if out else ""}')
    say()
    say('콘솔 창을 닫지 않는다. 결과는 `astra status`.')
    return 2 if failed else 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------
def latest_report(day, port, mode='live'):
    stamp = day.replace('-', '')
    best = None
    for path in sorted(REPORTS.glob(f'{mode}-{stamp}-*.json')) if REPORTS.exists() else []:
        try:
            rep = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(rep, dict) and rep.get('port') == port:
            best = rep
    return best


def read_json(path):
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def run_outcome(run, day, report, intent, signal):
    """한 실행의 결과를 (기호, 문장)으로. 주문 기록을 먼저 보고, 없으면 실행 보고서를 본다."""
    state = (intent or {}).get('state')
    if state == 'ordered':
        if intent.get('paymentWindowReached'):
            return '✔', '좌석을 잡았다. 결제창이 열려 있다 - 직접 결제한다'
        return '✔', '좌석을 잡았다. 결제창은 열지 못했다 - 대한항공 누리집·앱의 예약 조회에서 결제한다'
    if state == 'unknown' and intent.get('why') == 'business-error':
        return '✘', '주문이 거절됐다. 좌석이 먼저 팔렸다(콘솔 창이 입력을 기다리면 exit 를 치거나 닫는다)'
    if state in ('unknown', 'sending'):
        return '?', '주문을 보냈지만 결과를 확인하지 못했다 - 대한항공 예약 조회에서 직접 확인한다'
    if state == 'prep-order-possible':
        return '?', '준비 중에 주문 요청이 나갔을 수 있다 - 대한항공 예약 조회에서 직접 확인한다'
    if report is None:
        return '·', '아직 시작하지 않았다'
    result = report.get('result')
    if result is None:
        fire = (report.get('fireAt') or '')[11:19]
        return '…', f'진행 중이다' + (f'(발사 {fire} 예정)' if fire else '(준비 중)')
    if result == 'done':
        if run['role'] == 'fallback':
            if (signal or {}).get('state') == 'order-recorded':
                return '–', '1순위가 좌석을 잡아서 주문하지 않았다(정상)'
            return '–', '1순위의 결과를 기다리다 주문하지 않았다'
        return '–', '끝났지만 주문 기록이 없다 - 콘솔 로그를 본다'
    reasons = {'reprep-cutoff': '준비가 마감 시각까지 끝나지 않아 발사하지 못했다',
               'attempts-exhausted': '준비를 세 번 시도했지만 끝내지 못해 발사하지 못했다',
               'prepare-failed': '로그인이나 달력 준비에 실패해 발사하지 못했다',
               'chrome-failed': 'Chrome 을 띄우지 못해 발사하지 못했다',
               'lingering-process': '같은 자리에서 다른 실행이 돌고 있어 시작하지 않았다'}
    return '✘', reasons.get(result, f'주문 전에 멈췄다({result}) - 콘솔 로그를 본다')


def cmd_status(a):
    cfg = load_cfg()
    day = a.date or today().isoformat()
    runs = ac.plan_runs(cfg, day)
    trip = cfg['trip']
    say(f'{day} · KE{trip["flight"]} {trip["origin"]}→{trip["destination"]} {trip["date"]}')
    got = False
    for run in runs:
        report = latest_report(day, run['port'])
        intent = read_json(ROOT / run['stateDir'] / f'order-intent-{day}.json')
        signal = read_json(ROOT / (run['waitsFor'] or run['writes'])) if (run['waitsFor'] or run['writes']) else None
        mark, text = run_outcome(run, day, report, intent, signal)
        got = got or mark == '✔'
        say(f'  {mark} {run["account"]:<8} {cabin_ko(run["cabin"]):<6} (자리 {run["port"]})  {text}')
    if os.name == 'nt':
        for t in scheduled_tasks():
            if t['next']:
                say(f'  예약: {t["name"]} 다음 실행 {t["next"]}')
    say()
    if got:
        say('좌석은 결제해야 내 것이 된다. 결제하지 않으면 풀린다(2026-09 실측 10~32분).')
    say(f'자세한 기록: {REPORTS.relative_to(ROOT)} 의 *-console.log (읽는 법은 docs/samples/README.md)')
    return 0


# ---------------------------------------------------------------------------
def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8')
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog='astra', description='대한항공 마일리지 좌석을 개방 시각에 잡는다',
                                 epilog='처음이면 astra setup 부터. 자세한 사용법은 README.md')
    sub = ap.add_subparsers(dest='command', metavar='명령')
    sub.add_parser('setup', help='처음 한 번: 설정 파일을 만들고 PC 를 확인한다').set_defaults(fn=cmd_setup)
    p = sub.add_parser('login', help='계정마다 브라우저를 띄워 로그인해 둔다')
    p.add_argument('--account', default='', help='이 계정의 자리만')
    p.set_defaults(fn=cmd_login)
    p = sub.add_parser('check', help='실행 전 점검')
    p.add_argument('--online', action='store_true', help='실제로 로그인해서 달력까지 가 본다(3~4분)')
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser('plan', help='무엇이 언제 실행되는지 보여 준다')
    p.add_argument('--date', default='', help='실행하는 날 YYYY-MM-DD(기본: 좌석이 열리는 날)')
    p.add_argument('--commands', action='store_true', help='실제로 실행될 명령 줄도 보여 준다')
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser('rehearse', help='이미 열린 날짜로 끝까지 연습한다(미결제 예약 1건이 생긴다)')
    p.add_argument('--account', default='', help='연습할 계정(기본: 첫째 계정)')
    p.add_argument('--date', default='', help='연습에 쓸 이미 열린 출발일(기본: 목표와 같은 요일의 열린 날)')
    p.add_argument('--fire-in-min', type=float, default=6.0, help='연습 통과 뒤 발사까지 기다릴 분(기본 6)')
    p.add_argument('--yes', action='store_true', help='확인을 묻지 않는다')
    p.set_defaults(fn=cmd_rehearse)
    p = sub.add_parser('schedule', help='실전을 예약한다(Windows 작업 스케줄러)')
    p.add_argument('--date', default='', help='실행하는 날 YYYY-MM-DD(기본: 좌석이 열리는 날)')
    p.add_argument('--remove', action='store_true', help='이 설정의 예약을 취소한다')
    p.add_argument('--list', action='store_true', help='예약된 작업을 보여 준다')
    p.add_argument('--dry-run', action='store_true', help='등록하지 않고 무엇을 등록할지만 보여 준다')
    p.set_defaults(fn=cmd_schedule)
    p = sub.add_parser('run', help='지금 바로 실전을 시작한다(오늘 발사 12분 전까지)')
    p.add_argument('--only', default='', help='이 이름의 실행 하나만(이름은 astra plan 에 나온다)')
    p.add_argument('--again', action='store_true', help='오늘 이미 주문을 보낸 기록이 있어도 다시 보낸다')
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser('status', help='무슨 일이 있었는지 알려 준다')
    p.add_argument('--date', default='', help='그날의 결과 YYYY-MM-DD(기본: 오늘)')
    p.set_defaults(fn=cmd_status)
    a = ap.parse_args(argv)
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 0
    return a.fn(a)


if __name__ == '__main__':
    raise SystemExit(main())
