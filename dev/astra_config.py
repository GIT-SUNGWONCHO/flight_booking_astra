"""실행 설정(config/run.json)을 읽고 검증한다. 누가(계정) 무엇을(노선·등급) 언제 잡을지가 여기 한 곳에 있다.

설정 파일이 없으면 2026-09 에 쓰던 구성 그대로 동작한다(9232·9243 = .env 의 스카이패스 계정,
나머지 = 네이버 연동). 그래서 이 모듈을 들여도 예전 명령은 전과 같이 돈다.

브라우저 자리(포트·프로필 폴더)는 다섯 개로 고정이다. 바꿀 수 있는 것은 '어느 계정이 어느 자리에
어떤 방식으로 로그인하는가'다.

  자리      쓰임                       프로필 폴더
  9232      계정 1 의 1순위 예매        .debug-profile
  9243      계정 1 의 대체 예매         .debug-profile3
  9242      계정 2 의 1순위 예매        .api-profile
  9244      계정 2 의 대체 예매         .api-profile2
  9233      계측(좌석 수 관찰)          .debug-profile2

비밀번호는 여기에 없다. .env 에만 있고 이 모듈은 '어느 이름의 값을 쓸지'만 정한다.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / 'config' / 'run.json'
EXAMPLE = ROOT / 'config' / 'run.example.json'

# 포트: (프로필 폴더, 계정 순번 또는 'observer', 역할)
SLOTS = {
    9232: ('.debug-profile', 0, 'first'),
    9243: ('.debug-profile3', 0, 'fallback'),
    9242: ('.api-profile', 1, 'first'),
    9244: ('.api-profile2', 1, 'fallback'),
    9233: ('.debug-profile2', 'observer', 'observer'),
}
BOOKING_PORTS = tuple(p for p, s in SLOTS.items() if s[2] != 'observer')
OBSERVER_PORT = 9233
MAX_ACCOUNTS = 2
LOGINS = ('skypass', 'naver')
CABINS = {'prestige': ('KEBONUSPR', '프레스티지'), 'economy': ('KEBONUSEY', '일반석')}
# 계정 순번별 .env 이름. 1번은 예전 이름 그대로다.
ENV_KEYS = (('KE_SKYPASS_ID', 'KE_SKYPASS_PW', 'KE_LOGIN_TAB'),
            ('KE_SKYPASS_ID_2', 'KE_SKYPASS_PW_2', 'KE_LOGIN_TAB_2'))
LEGACY_SKYPASS_PORTS = frozenset({9232, 9243})

_DATE = re.compile(r'\d{4}-\d{2}-\d{2}')
_HMS = re.compile(r'([01]\d|2[0-3]):[0-5]\d:[0-5]\d')
_HM = re.compile(r'([01]\d|2[0-3]):[0-5]\d')
DEFAULT_TIMES = {'open': '09:00:00', 'start': '08:20', 'capture': '08:38:00',
                 'health': '08:50:00', 'lastPrepare': '08:52:00'}
DEFAULT_TUNING = {'preFireMs': 800, 'fallbackDelayMs': 3500, 'fallbackWaitSeconds': 5.0}
# auto: 정해진 결제수단의 결제창까지 연다(한국 도착 = 현대카드, 한국 출발 = 네이버페이).
# manual: 동의·마일리지까지만 하고 결제수단 선택 앞에서 멈춘다.
PAYMENTS = ('auto', 'manual')


class ConfigError(ValueError):
    """설정 파일에 고칠 곳이 있다. 메시지는 사용자에게 그대로 보여 준다."""


def profile_for(port):
    slot = SLOTS.get(port)
    return slot[0] if slot else None


def load(path=None):
    """설정을 읽어 검증한 dict 를 돌려준다. 파일이 없으면 None(예전 구성으로 동작)."""
    path = Path(path) if path else CONFIG
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except ValueError as exc:
        raise ConfigError(f'{path.name} 를 읽을 수 없다(JSON 형식 오류): {exc}') from None
    return validate(raw, path.name)


def _strip(value):
    """'_' 로 시작하는 키는 설명이다. 버린다."""
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if not str(k).startswith('_')}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def validate(raw, name='run.json'):
    """모든 문제를 모아 한 번에 알려 준다. 통과하면 기본값을 채운 dict."""
    if not isinstance(raw, dict):
        raise ConfigError(f'{name}: 맨 바깥은 {{ }} 여야 한다')
    cfg, bad = _strip(raw), []

    trip = cfg.get('trip') if isinstance(cfg.get('trip'), dict) else {}
    if not trip:
        bad.append('trip 이 없다(노선·날짜)')
    for key in ('origin', 'destination'):
        if not re.fullmatch(r'[A-Z]{3}', str(trip.get(key, ''))):
            bad.append(f'trip.{key}: 공항 코드 세 글자(대문자)여야 한다 - 예: "FCO"')
    if trip.get('origin') and trip.get('origin') == trip.get('destination'):
        bad.append('trip.origin 과 trip.destination 이 같다')
    if not re.fullmatch(r'\d{1,4}', str(trip.get('flight', ''))):
        bad.append('trip.flight: 편명의 숫자만 적는다 - KE932 면 "932"')
    if not _DATE.fullmatch(str(trip.get('date', ''))):
        bad.append('trip.date: 잡으려는 출발일 YYYY-MM-DD')
    capture = trip.get('captureDate', 'auto')
    if capture != 'auto' and not _DATE.fullmatch(str(capture)):
        bad.append('trip.captureDate: "auto" 또는 이미 열린 날짜 YYYY-MM-DD')
    if capture != 'auto' and capture == trip.get('date'):
        bad.append('trip.captureDate 는 trip.date 와 다른 날이어야 한다(이미 열려 있는 날짜)')

    accounts = cfg.get('accounts') if isinstance(cfg.get('accounts'), list) else []
    if not 1 <= len(accounts) <= MAX_ACCOUNTS:
        bad.append(f'accounts: 계정을 1~{MAX_ACCOUNTS}개 적는다')
    names = []
    for i, acc in enumerate(accounts[:MAX_ACCOUNTS]):
        where = f'accounts[{i}]'
        if not isinstance(acc, dict):
            bad.append(f'{where}: {{ }} 여야 한다')
            continue
        label = str(acc.get('name', ''))
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,20}', label):
            bad.append(f'{where}.name: 영문·숫자·-·_ 로 1~20자(파일 이름에 쓴다)')
        names.append(label)
        if acc.get('login') not in LOGINS:
            bad.append(f'{where}.login: "skypass"(아이디·비밀번호) 또는 "naver"(네이버 연동)')
        if type(acc.get('mileage')) is not int or acc['mileage'] < 0:
            bad.append(f'{where}.mileage: 그 계정의 지금 마일리지 잔액을 숫자로 - 예: 65000')
        if acc.get('first') not in CABINS:
            bad.append(f'{where}.first: "prestige" 또는 "economy"')
        fallback = acc.get('fallback')
        if fallback is not None and fallback not in CABINS:
            bad.append(f'{where}.fallback: "prestige"·"economy" 또는 없음(null)')
        if fallback is not None and fallback == acc.get('first'):
            bad.append(f'{where}.fallback 이 first 와 같다. 대체는 다른 등급이어야 한다')
    if len(set(names)) != len(names):
        bad.append('accounts: name 이 겹친다')

    observer = cfg.get('observer')
    if observer is not None and observer not in names:
        bad.append(f'observer: 계측에 쓸 계정의 name 또는 null - 지금 값 {observer!r}')

    times = {**DEFAULT_TIMES, **(cfg.get('times') if isinstance(cfg.get('times'), dict) else {})}
    for key, pattern, form in (('open', _HMS, 'HH:MM:SS'), ('start', _HM, 'HH:MM'), ('capture', _HMS, 'HH:MM:SS'),
                               ('health', _HMS, 'HH:MM:SS'), ('lastPrepare', _HMS, 'HH:MM:SS')):
        if not pattern.fullmatch(str(times.get(key, ''))):
            bad.append(f'times.{key}: {form}')
    if not bad:
        order = [times['start'] + ':00', times['capture'], times['health'], times['lastPrepare'], times['open']]
        if order != sorted(order) or len(set(order)) != len(order):
            bad.append('times: start < capture < health < lastPrepare < open 순서여야 한다')

    tuning = {**DEFAULT_TUNING, **(cfg.get('tuning') if isinstance(cfg.get('tuning'), dict) else {})}
    if type(tuning['preFireMs']) is not int or not 0 <= tuning['preFireMs'] <= 2000:
        bad.append('tuning.preFireMs: 0~2000 사이 정수(ms)')
    if type(tuning['fallbackDelayMs']) is not int or not 0 <= tuning['fallbackDelayMs'] <= 30000:
        bad.append('tuning.fallbackDelayMs: 0~30000 사이 정수(ms)')
    if not isinstance(tuning['fallbackWaitSeconds'], (int, float)) or not 0 < tuning['fallbackWaitSeconds'] <= 60:
        bad.append('tuning.fallbackWaitSeconds: 0 초과 60 이하')

    payment = cfg.get('payment', 'auto')
    if payment not in PAYMENTS:
        bad.append('payment: "auto"(정해진 결제수단의 결제창까지) 또는 "manual"(결제수단 선택 앞에서 멈춤)')
    elif payment == 'auto' and 'ICN' not in (trip.get('origin'), trip.get('destination')):
        bad.append('payment: 인천(ICN)을 지나지 않는 노선은 정해진 결제수단이 없다. "manual" 로 둔다')

    if bad:
        raise ConfigError(f'{name} 에 고칠 곳이 {len(bad)}군데 있다:\n' + '\n'.join(f'  - {b}' for b in bad))
    return {'trip': {'origin': trip['origin'], 'destination': trip['destination'], 'flight': str(trip['flight']),
                     'date': trip['date'], 'captureDate': capture},
            'accounts': [{'name': a['name'], 'login': a['login'], 'mileage': a['mileage'],
                          'first': a['first'], 'fallback': a.get('fallback')} for a in accounts],
            'observer': observer, 'payment': payment, 'times': times, 'tuning': tuning}


def account_for(port, cfg):
    """이 자리에 로그인하는 계정(dict). 계측 자리는 observer 로 지정한 계정. 없으면 None."""
    slot = SLOTS.get(port)
    if not slot or cfg is None:
        return None
    if slot[1] == 'observer':
        return next((a for a in cfg['accounts'] if a['name'] == cfg['observer']), None)
    return cfg['accounts'][slot[1]] if slot[1] < len(cfg['accounts']) else None


def account_index(port, cfg):
    acc = account_for(port, cfg)
    return cfg['accounts'].index(acc) if acc is not None else None


def login_for(port, env, cfg):
    """이 자리의 로그인 방식: 'skypass' 또는 'naver'.

    설정이 없으면 예전 규칙이다: .env 의 KE_LOGIN_MODE=skypass 이고 9232·9243 일 때만 skypass.
    """
    if cfg is None:
        legacy = (env.get('KE_LOGIN_MODE') or '').strip().lower() == 'skypass'
        return 'skypass' if legacy and port in LEGACY_SKYPASS_PORTS else 'naver'
    acc = account_for(port, cfg)
    return acc['login'] if acc else 'naver'


def marker_ports(env, cfg):
    """로그인 방식 표시(.ke-login-mode)를 대조하는 자리. 다른 계정의 옛 세션으로 예매하는 것을 막는다.

    프로그램이 스스로 아이디·비밀번호로 로그인하는 자리만 대조한다. 네이버 연동 자리는 사람이 직접
    로그인하므로 표시가 남지 않는다.
    """
    if cfg is None:
        return LEGACY_SKYPASS_PORTS
    return frozenset(p for p in SLOTS if login_for(p, env, cfg) == 'skypass')


def credential_keys(port, cfg):
    """이 자리가 쓸 .env 이름 (아이디, 비밀번호, 탭). 값은 다루지 않는다."""
    index = account_index(port, cfg) if cfg is not None else 0
    return ENV_KEYS[index if index is not None and index < len(ENV_KEYS) else 0]


# ---------------------------------------------------------------------------
# 실행 계획: 설정 한 장을 api_day 실행 여러 개로 푼다
# ---------------------------------------------------------------------------
OPEN_DAYS_AHEAD = 360          # 출발 360일 전 09:00(KST)에 열린다(config/test_calendar.json 과 같다)
FIRST_PORTS = (9232, 9242)
FALLBACK_PORTS = (9243, 9244)


def open_day(target_iso):
    """그 출발일의 좌석이 열리는 날(YYYY-MM-DD)."""
    from datetime import date, timedelta
    return (date.fromisoformat(target_iso) - timedelta(days=OPEN_DAYS_AHEAD)).isoformat()


def fire_time(open_hms, delay_ms, pre_fire_ms=0):
    """(발사 시각 HH:MM:SS, 선발사 ms). 발사 시각은 초 단위라 나머지는 선발사로 맞춘다.

    delay_ms=3500 이면 ('09:00:04', 500) - 09:00:04 보다 500ms 먼저, 곧 개방 3.5초 뒤다.
    """
    h, m, s = (int(x) for x in open_hms.split(':'))
    if delay_ms <= 0:
        return open_hms, pre_fire_ms
    whole = -(-delay_ms // 1000)
    total = h * 3600 + m * 60 + s + whole
    return f'{total // 3600 % 24:02d}:{total // 60 % 60:02d}:{total % 60:02d}', whole * 1000 - delay_ms


def plan_runs(cfg, run_day):
    """설정을 실행 목록으로. 계정마다 1순위 하나, 대체가 있으면 하나 더.

    대체 실행은 같은 계정의 1순위가 남긴 신호 파일을 주문 직전에 본다. 1순위가 좌석을 잡았으면
    주문하지 않고, 거절됐을 때만 주문한다.
    """
    times, tuning, runs = cfg['times'], cfg['tuning'], []
    for index, acc in enumerate(cfg['accounts']):
        signal = f'dev-shots/gate/{run_day}-{acc["name"]}.json' if acc['fallback'] else ''
        base = {'account': acc['name'], 'login': acc['login'], 'mileage': acc['mileage']}
        runs.append({**base, 'name': f'{acc["name"]}-first', 'role': 'first', 'port': FIRST_PORTS[index],
                     'cabin': acc['first'], 'family': CABINS[acc['first']][0],
                     'at': times['open'], 'preFireMs': tuning['preFireMs'],
                     'stateDir': f'dev-shots/state-{FIRST_PORTS[index]}',
                     'writes': signal, 'waitsFor': '', 'waitSeconds': None,
                     'observer': bool(cfg['observer']) and index == 0})
        if acc['fallback']:
            at, pre = fire_time(times['open'], tuning['fallbackDelayMs'])
            runs.append({**base, 'name': f'{acc["name"]}-fallback', 'role': 'fallback',
                         'port': FALLBACK_PORTS[index], 'cabin': acc['fallback'],
                         'family': CABINS[acc['fallback']][0], 'at': at, 'preFireMs': pre,
                         'stateDir': f'dev-shots/state-{FALLBACK_PORTS[index]}',
                         'writes': '', 'waitsFor': signal, 'waitSeconds': tuning['fallbackWaitSeconds'],
                         'observer': False})
    return runs


def api_day_args(run, cfg, *, again=False):
    """그 실행의 실전(live) api_day 인자."""
    trip, times = cfg['trip'], cfg['times']
    args = ['--mode', 'live', '--target-date', trip['date'], '--capture-iso', trip['captureDate'],
            '--origin', trip['origin'], '--destination', trip['destination'], '--flight', trip['flight'],
            '--port', str(run['port']), '--family', run['family'], '--own-mileage', str(run['mileage']),
            '--pre-fire-ms', str(run['preFireMs']), '--at', run['at'], '--health-at', times['health'],
            '--capture-not-before', times['capture'], '--reprep-cutoff', times['lastPrepare'],
            '--live-state-dir', run['stateDir']]
    if run['writes']:
        args += ['--order-outcome-file', run['writes']]
    if run['waitsFor']:
        args += ['--order-gate-file', run['waitsFor'], '--order-gate-timeout', str(run['waitSeconds'])]
    if run['observer']:
        args += ['--observer']
    if again:
        args += ['--again']
    if cfg.get('payment', 'auto') != 'auto':
        args += ['--payment', cfg['payment']]
    return args


def rehearsal_args(cfg, account, target_iso, *, fire_in_min=6.0, capture='auto'):
    """연습(리허설)용 api_day 인자. 이미 열린 날짜의 일반석으로 그 계정의 1순위 자리에서 끝까지 간다."""
    trip = cfg['trip']
    index = [a['name'] for a in cfg['accounts']].index(account['name'])
    return ['--mode', 'rehearsal', '--target-date', target_iso, '--capture-iso', capture,
            '--origin', trip['origin'], '--destination', trip['destination'], '--flight', trip['flight'],
            '--port', str(FIRST_PORTS[index]), '--family', CABINS['economy'][0],
            '--own-mileage', str(account['mileage']), '--fire-in-min', str(fire_in_min),
            *(['--payment', cfg['payment']] if cfg.get('payment', 'auto') != 'auto' else [])]
