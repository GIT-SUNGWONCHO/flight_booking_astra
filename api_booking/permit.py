"""D4 주문 전송 안전성: 배타 전송권·발사 시각 고정·늦은 무장 차단.

전송권
  주문 요청(inputTravellers)을 보내기 **직전**에 전송권 파일 하나(`order-permit.json`,
  날짜와 무관)를 `O_CREAT|O_EXCL`로 만든다. 운영체제가 원자적으로 하나만 성공시키므로
  동시에 시작한 두 프로세스 중 하나만 보낸다. 자정을 넘긴 재시작·실행일이 다른 실행도 같은
  파일에 막힌다(검토 9aaff071 P1). 파일은 자동으로 지우지 않는다. 남아 있으면 이후 모든 전송을
  막는다. 사용자가 정상 예약 조회로 확인하고 직접 치우기 전에는 새 주문이 나가지 않는다.
  파일에는 실행일·실행 표지·PID·목표·시각만 쓰고 계정·주문 참조는 쓰지 않는다.
  생성 뒤 파일과 부모 디렉터리를 동기화한다(POSIX). Windows 디렉터리 동기화는 하지 못한다.

  한계: 같은 PC·같은 `dev-shots/state` 폴더를 쓰는 프로세스끼리만 막는다. 다른 PC·다른
  작업 폴더·브라우저에서 사람이 한 주문은 모른다. 네트워크 드라이브의 O_EXCL 보장은 확인하지 않았다.

발사 시각
  `--day`(KST 실행일)와 `--at`(시:분:초)을 합쳐 시작할 때 한 번 고정한다. 실행일이 오늘이
  아니거나 이미 지난 시각이면 시작하지 않는다. 무장이 발사 시각 뒤에 확인되거나, 대기에서
  깨어난 시각이 허용 지연을 넘으면 발사하지 않는다(README §3 순서 2).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dtime
import json
import os
import re
from pathlib import Path

PERMIT_NAME = 'order-permit.json'
DEFAULT_LATE_LIMIT = 3.0


def permit_path(state_dir):
    return Path(state_dir) / PERMIT_NAME


@dataclass(frozen=True)
class Permit:
    path: Path
    run_id: str


def _fsync_dir(directory):
    """부모 디렉터리 항목까지 영속화한다. 디렉터리를 열 수 없는 OS(Windows)에서는 건너뛴다."""
    if os.name == 'nt':
        return False
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return True


def durable_json(path, value):
    """임시 파일에 쓰고 동기화한 뒤 교체하고 디렉터리까지 동기화한다. 전송 전 기록용."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.tmp')
    data = json.dumps(value, ensure_ascii=False, indent=2).encode('utf-8')
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o644)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    _fsync_dir(path.parent)


def existing_permit(state_dir):
    """전송권이 이미 있으면 내용(읽을 수 없으면 표지)을 돌려준다. 실행일과 무관하다."""
    path = permit_path(state_dir)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return data if type(data) is dict else {'state': 'unreadable'}
    except (OSError, ValueError):
        return {'state': 'unreadable'}


def acquire(state_dir, *, day, run_id, target, now_iso):
    """전송권을 원자적으로 얻는다. 이미 있으면 None. 얻으면 내용·디렉터리 항목을 동기화한다."""
    path = permit_path(state_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0)
    try:
        fd = os.open(path, flags, 0o644)
    except FileExistsError:
        return None
    body = json.dumps({'day': day, 'runId': run_id, 'pid': os.getpid(), 'at': now_iso,
                       'target': target, 'meaning': 'order-send-permit; never auto-removed'},
                      ensure_ascii=False, indent=2).encode('utf-8')
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_dir(path.parent)
    return Permit(path, run_id)


def parse_at(day, at, tz):
    """실행일과 HH:MM:SS 를 합친 시간대 있는 발사 시각. 형식이 틀리면 ValueError."""
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day or ''):
        raise ValueError('invalid-day')
    if not re.fullmatch(r'\d{2}:\d{2}:\d{2}', at or ''):
        raise ValueError('invalid-at')
    hh, mm, ss = (int(x) for x in at.split(':'))
    return datetime.combine(datetime.strptime(day, '%Y-%m-%d').date(), dtime(hh, mm, ss), tz)


def start_check(*, day, now, fire_at=None):
    """시작 시 검사. 문제가 없으면 None, 있으면 이유."""
    if now.tzinfo is None:
        return 'naive-now'
    if now.date().isoformat() != day:
        return 'day-is-not-today'
    if fire_at is not None and now >= fire_at:
        return 'fire-time-passed'
    return None


def fire_check(*, day, now, fire_at=None, late_limit=DEFAULT_LATE_LIMIT):
    """발사 직전 검사. 정각 전·허용 지연 초과·실행일 불일치면 이유를 돌려준다."""
    if now.tzinfo is None:
        return 'naive-now'
    if now.date().isoformat() != day:
        return 'day-is-not-today'
    if fire_at is None:
        return None
    late = (now - fire_at).total_seconds()
    if late < 0:
        return 'before-fire-time'
    if late > late_limit:
        return 'too-late'
    return None
