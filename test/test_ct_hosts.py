"""CT 호스트 추출기(dev/ct_hosts.py)의 정리·후보 판정 시험. 네트워크 없음."""
import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
import ct_hosts as ct  # noqa: E402


class HostTests(unittest.TestCase):
    def test_wildcards_case_and_duplicates_are_cleaned(self):
        rows = [{'name_value': '*.KoreanAir.com\nwww.koreanair.com'},
                {'name_value': 'www.koreanair.com\nmapi.koreanair.com'}]
        self.assertEqual(ct.hosts_from_ct(rows), ['koreanair.com', 'mapi.koreanair.com', 'www.koreanair.com'])

    def test_lookalike_domains_are_dropped(self):
        rows = [{'name_value': 'evilkoreanair.com\nkoreanair.com.example\nx.koreanair.com'}]
        self.assertEqual(ct.hosts_from_ct(rows), ['x.koreanair.com'])

    def test_candidates_look_at_the_subdomain_part_only(self):
        hosts = ['koreanair.com', 'www.koreanair.com', 'mapi.koreanair.com',
                 'ios-gw.koreanair.com', 'news.koreanair.com']
        self.assertEqual(ct.interesting(hosts), ['mapi.koreanair.com', 'ios-gw.koreanair.com'])


class FetchTests(unittest.TestCase):
    def test_crtsh_is_retried_then_certspotter_is_paged(self):
        calls = []
        pages = [[{'id': '1', 'dns_names': ['a.koreanair.com']}, {'id': '2', 'dns_names': ['b.koreanair.com']}],
                 [{'id': '3', 'dns_names': ['*.c.koreanair.com']}], []]

        def get(url, timeout):
            calls.append(url)
            if 'crt.sh' in url:
                raise urllib.error.HTTPError(url, 404, 'Not Found', {}, None)
            return pages.pop(0)

        rows, source = ct.fetch_rows(10, tries=2, get=get, log=lambda m: None, pause=0)
        self.assertEqual(source, 'certspotter')
        self.assertEqual(sum('crt.sh' in u for u in calls), 4)
        self.assertIn('after=2', calls[-2])
        self.assertEqual(ct.hosts_from_ct(rows), ['a.koreanair.com', 'b.koreanair.com', 'c.koreanair.com'])

    def test_crtsh_success_stops_early(self):
        rows, source = ct.fetch_rows(10, get=lambda u, t: [{'name_value': 'x.koreanair.com'}],
                                     log=lambda m: None, pause=0)
        self.assertEqual((source, len(rows)), ('crt.sh', 1))


if __name__ == '__main__':
    unittest.main()
