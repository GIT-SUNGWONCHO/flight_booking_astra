"""08:50 읽기 전용 점검. 새로고침·대기 시작·대기 해제·프로세스 종료를 하지 않는다."""
import argparse
import json
from datetime import datetime
from pathlib import Path
from runtime import atomic_json
from session_health import session_health
from test_calendar import plan, KST
from worker_health import inspect_worker

ROOT = Path(__file__).resolve().parent.parent
CAL = '/booking/calendar-fare-bonus'


def latest_preparation(day, root=ROOT):
    candidates = []
    for path in (root / 'dev-shots/runs').glob('*/prepare_day.json'):
        try:
            report = json.loads(path.read_text(encoding='utf-8'))
            if report.get('plan', {}).get('runDate') == day and report.get('preview') is False:
                candidates.append((path.parent.name, path, report))
        except (OSError, ValueError):
            continue
    return max(candidates, key=lambda x: x[0])[1:] if candidates else (None, None)


def check(day, *, root=ROOT, inspect_browser=True):
    expected = plan(day)
    result = {'checkedAt': datetime.now(KST).isoformat(), 'plan': expected,
              'readOnly': True, 'booking': {'ready': False}, 'observer': {'ready': False}}
    if not expected['enabled']:
        result['skipReason'] = expected['skipReason']
        return result
    path, parent = latest_preparation(day, root)
    result['preparationReport'] = str(path) if path else None
    for key, role in [('booking', 'manual-booking'), ('observer', 'calendar-observer')]:
        meta = (parent or {}).get(key, {}).get('service', {})
        identity = meta.get('runId')
        if identity and Path(identity).name == identity:
            result[key] = inspect_worker(root / 'dev-shots/runs' / identity, role, expected)
        else:
            result[key] = {'ready': False, 'issues': ['해당 날짜의 당일 준비/작업자 기록 없음']}
    if not inspect_browser:
        return result
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        for key, port in [('booking', 9232), ('observer', 9233)]:
            section = result[key]
            try:
                browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{port}', timeout=5000)
                context = browser.contexts[0]
                pages = [p for p in context.pages if CAL in p.url]
                if len(pages) != 1:
                    section['issues'].append('전용 달력 탭이 정확히 하나가 아님')
                    continue
                page = pages[0]
                section['browser'] = {'port': port, 'title': page.title()}
                if key == 'booking':
                    hud = page.evaluate('''() => {
                      const H=window.KE_HUD,R=window.KE_REC;
                      if(!H || !R) return null;
                      return {armed:H.state.armed, targetKst:H.state.targetKst,
                        date:R.state.expectDate, cabin:R.state.cabin, leadMs:H.state.leadMs,
                        startAt:H.state.startAt, allowPay:R.state.allowPay,
                        total:R.state.steps.length, buttonDisabled:document.querySelector('#ke-arm')?.disabled ?? true};
                    }''')
                    section['hud'] = hud
                    section['armedByUser'] = bool(hud and hud['armed'])
                    if not hud:
                        section['issues'].append('HUD 없음')
                    elif (hud['date'] != expected['departureDate'][5:] or hud['cabin'] != expected['cabin']
                          or hud['targetKst'] != datetime.fromisoformat(expected['openAt']).strftime('%Y-%m-%d %H:%M:%S')
                          or hud['leadMs'] != expected['leadMs'] or hud['startAt'] != 'calendar'
                          or hud['allowPay'] is not True or hud['total'] != 17 or hud['buttonDisabled']):
                        section['issues'].append('실제 HUD 설정 불일치 또는 미리보기 버튼 비활성')
                    health = session_health(context, datetime.fromisoformat(expected['openAt']).timestamp())
                    section['liveSession'] = health
                    if health.get('known') is not True or health.get('coversOpen') is not True:
                        section['issues'].append('현재 로그인 만료 확인 필요')
                else:
                    identity = section.get('runId')
                    helper_url = 'https://www.koreanair.com/__astra_observer__?run=' + (identity or '')
                    helpers = [p for p in context.pages if p.url == helper_url]
                    section['helperPresent'] = len(helpers) == 1
                    if len(helpers) != 1 or not helpers[0].evaluate("typeof window.sampler === 'object'"):
                        section['issues'].append('동일 실행의 계측 보조 탭/타이머 없음')
            except Exception as exc:
                section['issues'].append('브라우저 읽기 실패: ' + type(exc).__name__)
            finally:
                section['ready'] = not section['issues']
    result['booking']['readyForOpening'] = result['booking']['ready'] and result['booking'].get('armedByUser', False)
    result['booking']['userAction'] = None if result['booking']['readyForOpening'] else '문제 해결 후 목표 확인·사용자가 대기 시작. 점검기는 상태를 바꾸지 않음.'
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--day', required=True)
    args = parser.parse_args()
    result = check(args.day)
    path = ROOT / 'dev-shots/checks' / (datetime.now(KST).strftime('%Y%m%d-%H%M%S') + '.json')
    atomic_json(path, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(str(path))
    # 계측 상태는 예매 점검의 종료 코드와 독립. 어느 프로그램도 중단하지 않는다.
    raise SystemExit(0 if result.get('skipReason') or result['booking'].get('readyForOpening') else 2)
