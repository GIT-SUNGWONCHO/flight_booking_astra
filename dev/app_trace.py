"""아이폰 대한항공 앱의 호출을 mitmproxy 로 기록한다(애드온).

왜
  경쟁자가 웹 경로의 바닥(주문 송신 약 +1.9초)보다 앞선 흔적을 남긴다(9/21).
  더 빠른 입구가 네이티브 앱에 있는지 본 적이 없다(9/22 §7.4). 앱이 어느 호스트·경로로
  조회·운임·주문을 보내는지, 단계가 몇 개인지를 본다.

안전
  예약을 만드는 계열의 POST/PUT/PATCH(traveller/order/reserv/pnr/hold/purchase/ticket)는
  **프록시에서 403 으로 막는다.** 끝까지 눌러도 예약이 생기지 않는다.
  본문·헤더·쿠키·토큰은 저장하지 않는다. 호스트·경로(쿼리 제외)·메서드·상태·크기·시각만 남긴다.
  인증서 고정으로 TLS 가 실패한 연결은 **호스트 이름만** 남긴다(그 자체가 답의 절반이다).

사용 (집 PC)
  pip install mitmproxy
  mitmdump -s dev/app_trace.py
  아이폰: Wi-Fi 프록시 수동 → PC IP:8080, Safari 로 mitm.it 에서 프로필 설치 →
  설정 > 일반 > 정보 > 인증서 신뢰 설정에서 켠다. 앱에서 이미 열린 날짜를 조회하고
  운임·등급 선택 → 승객 화면까지만 간다. 끝나면 Ctrl+C, 프록시 끄고 프로필 삭제.
  결과: dev-shots/research/app-trace-<시각>.jsonl · .summary.json
"""
from __future__ import annotations
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'dev-shots' / 'research'

DANGER = re.compile(r'traveller|traveler|order|reserv|pnr|hold|purchase|ticket', re.I)
WRITE_METHODS = {'POST', 'PUT', 'PATCH'}


def must_block(method, path):
    return method.upper() in WRITE_METHODS and bool(DANGER.search(path))


def strip_query(path):
    return path.split('?', 1)[0]


def summarize(rows, tls_failed):
    """호스트별 호출 수, 쓰기 경로 목록, TLS 실패 호스트를 한 장으로."""
    hosts = {}
    for r in rows:
        hosts[r['host']] = hosts.get(r['host'], 0) + 1
    writes = sorted({(r['method'], r['host'], r['path']) for r in rows if r['method'] in WRITE_METHODS})
    return {
        'calls': len(rows),
        'hosts': dict(sorted(hosts.items(), key=lambda kv: -kv[1])),
        'writes': [f'{m} {h}{p}' for m, h, p in writes],
        'blocked': sorted({f"{r['method']} {r['host']}{r['path']}" for r in rows if r.get('blocked')}),
        'tlsFailedHosts': sorted(tls_failed),
    }


class AppTrace:
    def __init__(self, out_dir=OUT):
        out_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        self.jsonl = out_dir / f'app-trace-{stamp}.jsonl'
        self.summary_path = out_dir / f'app-trace-{stamp}.summary.json'
        self.t0 = time.time()
        self.rows = []
        self.tls_failed = set()

    def _rel(self, t):
        return round((t - self.t0) * 1000) if t else None

    def request(self, flow):
        req = flow.request
        path = strip_query(req.path)
        if must_block(req.method, path):
            from mitmproxy import http
            flow.response = http.Response.make(403, b'blocked by app_trace', {'Content-Type': 'text/plain'})
            flow.metadata['app_trace_blocked'] = True
            print(f'  ✂ 차단(예약 생성 안 됨) {req.method} {req.pretty_host}{path}', flush=True)

    def response(self, flow):
        req, resp = flow.request, flow.response
        start, end = getattr(req, 'timestamp_start', None), getattr(resp, 'timestamp_end', None)
        row = {
            'at': self._rel(start),
            'ms': round((end - start) * 1000) if start and end else None,
            'method': req.method,
            'host': req.pretty_host,
            'path': strip_query(req.path),
            'status': resp.status_code,
            'reqBytes': len(req.raw_content or b''),
            'respBytes': len(getattr(resp, 'raw_content', None) or b''),
            'blocked': bool(flow.metadata.get('app_trace_blocked')),
        }
        self.rows.append(row)
        with self.jsonl.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
        if row['method'] in WRITE_METHODS:
            print(f"[{row['at']:>7}ms] {row['method']} {row['host']}{row['path']} "
                  f"{row['status']} {row['ms']}ms {row['reqBytes']}B", flush=True)

    def tls_failed_client(self, data):
        sni = getattr(data.conn, 'sni', None)
        if sni and sni not in self.tls_failed:
            self.tls_failed.add(sni)
            print(f'  ! TLS 실패(인증서 고정 추정): {sni}', flush=True)

    def done(self):
        s = summarize(self.rows, self.tls_failed)
        self.summary_path.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(s, ensure_ascii=False, indent=2), flush=True)


# mitmdump -s 로 읽힐 때만 기록기를 만든다. 시험에서 import 할 때는 파일을 만들지 않는다.
addons = [AppTrace()] if 'mitmproxy' in sys.modules else []
