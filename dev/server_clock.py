"""서버 시각 추정기(네이비즘 방식). 응답 헤더 `Date` 의 초 전환 지점을 찾아 서버 시계를 좁힌다.

왜 필요한가
  우리 발사 기준은 NTP 기준시각이다. 그런데 9/20 계측에서 좌석이 09:00보다 **0.85초 먼저** 풀렸다.
  대한항공 서버가 생각하는 09:00:00 이 우리 기준 몇 시인지 알면 선발사를 그 기준에 맞출 수 있다.

방법
  같은 연결로 `HEAD` 를 촘촘히 보내고 (송신, 수신, Date 초)를 기록한다. `Date=d` 응답은 서버 시계로
  d초 안에서 만들어졌으므로 **경계(d+1 시작)는 그 요청 송신 이후**이고, `Date=d+1` 응답이 있으면
  **경계는 그 수신 이전**이다. 여러 경계의 교집합으로 좁힌다.

한계
  `Date` 는 앞단(CDN) 서버가 찍을 수 있어 실제 예약 처리 서버의 시계와 다를 수 있다. 네이비즘도 같다.
  쿠키·인증을 쓰지 않고 로그인 세션과 무관한 별도 연결로 보낸다(예매 세션에 영향 없음).

  python dev/server_clock.py --seconds 4 --gap 0.05
"""
from __future__ import annotations
import argparse
import http.client
import json
import sys
import time
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
HOST = 'www.koreanair.com'
PATH = '/kr/ko'


def probe(host=HOST, path=PATH, seconds=4.0, gap=0.05, timeout=5.0):
    """(송신, 수신, Date 초) 표본 목록. 연결은 재사용하고 실패하면 다시 연결한다."""
    rows, failures = [], 0
    conn = http.client.HTTPSConnection(host, timeout=timeout)
    end = time.time() + seconds
    while time.time() < end:
        started = time.time()
        try:
            conn.request('HEAD', path)
            response = conn.getresponse()
            response.read()
            received = time.time()
            header = response.getheader('Date')
        except Exception:  # noqa: BLE001 - 네트워크 실패는 표본에서 빼고 다시 연결한다
            failures += 1
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = http.client.HTTPSConnection(host, timeout=timeout)
            time.sleep(gap)
            continue
        if header:
            try:
                rows.append({'sent': started, 'received': received,
                             'date': parsedate_to_datetime(header).timestamp(),
                             'rttMs': (received - started) * 1000})
            except (TypeError, ValueError):
                failures += 1
        time.sleep(max(0.0, gap - (time.time() - started)))
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    return rows, failures


def estimate(rows):
    """초 전환 구간들과 그 교집합. 값은 '로컬이 서버보다 빠른 정도(ms)'.

    Date=d 응답의 생성 시각은 [송신, 수신] 안이고 경계(d+1)보다 앞선다 → 경계 > 송신.
    Date=d+1 응답의 생성 시각은 경계 이후이고 [송신, 수신] 안이다 → 경계 < 수신.
    """
    seconds = sorted({r['date'] for r in rows})
    edges = []
    for d in seconds:
        if d + 1 not in seconds:
            continue
        low = max((r['sent'] for r in rows if r['date'] == d), default=None)
        high = min((r['received'] for r in rows if r['date'] == d + 1), default=None)
        if low is None or high is None or high <= low:
            continue
        edges.append({'serverSecond': d + 1,
                      'aheadLowMs': round((low - (d + 1)) * 1000, 1),
                      'aheadHighMs': round((high - (d + 1)) * 1000, 1),
                      'widthMs': round((high - low) * 1000, 1)})
    out = {'samples': len(rows), 'edges': edges,
           'rttMedianMs': round(sorted(r['rttMs'] for r in rows)[len(rows) // 2], 1) if rows else None}
    if edges:
        low = max(e['aheadLowMs'] for e in edges)
        high = min(e['aheadHighMs'] for e in edges)
        out['localAheadOfServerMs'] = None if high <= low else [round(low, 1), round(high, 1)]
        if out['localAheadOfServerMs'] is None:   # 교집합이 비면 경계마다 흔들린 것이다
            out['localAheadOfServerMs'] = [round(min(e['aheadLowMs'] for e in edges), 1),
                                           round(max(e['aheadHighMs'] for e in edges), 1)]
            out['edgesDisagree'] = True
    return out


def measure(host=HOST, path=PATH, seconds=4.0, gap=0.05, offset=None):
    """서버 시계 추정 한 번. offset(기준시각-로컬, 초)을 주면 기준시각 대비 값도 낸다."""
    rows, failures = probe(host, path, seconds, gap)
    out = estimate(rows)
    out.update(host=host, path=path, seconds=seconds, gapMs=round(gap * 1000), failures=failures,
               measuredAt=datetime.now().isoformat(timespec='milliseconds'))
    window = out.get('localAheadOfServerMs')
    if window and offset is not None:
        # 기준시각 = 로컬 + offset. 서버가 기준시각보다 늦은 정도 = (로컬이 서버보다 빠른 정도) + offset.
        out['trueAheadOfServerMs'] = [round(window[0] + offset * 1000, 1),
                                      round(window[1] + offset * 1000, 1)]
        out['clockOffsetMs'] = round(offset * 1000, 1)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description='서버 시각 추정기(Date 헤더 초 전환)')
    ap.add_argument('--host', default=HOST)
    ap.add_argument('--path', default=PATH)
    ap.add_argument('--seconds', type=float, default=4.0)
    ap.add_argument('--gap', type=float, default=0.05)
    ap.add_argument('--no-ntp', action='store_true', help='NTP 보정 없이 로컬 시계 기준만')
    a = ap.parse_args()
    offset = None
    if not a.no_ntp:
        import os
        from runtime import measure_clock
        os.environ.pop('KE_CLOCK', None)
        state = measure_clock()
        offset = state.get('offset') if state.get('ok') else None
    result = measure(a.host, a.path, a.seconds, a.gap, offset)
    print(json.dumps(result, ensure_ascii=False, indent=1))
