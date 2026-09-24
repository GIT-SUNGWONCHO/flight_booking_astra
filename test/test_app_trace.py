"""앱 호출 기록기(dev/app_trace.py)의 차단 판정·기록·요약 시험. mitmproxy·네트워크 없음."""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'dev'))
import app_trace as at  # noqa: E402


def fake_flow(method, path, host='www.koreanair.com', status=200, t=100.0):
    req = NS(method=method, path=path, pretty_host=host, timestamp_start=t, raw_content=b'{"a":1}')
    resp = NS(status_code=status, timestamp_end=t + 1.5, raw_content=b'x' * 10)
    return NS(request=req, response=resp, metadata={})


class BlockTests(unittest.TestCase):
    def test_order_creating_writes_are_blocked(self):
        for p in ('/api/ap/booking/traveller/inputTravellers', '/api/x/createOrder',
                  '/api/x/reservation', '/api/x/pnr', '/api/x/seatHold', '/api/x/purchase'):
            self.assertTrue(at.must_block('POST', p), p)
        self.assertTrue(at.must_block('put', '/api/x/order'))

    def test_search_and_fare_are_not_blocked(self):
        for p in ('/api/ap/booking/avail/awardAvailability', '/api/ap/booking/avail/fareInformation'):
            self.assertFalse(at.must_block('POST', p), p)

    def test_reads_are_never_blocked(self):
        self.assertFalse(at.must_block('GET', '/api/x/order/status'))

    def test_query_is_stripped(self):
        self.assertEqual(at.strip_query('/api/x?token=secret'), '/api/x')


class RecordTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tr = at.AppTrace(Path(self.tmp.name))
        self.tr.t0 = 100.0
        made = []
        fake_http = types.ModuleType('mitmproxy.http')
        fake_http.Response = NS(make=lambda *a: made.append(a) or NS(status_code=a[0]))
        self.made = made
        self._saved = {k: sys.modules.get(k) for k in ('mitmproxy', 'mitmproxy.http')}
        sys.modules['mitmproxy'] = types.ModuleType('mitmproxy')
        sys.modules['mitmproxy'].http = fake_http
        sys.modules['mitmproxy.http'] = fake_http

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
        self.tmp.cleanup()

    def test_blocked_request_gets_a_403_and_is_marked(self):
        f = fake_flow('POST', '/api/ap/booking/traveller/inputTravellers')
        self.tr.request(f)
        self.assertEqual(self.made[0][0], 403)
        self.assertTrue(f.metadata['app_trace_blocked'])

    def test_row_keeps_no_body_or_query(self):
        f = fake_flow('POST', '/api/ap/booking/avail/awardAvailability?k=secret', t=100.25)
        self.tr.request(f)
        self.tr.response(f)
        row = json.loads(self.tr.jsonl.read_text(encoding='utf-8'))
        self.assertEqual(row['path'], '/api/ap/booking/avail/awardAvailability')
        self.assertEqual((row['at'], row['ms'], row['reqBytes'], row['respBytes']), (250, 1500, 7, 10))
        self.assertNotIn('secret', self.tr.jsonl.read_text(encoding='utf-8'))
        self.assertFalse(self.made)

    def test_tls_failure_records_host_once(self):
        d = NS(conn=NS(sni='mapi.example.com'))
        self.tr.tls_failed_client(d)
        self.tr.tls_failed_client(d)
        self.assertEqual(self.tr.tls_failed, {'mapi.example.com'})

    def test_summary_counts_hosts_writes_and_blocks(self):
        for m, p, h in (('POST', '/api/a/avail', 'www.koreanair.com'), ('GET', '/x.js', 'cdn.example'),
                        ('POST', '/api/a/order', 'www.koreanair.com')):
            f = fake_flow(m, p, host=h)
            self.tr.request(f)
            self.tr.response(f)
        self.tr.tls_failed.add('pinned.example')
        self.tr.done()
        s = json.loads(self.tr.summary_path.read_text(encoding='utf-8'))
        self.assertEqual(s['hosts'], {'www.koreanair.com': 2, 'cdn.example': 1})
        self.assertEqual(s['writes'], ['POST www.koreanair.com/api/a/avail', 'POST www.koreanair.com/api/a/order'])
        self.assertEqual(s['blocked'], ['POST www.koreanair.com/api/a/order'])
        self.assertEqual(s['tlsFailedHosts'], ['pinned.example'])


if __name__ == '__main__':
    unittest.main()
