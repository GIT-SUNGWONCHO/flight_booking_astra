"""실행 ID·목표·생존·heartbeat를 함께 읽는다. 프로세스나 브라우저를 변경하지 않는다."""
import ctypes
import json
import os
import time
from datetime import datetime
from pathlib import Path
from test_calendar import KST


def process_alive(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False
    if os.name == 'nt':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def inspect_worker(folder, role, expected, *, now=None, alive=process_alive):
    """ready는 대기 준비 판정이다. 실행 중/종료를 대기 준비로 오인하지 않는다."""
    folder = Path(folder)
    now = now or datetime.now(KST)
    result = {'ready': False, 'runId': folder.name, 'issues': []}
    issues = result['issues']
    try:
        status = json.loads((folder / (role + '_status.json')).read_text(encoding='utf-8'))
        filename = 'manual_booking.json' if role == 'manual-booking' else 'calendar_observer.json'
        report = json.loads((folder / filename).read_text(encoding='utf-8'))
        result.update(pid=status.get('pid'), state=status.get('state'), statusAt=status.get('at'))
        if status.get('runId') != folder.name or report.get('runId') != folder.name:
            issues.append('실행 ID 불일치')
        age = (now - datetime.fromisoformat(status['at'])).total_seconds()
        result['heartbeatAgeSeconds'] = round(age, 3)
        if not 0 <= age < 5:
            issues.append('heartbeat 만료 또는 시계 불일치')
        if not alive(status.get('pid')):
            issues.append('작업자 프로세스 없음')
        for key in ('runDate', 'origin', 'destination', 'departureDate'):
            if report.get('plan', {}).get(key) != expected.get(key):
                issues.append('일정 불일치: ' + key)
        if any(source.get('target') != expected['departureDate'] or source.get('openAt') != expected['openAt']
               for source in (status, report)):
            issues.append('작업자 목표 날짜 또는 정각 불일치')
        if report.get('rehearsal') or report.get('preview'):
            issues.append('리허설/미리보기 실행')
        if report.get('prepared') is not True:
            issues.append('준비 완료 기록 없음')
        if report.get('endedAt') or report.get('fire') or report.get('ok'):
            issues.append('이미 실행했거나 종료됨')
        states = ('ready-unarmed', 'armed-by-user') if role == 'manual-booking' else ('ready',)
        if status.get('state') not in states:
            issues.append('대기 상태 아님')
        if role == 'manual-booking':
            health = report.get('session', {})
            result['session'] = health
            if health.get('known') is not True:
                issues.append('로그인 만료 시각 미확인')
            elif health.get('coversOpen') is not True:
                issues.append('로그인이 정각 이후까지 유지되지 않음')
            config = report.get('configuration', {})
            if report.get('routeVerified') is not True:
                issues.append('실제 노선 확인 기록 없음')
            if config.get('cabin') != expected['cabin'] or config.get('leadMs') != expected['leadMs'] or config.get('startAt') != 'calendar':
                issues.append('예매 등급·선발사·진입점 불일치')
        else:
            for field, key in [('requestGapMs', 'gapMs'), ('maxInflight', 'maxInflight'),
                               ('uiReloadGapMs', 'uiReloadMs'), ('maxStartDelayMs', 'maxStartDelayMs')]:
                if report.get(field) != expected['observer'][key]:
                    issues.append('계측 설정 불일치: ' + key)
            if report.get('worker'):
                issues.append('실험용 Worker 실행')
    except (OSError, ValueError, KeyError, TypeError):
        issues.append('상태/결과 파일 누락 또는 형식 오류')
    result['ready'] = not issues
    return result


def wait_worker(meta, proc, role, marker, expected, root, seconds, *, deadline=None):
    import re
    limit = min(time.monotonic() + seconds, deadline) if deadline is not None else time.monotonic() + seconds
    while time.monotonic() < limit:
        if proc.poll() is not None:
            meta['health'] = {'ready': False, 'issues': ['준비 도중 작업자 종료'], 'exitCode': proc.returncode}
            return False
        try:
            match = re.search(r'(?m)^(\d{8}-\d{6}-[a-f0-9]{8}) ' + re.escape(marker), Path(meta['log']).read_text(encoding='utf-8'))
            if match:
                folder = Path(root) / 'dev-shots/runs' / match.group(1)
                health = inspect_worker(folder, role, expected)
                meta.update(runId=folder.name, status=str(folder / (role + '_status.json')),
                            workerPid=health.get('pid'), health=health)
                if health['ready']:
                    return True
        except OSError:
            pass
        time.sleep(min(0.2, max(0, limit - time.monotonic())))
    meta.setdefault('health', {'ready': False, 'issues': ['준비 메시지/heartbeat 대기 시간 초과']})
    return False
