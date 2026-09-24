"""인증서 투명성(CT) 로그에서 koreanair.com 하위 호스트 이름을 전부 뽑는다.

왜
  9/22 §7.1 은 우리가 이름을 추측한 호스트 7개만 DNS 로 확인했다. 그래서 "앱 전용 호스트는
  없다"를 철회했다. 공개 인증서에는 실제로 발급된 호스트 이름이 남으므로 추측이 필요 없다.
  앱 트래픽 관찰(dev/app_trace.py) 전에 후보를 좁힌다.

안전
  crt.sh(실패하면 Cert Spotter) 공개 자료만 읽는다. 대한항공 서버에는 아무 요청도 보내지 않는다.
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
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'dev-shots' / 'research'
DOMAIN = 'koreanair.com'
# crt.sh 는 부하가 크면 404/502/타임아웃을 낸다. 만료 제외(결과가 작다) → 전체 순으로 시도하고,
# 둘 다 실패하면 Cert Spotter 로 넘어간다.
CRTSH = ('https://crt.sh/?q=%25.{d}&output=json&exclude=expired',
         'https://crt.sh/?q=%25.{d}&output=json')
CERTSPOTTER = 'https://api.certspotter.com/v1/issuances?domain={d}&include_subdomains=true&expand=dns_names'
UA = {'User-Agent': 'Mozilla/5.0 astra-ct-hosts'}

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


def names_from_certspotter(items):
    """Cert Spotter 응답을 crt.sh 행 모양으로 바꾼다."""
    return [{'name_value': '\n'.join(it.get('dns_names', []))} for it in items]


def get_json(url, timeout):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_rows(timeout, tries=3, get=get_json, log=print, pause=5):
    """crt.sh 두 가지를 각각 재시도하고, 안 되면 Cert Spotter 를 쪽수 넘겨 가며 읽는다."""
    for tpl in CRTSH:
        url = tpl.format(d=DOMAIN)
        for i in range(1, tries + 1):
            try:
                rows = get(url, timeout)
                log(f'crt.sh 성공 ({len(rows)}건): {url}')
                return rows, 'crt.sh'
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                log(f'crt.sh 실패 {i}/{tries}: {e}')
                if i < tries:
                    time.sleep(pause)
    rows, after = [], None
    for _ in range(100):
        url = CERTSPOTTER.format(d=DOMAIN) + (f'&after={urllib.parse.quote(str(after))}' if after else '')
        page = get(url, timeout)
        if not page:
            break
        rows += names_from_certspotter(page)
        after = page[-1].get('id')
    log(f'Cert Spotter 사용 ({len(rows)}건)')
    return rows, 'certspotter'


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

    rows, source = fetch_rows(a.timeout)
    hosts = hosts_from_ct(rows)
    cand = interesting(hosts)
    result = {'source': source, 'certs': len(rows), 'hosts': hosts, 'candidates': cand}
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
