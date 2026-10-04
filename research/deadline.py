"""좌석 소멸 마감과 요청 왕복 실측으로 API 연속 처리의 시간 예산을 계산한다.

저장된 실행 폴더만 읽는다. 사이트 접속·주문 전송 없음.

로컬 시계가 틀려도 결과가 흔들리지 않도록, 시간 축을 **서버 Date 헤더**로 다시 잡는다.
로컬 NTP 측정값은 비교용으로만 쓰고 마감 판정의 기준으로 삼지 않는다.
"""
import argparse
import email.utils
import json
from datetime import datetime
from pathlib import Path

# 좌석 확보 후보 요청까지의 직렬 연결. 후보 판정이 바뀌면 함께 바꾼다.
CHAIN = ('awardAvailability', 'fareInformation', 'inputTravellers')
BOOKING_API = ('calendarFareMatrix', 'awardAvailability', 'fareInformation', 'inputTravellers')


def _epoch_ms(text):
    return datetime.fromisoformat(text).timestamp() * 1000


def _header_ms(text):
    return email.utils.parsedate_to_datetime(text).timestamp() * 1000


def server_offset(events):
    """서버 Date 헤더로 (서버시각 - 로컬시각)의 구간을 좁힌다.

    각 응답의 생성 시점은 로컬 축에서 [sentAt, readAt] 안에 있고, Date 헤더는 1초
    해상도라 생성 시각이 [date, date+1000)이다. 두 조건을 모든 표본에서 교차한다.
    """
    low, high, used = None, None, 0
    for event in events:
        if event.get('kind') != 'response':
            continue
        header = (event.get('cache') or {}).get('date')
        sent, read = event.get('sentAt'), event.get('readAt')
        if not header or not isinstance(sent, (int, float)) or not isinstance(read, (int, float)):
            continue
        try:
            stamp = _header_ms(header)
        except (TypeError, ValueError):
            continue
        lower, upper = stamp - read, stamp + 1000 - sent
        low = lower if low is None else max(low, lower)
        high = upper if high is None else min(high, upper)
        used += 1
    if used < 2 or low is None or low > high:
        return {'samples': used, 'usable': False,
                'why': '표본이 부족하거나 구간이 교차하지 않는다. 서버 축을 세우지 않는다.'}
    return {'samples': used, 'usable': True, 'lowMs': round(low, 1), 'highMs': round(high, 1),
            'limit': 'Date 헤더는 1초 해상도이며 중간 캐시·프록시 생성 시각일 수 있다. 하한이 더 단단하다.',
            'direction': 'lowMs는 하한이다. 이 값으로 세운 서버 축의 경과시간은 실제보다 이르게 표기된다.'}


