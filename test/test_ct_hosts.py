"""CT 호스트 추출기(dev/ct_hosts.py)의 정리·후보 판정 시험. 네트워크 없음."""
import sys
import unittest
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


if __name__ == '__main__':
    unittest.main()
