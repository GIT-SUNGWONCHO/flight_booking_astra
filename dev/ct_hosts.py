"""인증서 투명성(CT) 로그에서 koreanair.com 하위 호스트 이름을 전부 뽑는다.

왜
  9/22 §7.1 은 우리가 이름을 추측한 호스트 7개만 DNS 로 확인했다. 그래서 "앱 전용 호스트는
  없다"를 철회했다. 공개 인증서에는 실제로 발급된 호스트 이름이 남으므로 추측이 필요 없다.
  앱 트래픽 관찰(dev/app_trace.py) 전에 후보를 좁힌다.

안전
  crt.sh 공개 자료만 읽는다. 대한항공 서버에는 아무 요청도 보내지 않는다.
  --resolve 를 주면 후보 호스트의 DNS 조회만 한다(HTTP 요청 없음).

사용 (집 PC, 실전 준비와 무관하게 아무 때나)
  .venv/Scripts/python.exe dev/ct_hosts.py
  .venv/Scripts/python.exe dev/ct_hosts.py --resolve
  결과: dev-shots/research/ct-hosts-<시각>.json
"""
from __future__ import annotations
import argparse
import json
import re
import socket
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'dev-shots' / 'research'
DOMAIN = 'koreanair.com'
URL = 'https://crt.sh/?q=%25.{d}&output=json'

# 앱·API 쪽일 가능성이 있는 이름. 순위 매기기용이지 판정이 아니다.
INTEREST = re.compile(r'api|app|mobile|mapi|gw|gateway|ios|iphone|native|award|booking|mileage|skypass|ibe|m\.', re.I)


def hosts_from_ct(rows, domain=DOMAIN):
    """crt.sh JSON 행에서 호스트 이름을 모은다. 와일드카드·대소문자·중복 정리."""
    out = set()
    for r in rows:
        for name in str(r.get('name_value', '')).split('\n'):
            name = name.strip().lower().lstrip('*.')
            if name == domain or name.endswith('.' + domain):
                out.add(name)
    return sorted(out)


def interesting(hosts):
    return [h for h in hosts if INTEREST.search(h[:-len(DOMAIN)])]


def resolve(host):
    try:
        return sorted({a[4][0] for a in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)})
    except OSError:
        return []


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--resolve', action='store_true', help='관심 후보의 DNS 만 조회(HTTP 없음)')
    ap.add_argument('--timeout', type=int, default=120)
    a = ap.parse_args(argv)

    with urllib.request.urlopen(URL.format(d=DOMAIN), timeout=a.timeout) as r:
        rows = json.load(r)
    hosts = hosts_from_ct(rows)
    cand = interesting(hosts)
    result = {'certs': len(rows), 'hosts': hosts, 'candidates': cand}
    if a.resolve:
        result['dns'] = {h: resolve(h) for h in cand}

    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"ct-hosts-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'인증서 {len(rows)}건 · 호스트 {len(hosts)}개 · 관심 후보 {len(cand)}개')
    for h in cand:
        dns = result.get('dns', {}).get(h)
        print(f'  {h}' + (f'  → {", ".join(dns) if dns else "DNS 없음"}' if a.resolve else ''))
    print(f'저장: {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