def depletion(report, offset_low):
    """P 있음→없음 전이를 서버 축으로 다시 표기한다.

    Date 헤더는 응답 생성 시각의 하한이므로 '있음'을 뒤로 밀어 마감을 늦추고,
    수신 시각은 생성 시각의 상한이므로 '없음'을 앞으로 당겨 구간을 좁힌다.
    """
    nominal = _epoch_ms(report['openAt'])
    open_local = nominal - offset_low                 # 서버 시계가 09:00:00을 가리킨 로컬 시각
    present, absent = [], []
    for event in report.get('events', []):
        if event.get('kind') != 'response' or event.get('state') != 'valid':
            continue
        if event.get('p') not in (True, False):
            continue
        header = (event.get('cache') or {}).get('date')
        served = None
        if header:
            try:
                served = (_header_ms(header) - nominal) / 1000
            except (TypeError, ValueError):
                served = None
        sent = (event['sentAt'] - open_local) / 1000
        read = (event['readAt'] - open_local) / 1000 if isinstance(event.get('readAt'), (int, float)) else None
        sample = {'id': event.get('id'), 'sentSinceServerOpen': round(sent, 3),
                  'readSinceServerOpen': None if read is None else round(read, 3),
                  'servedAtLeastSinceServerOpen': served,
                  'provenAt': round(max(sent, served if served is not None else sent), 3),
                  'provenBy': None if read is None else round(read, 3)}
        (present if event['p'] else absent).append(sample)
    if not present or not absent:
        return {'observed': False, 'why': 'P 있음·없음 표본을 모두 얻지 못했다.'}
    last = max(present, key=lambda s: s['provenAt'])
    usable = [s for s in absent if s['provenBy'] is not None]
    if not usable:
        return {'observed': False, 'why': '없음 표본의 수신 시각이 없어 상한을 세우지 못했다.'}
    first = min(usable, key=lambda s: s['provenBy'])
    if first['provenBy'] <= last['provenAt']:
        return {'observed': True, 'consistent': False, 'lastPresent': last, 'firstAbsent': first,
                'why': '있음·없음 표본의 시간 순서가 겹쳐 전이 구간을 세우지 않는다.'}
    return {'observed': True, 'consistent': True, 'lastPresent': last, 'firstAbsent': first,
            'depletionWindowSinceServerOpen': [last['provenAt'], first['provenBy']],
            'designDeadlineSeconds': last['provenAt'],
            'deadlineMeaning': '이 시각까지는 서버가 P 있음으로 응답했다. 좌석 요청은 그 전에 도착해야 한다.',
            'limit': '달력 P 조건의 관측이다. 실제 판매 완료 시각·좌석 수가 아니며 이 날짜 한 번의 관측이다.'}


def roundtrips(rows, open_local):
    """개방 이후 실제 예매 API의 왕복 실측만 모은다."""
    result = {}
    for row in rows:
        name = row.get('path', '').rsplit('/', 1)[-1]
        timing = row.get('timing') or {}
        start, end = timing.get('startTime', 0), timing.get('responseEnd', -1)
        if name not in BOOKING_API or start <= 0 or end < 0:
            continue
        since = (start - open_local) / 1000
        if since < -5:
            continue
        result.setdefault(name, []).append({'sentSinceServerOpen': round(since, 3),
                                            'ms': round(end, 1), 'httpStatus': row.get('status')})
    return result


def forecast(chain_rtt, deadline, lead=0.0, gap=0.0):
    """직렬 연결의 각 요청이 가장 빨라도 언제 나가는지 계산한다. 예측이 아니라 산술이다."""
    steps, at = [], -lead
    for name, ms in chain_rtt:
        steps.append({'step': name, 'sentSinceServerOpen': round(at, 3),
                      'roundTripMs': None if ms is None else round(ms, 1),
                      'receivedSinceServerOpen': None if ms is None else round(at + ms / 1000, 3)})
        if ms is None:
            return {'steps': steps, 'usable': False,
                    'why': '왕복 실측이 없는 단계가 있어 도달 시각을 계산하지 않는다.'}
        at += ms / 1000 + gap
    last = steps[-1]
    verdict = 'unknown' if deadline is None else (
        'within' if last['sentSinceServerOpen'] <= deadline else 'misses')
    return {'steps': steps, 'usable': True, 'leadSeconds': lead, 'clientGapSeconds': gap,
            'candidateSentSinceServerOpen': last['sentSinceServerOpen'],
            'deadlineSeconds': deadline, 'verdict': verdict,
            'assumptions': ['세 요청이 직렬로 필요하다고 가정', '화면·클릭 대기를 0으로 가정',
                            '관측한 왕복 시간이 유지된다고 가정', '좌석 확보 요청은 후보이며 미확정'],
            'warning': '성능 보장이나 하한이 아니다. 실제 API 연속 처리는 아직 측정되지 않았다.'}


