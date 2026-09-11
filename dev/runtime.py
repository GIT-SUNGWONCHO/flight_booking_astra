"""Shared run identity, atomic reports, explicit timing and rehearsal contract."""
from __future__ import annotations
import hashlib
import json
import os
import uuid
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))

def run_id():
    if "KE_RUN_ID" not in os.environ:
        os.environ["KE_RUN_ID"] = datetime.now(KST).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    return os.environ["KE_RUN_ID"]

def output_dir():
    return ROOT / "dev-shots" / "runs" / run_id()

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        for attempt in range(6):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.01 * (2 ** attempt))
    finally:
        tmp.unlink(missing_ok=True)

def resolve_time(spec, now=None):
    now = now or datetime.now(KST)
    if spec.startswith("+"):
        result = now + timedelta(seconds=float(spec[1:].rstrip("s")))
    elif "T" in spec:
        result = datetime.fromisoformat(spec)
        if result.tzinfo is None:
            raise ValueError("절대 시각에는 시간대가 필요합니다")
    else:
        parts = spec.split(":")
        result = now.replace(hour=int(parts[0]), minute=int(parts[1]),
                             second=int(parts[2]) if len(parts) > 2 else 0, microsecond=0)
    if result <= now:
        raise ValueError("발사 시각이 이미 지났습니다. 다음 날로 자동 연기하지 않습니다")
    return result

def new_run():
    identity = datetime.now(KST).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
    os.environ["KE_RUN_ID"] = identity
    output_dir().mkdir(parents=True)
    return identity

def build_hash():
    return hashlib.sha256((ROOT / "userscript/ke-award-macro.user.js").read_bytes()).hexdigest()


def runtime_hash():
    digest = hashlib.sha256()
    paths = list((ROOT / 'dev').glob('*.py')) + list((ROOT / 'dev').glob('*.ps1'))
    paths += list((ROOT / 'config').glob('*.json')) + [ROOT / 'ke_award/calendar_probe.js']
    paths += [ROOT / 'requirements.lock.txt', ROOT / 'userscript/ke-award-macro.user.js']
    for path in sorted(paths):
        digest.update(path.relative_to(ROOT).as_posix().encode())
        digest.update(b'\0')
        digest.update(path.read_bytes())
        digest.update(b'\0')
    return digest.hexdigest()

def measure_clock():
    """No OS setting changes. Return measured offset and RTT uncertainty explicitly."""
    cached = os.environ.get("KE_CLOCK")
    if cached:
        state = json.loads(cached)
        if 0 <= datetime.now(KST).timestamp() - state["measuredAt"] < 1800:
            return state
    sys.path.insert(0, str(ROOT))
    from ke_award.clock import _query
    samples = []
    for host in ("time.windows.com", "time.google.com"):
        try:
            offset, delay = _query(host, timeout=2)
            if 0 <= delay < 1:
                samples.append((delay, offset, host))
        except Exception:
            pass
    best = min(samples) if samples else None
    state = {"ok": bool(best), "offset": best[1] if best else 0.0,
             "uncertainty": best[0]/2 if best else None,
             "source": best[2] if best else "unmeasured-local-clock",
             "measuredAt": datetime.now(KST).timestamp()}
    os.environ["KE_CLOCK"] = json.dumps(state)
    return state

def heartbeat(role, state, **details):
    atomic_json(output_dir() / (role + "_status.json"), {
        "runId": run_id(), "pid": os.getpid(), "state": state,
        "workerToken": os.environ.get('KE_WORKER_TOKEN'),
        "at": datetime.now(KST).isoformat(), **details})

def check_report(value, identity):
    return isinstance(value, dict) and bool(value) and value.get("runId") == identity

def validate_rehearsal(watch, macro, identity, max_seconds=90, require_watch=True, mode="payment-window"):
    failures = []
    if require_watch and (not check_report(watch, identity) or watch.get("ok") is not True or not watch.get("samples")):
        failures.append("동일 실행의 유효한 좌석 표본이 없습니다")
    if not check_report(macro, identity):
        failures.append("동일 실행의 매크로 결과가 없습니다")
    else:
        complete = (macro.get('ok') is True and not macro.get('problem')
                    and isinstance(macro.get('seconds'), (float, int))
                    and 0 <= macro['seconds'] <= max_seconds)
        if mode == 'payment-window':
            complete = (complete and macro.get('paymentWindowTest') is True
                        and macro.get('dry') is False and macro.get('idx') == 17
                        and macro.get('total') == 17 and macro.get('payWindowReady') is True
                        and macro.get('paymentApprovalClicked') is False)
        elif mode == 'dry':
            complete = (complete and macro.get('dry') is True and macro.get('total') == 6
                        and macro.get('idx') == 6 and macro.get('dryReady') is True)
        else:
            complete = False
        if not complete:
            failures.append(f"{mode}: 실제 도착 화면/전체 단계/시간 기준을 통과하지 못했습니다")
    return failures
