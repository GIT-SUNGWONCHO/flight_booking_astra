"""확정된 공통 일정. 예매·계측·전날 준비가 같은 노선과 날짜를 읽는다."""
from __future__ import annotations
import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KST = timezone(timedelta(hours=9))


def load_calendar():
    cfg = json.loads((ROOT / 'config/test_calendar.json').read_text(encoding='utf-8'))
    excluded = cfg.get('excludedDepartureWeekdays', [])
    if any(type(d) is not int or d not in range(7) for d in excluded) or len(set(excluded)) == 7:
        raise ValueError('출발일 제외 요일 설정 오류: 월요일=0~일요일=6, 최소 하루는 허용해야 합니다')
    return cfg


def plan(day: date | str):
    day = date.fromisoformat(day) if isinstance(day, str) else day
    cfg = load_calendar()
    if not date.fromisoformat(cfg['start']) <= day <= date.fromisoformat(cfg['end']):
        raise ValueError('확정 일정 범위 밖입니다. 임의로 다음 날에 실행하지 않습니다.')
    active = cfg['weekends'] or day.weekday() < 5
    target = next(t for t in cfg['targets'] if date.fromisoformat(t['runDate']) >= day)
    departure = day + timedelta(days=cfg['openDaysAhead'])
    skip_reason = None if active else '사용자 확정: 실행일 주말 테스트 없음'
    if departure.weekday() in cfg.get('excludedDepartureWeekdays', []):
        active = False
        skip_reason = '사용자 확정: 출발일 ' + '월화수목금토일'[departure.weekday()] + '요일 테스트 제외'
    if day.isoformat() == target['runDate'] and departure.isoformat() != target['departureDate']:
        raise ValueError('중요 목표 출발일과 개방 규칙 불일치')
    origin, destination, flight = target['origin'], target['destination'], target.get('flight')
    routes = cfg.get('weekdayRoutes')
    # 2026-09-19 사용자 확정: 이날부터는 출발일 요일로 노선을 정한다(월·수·토 FCO→ICN).
    if routes and day >= date.fromisoformat(routes['fromRunDate']):
        rule = next((r for r in routes['rules'] if departure.weekday() in r['weekdays']), routes['default'])
        origin, destination, flight = rule['origin'], rule['destination'], rule.get('flight')
        if day.isoformat() == target['runDate'] and (origin, destination) != (target['origin'], target['destination']):
            raise ValueError('중요 목표 노선과 요일 규칙 불일치')
    return dict(runDate=day.isoformat(), enabled=active,
                skipReason=skip_reason,
                origin=origin, destination=destination, flight=flight,
                departureDate=departure.isoformat(), cabin=cfg['cabin'],
                important=day.isoformat() == target['runDate'] and target.get('important', True),
                openAt=f"{day}T{cfg['openTime']}+09:00", leadMs=cfg['leadMs'],
                prepareAt=f"{day}T{cfg['prepareTime']}+09:00",
                bookingPort=cfg['bookingPort'], observerPort=cfg['observerPort'],
                bookingStart=cfg['bookingStart'],observer=cfg['observer'])


def rehearsal_plan(run_day, now=None):
    result = plan(run_day)
    if not result['enabled']:
        raise ValueError(result['skipReason'])
    now = (now or datetime.now(KST)).astimezone(KST)
    latest = now.date() + timedelta(days=360 if now.hour >= 9 else 359)
    excluded = load_calendar().get('excludedDepartureWeekdays', [])
    while latest.weekday() in excluded:
        latest -= timedelta(days=1)
    result['rehearsalDepartureDate'] = latest.isoformat()
    result['rehearsalCabin'] = '일반석'
    result['requiresSiteAvailabilityCheck'] = True
    return result


def render():
    cfg = load_calendar()
    lines = ['# 확정 테스트 일정', '',
             '역할: 언제 어느 방향·출발일을 시험하는지 보여주는 생성 문서다. 운영 절차는 [README](README.md#operations), 실행 결과와 현재 과제는 [NOW](NOW.md)를 따른다.', '',
             '## 1. 일정 원본과 적용 범위', '',
             f"- 확정일: {cfg['confirmedOn']}. 원본: [config/test_calendar.json](config/test_calendar.json).",
             f"- 적용 기간: {cfg['start']} ~ {cfg['end']}. 아래 일별 구분에서 휴무 여부를 확인한다.",
             '- 실행일은 테스트하는 날, 출발일은 실제 여행일이다. 예매와 계측은 같은 계획을 읽고 독립 실행한다.',
             '- 실행일의 주말 휴무와 별도로, 출발일이 일요일인 테스트는 양방향 모두 제외한다(사용자 확정 일정 규칙이며 양방향 운항 중단을 검증했다는 뜻은 아니다).',
             '- 2026-09-19부터 노선은 출발일 요일로 정한다: 월·수·토 FCO→ICN(KE932), 그 외 CDG→ICN(KE902). 9/25 중요 목표는 FCO→ICN.',
             '- 이 파일은 dev/test_calendar.py의 --render로 생성한다. 날짜·노선 수정은 확정된 원본 설정에 먼저 반영한다.', '',
             '## 2. 일일 테스트 일정', '',
             '| 실행일(2026) | 출발일(2027) | 노선 | 편 | 구분 |', '|---|---|---|---|---|']
    day = date.fromisoformat(cfg['start'])
    while day <= date.fromisoformat(cfg['end']):
        p = plan(day)
        label = p['skipReason'].removeprefix('사용자 확정: ') if not p['enabled'] else ('중요 목표' if p['important'] else '일일 테스트')
        lines.append(f"| {day} | {p['departureDate']} | {p['origin']} → {p['destination']} | {('KE' + p['flight']) if p.get('flight') else '-'} | {label} |")
        day += timedelta(days=1)
    lines += ['', '## 3. 사전 리허설 일정과 적용 원칙', '',
              '- 9/9: 인천→파리 최초 리허설. 9/11: 9/14 중요 목표 대비 점검.',
              '- 9/14 테스트 종료 후: 파리→인천 방향 전환 리허설. 9/24: 9/25 최종 점검.',
              '- 같은 방향의 전날 추가 리허설은 실패·코드·설정 변경 시 실시. 매일 08:20 점검은 유지.',
              '- 사전 리허설은 다음 실행일의 방향 + 현재 이미 열린 출발일을 사용한다. 출발일 제외 요일은 이전 허용일로 거슬러 선택하고, 사이트에서 실제 운항을 확인한다.',
              '- 리허설 단계·목표 복원·사용자 역할은 [운영 명세](README.md#operations)를 따른다. 이 표는 리허설 완료를 뜻하지 않는다.', '',
              '## 4. 결과와 변경 관리', '',
              '- 수행 결과·미검증 범위는 [NOW](NOW.md), 실측 근거는 [FACTS](FACTS.md)에 남긴다.',
              '- 일정 변경 후 생성본과 원본을 대조한다. 휴무나 범위 밖 실행을 임의로 다른 날짜로 보충하지 않는다.', '']
    return '\n'.join(lines)


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--day', default=datetime.now(KST).date().isoformat())
    ap.add_argument('--render', action='store_true')
    a = ap.parse_args()
    if a.render:
        (ROOT / 'CALENDAR.md').write_text(render(), encoding='utf-8')
    else:
        current = plan(a.day)
        print(json.dumps(rehearsal_plan(a.day) if current['enabled'] else current, ensure_ascii=False, indent=2))