def evaluate(run_folder, observer_folder=None, lead=0.0, gap=0.0):
    run_folder = Path(run_folder)
    run = json.loads((run_folder / 'manual_booking.json').read_text(encoding='utf-8'))
    network = json.loads((run_folder / 'network.json').read_text(encoding='utf-8'))
    if run['runId'] != network.get('runId'):
        raise ValueError('예매 보고서와 네트워크 실행 ID가 다릅니다')

    clock = {'localNtpOffsetMs': round(run['clock']['offset'] * 1000, 1), 'serverOffset': None,
             'ourAxisLateBySeconds': None,
             'note': '예매 실행의 로컬 NTP 값은 발사 시각 계산에 쓰인 값이다.'}
    limits = ['한 날짜·한 번의 관측이다. 다른 날짜·등급·노선으로 일반화하지 않는다.',
              '좌석 확보 요청은 후보이며 서버의 실제 보유 시각은 확인되지 않았다.',
              '서버 축은 Date 헤더 하한으로 세운다. 표기된 "개방 후 n초"는 낙관값이며 실제는 그 이상이다.']

    result = {'sourceRun': run['runId'], 'observerRun': None, 'clock': clock,
              'depletion': {'observed': False, 'why': '계측 실행을 함께 주지 않았다.'},
              'measuredRoundTrips': {}, 'forecast': None, 'limits': limits}

    offset_low = run['clock']['offset'] * 1000            # 서버 축 근거가 없으면 로컬 값으로 둔다
    if observer_folder:
        observer = json.loads((Path(observer_folder) / 'calendar_observer.json').read_text(encoding='utf-8'))
        result['observerRun'] = observer['runId']
        bounds = server_offset(observer.get('events', []))
        clock['serverOffset'] = bounds
        if bounds['usable']:
            offset_low = bounds['lowMs']
            clock['ourAxisLateBySeconds'] = round((offset_low - clock['localNtpOffsetMs']) / 1000, 3)
            if abs(clock['ourAxisLateBySeconds']) >= 0.3:
                limits.insert(0, '로컬 시계가 서버보다 %.3f초 늦었다. 이 실행의 로컬 기준 "개방 후 n초"는 '
                                 '그만큼 이르게 표기됐다.' % clock['ourAxisLateBySeconds'])
        result['depletion'] = depletion(observer, offset_low)

    open_local = _epoch_ms(run['openAt']) - offset_low
    result['fireSinceServerOpen'] = round((run['fire']['localAt'] - open_local) / 1000, 3)
    result['measuredRoundTrips'] = roundtrips(network['rows'], open_local)

    deadline = result['depletion'].get('designDeadlineSeconds') if result['depletion'].get('observed') else None
    measured = {name: max(s['ms'] for s in samples) for name, samples in result['measuredRoundTrips'].items()}
    proxy = max(measured.values()) if measured else None
    chain = [(name, measured.get(name, proxy)) for name in CHAIN]
    result['chainRoundTripSource'] = {name: ('measured' if name in measured else
                                             ('proxy-slowest-measured' if proxy else 'none'))
                                      for name in CHAIN}
    result['forecast'] = forecast(chain, deadline, lead=lead, gap=gap)
    if proxy and any(name not in measured for name in CHAIN):
        limits.append('왕복 실측이 없는 단계는 같은 실행에서 가장 느린 예매 API 왕복으로 대체했다. 실측이 아니다.')
    return result


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='저장된 실행으로 좌석 마감 예산을 계산한다. 사이트 접속 없음.')
    ap.add_argument('run_folder')
    ap.add_argument('--observer', help='같은 날 계측 실행 폴더')
    ap.add_argument('--lead', type=float, default=0.0, help='첫 요청 선발사 초')
    ap.add_argument('--gap', type=float, default=0.0, help='응답→다음 요청 사이 클라이언트 대기 초')
    ap.add_argument('--output')
    a = ap.parse_args()
    text = json.dumps(evaluate(a.run_folder, a.observer, a.lead, a.gap), ensure_ascii=False, indent=2)
    if a.output:
        path = Path(a.output); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    print(text)
