"""D2 메모리 캡처와 문서 전환 뒤 발송을 격리 headless Chromium 에서 시험한다. 실사이트 연결 없음.

모든 요청은 route 가 로컬 응답으로 채우고 DNS 도 브라우저 안에서 전부 실패시킨다.
확인하는 것: 주문 요청을 네트워크에서 막아도 페이지 후킹이 본문·헤더를 캡처하는지,
캡처를 메모리로 옮긴 뒤 새 달력 문서에서 같은 헤더·본문으로 보낼 수 있는지,
다른 오리진 문서에서는 보내지 않는지. 사이트 서버가 그 요청을 받아 주는지는 모른다.
"""
import json
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

import site_drive
import transport

AVAIL = '/api/ap/booking/avail/awardAvailability'
FARE = '/api/ap/booking/avail/fareInformation'
ORDER = '/api/ap/booking/traveller/inputTravellers'
SITE = 'https://www.koreanair.com'

# 사이트가 게이트까지 오며 보내는 세 요청을 흉내 낸다. 헤더 이름은 관측된 이름, 값은 합성.
SITE_JS = """
const h = {'Content-Type': 'application/json', 'channel': 'pc', 'ksessionId': 'FAKE-SESSION'};
fetch('%s', {method: 'POST', headers: h, body: JSON.stringify({currency: 'KRW', segmentList: [{departureDate: '20270907'}], travelers: [{}]})}).catch(()=>{});
fetch('%s', {method: 'POST', headers: h, body: JSON.stringify({recommendList: [{recommendId: 'R', flightId: 'F'}]})}).catch(()=>{});
const x = new XMLHttpRequest(); x.open('POST', '%s');
x.setRequestHeader('Content-Type', 'application/json'); x.setRequestHeader('ksessionId', 'FAKE-SESSION');
x.send(JSON.stringify({travellerInfoList: [{travellerId: 'T'}], contactList: [{}, {}], preferLanguage: 'KO'}));
""" % (AVAIL, FARE, ORDER)

# 재준비가 조회까지만 가고 멈춘 SPA 화면. 운임·주문 요청은 보내지 않는다.
PARTIAL_JS = """
fetch('%s', {method: 'POST', headers: {'Content-Type': 'application/json', 'ksessionId': 'NEW'},
  body: JSON.stringify({currency: 'KRW', segmentList: [{departureDate: '20270910'}], travelers: [{}]})}).catch(()=>{});
""" % AVAIL


class MemoryCaptureBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(
            headless=True, args=['--host-resolver-rules=MAP * ~NOTFOUND'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.context = self.browser.new_context(service_workers='block')
        self.addCleanup(self.context.close)
        self.reached = []

        def fixture(route):
            req = route.request
            parts = urlsplit(req.url)
            self.reached.append({'origin': f'{parts.scheme}://{parts.netloc}', 'path': parts.path,
                                 'headers': req.headers, 'body': req.post_data})
            if parts.path.startswith('/api/'):
                route.fulfill(status=200, content_type='application/json', body='{"ok": true}')
            elif parts.path.startswith('/payment/gate'):
                route.fulfill(status=200, content_type='text/html; charset=utf-8',
                              body='<title>gate</title><script>' + SITE_JS + '</script>')
            elif parts.path == '/spa/partial':
                route.fulfill(status=200, content_type='text/html; charset=utf-8',
                              body='<title>spa</title><script>window.again = () => {'
                                   + PARTIAL_JS + '};</script>')
            else:
                route.fulfill(status=200, content_type='text/html; charset=utf-8',
                              body='<title>page</title>')

        self.context.route('**/*', fixture)
        transport.arm(self.context)
        self.page = self.context.new_page()

    def api_hits(self, path):
        return [r for r in self.reached if r['path'] == path]

    def prepare(self):
        """capture_pass 와 같은 방식으로 주문 요청을 막은 채 게이트 문서를 통과시킨다."""
        self.page.goto(SITE + '/booking/calendar-fare-bonus')
        watch = site_drive.HandoffWatch(self.context, page=self.page)
        watch.start()
        try:
            self.page.goto(SITE + '/payment/gate/RT/NR')
            self.page.wait_for_timeout(1500)
        finally:
            watch.stop()
        return watch.records(), transport.snapshot(self.page)

    def test_order_is_blocked_but_all_three_requests_are_captured(self):
        records, snap = self.prepare()
        self.assertEqual(self.api_hits(ORDER), [])
        orders = [r for r in records if r['path'] == ORDER]
        self.assertEqual((len(orders), orders[0]['blocked']), (1, True))
        self.assertEqual(sorted(snap), sorted([AVAIL, FARE, ORDER]))
        self.assertEqual(json.loads(snap[ORDER].body)['preferLanguage'], 'KO')
        self.assertEqual(snap[ORDER].headers.get('ksessionId'), 'FAKE-SESSION')
        self.assertNotIn('FAKE-SESSION', repr(snap))

    def test_memory_capture_is_sent_from_a_new_calendar_document(self):
        _, snap = self.prepare()
        self.page.goto(SITE + '/booking/calendar-fare-bonus')
        # 새 문서의 페이지 안 캡처는 비어 있다(9/13 08:44 와 같은 상황).
        self.assertEqual(transport.snapshot(self.page), {})
        before = len(self.api_hits(AVAIL))
        body = json.dumps({'currency': 'KRW', 'segmentList': [{'departureDate': '20270909'}],
                           'travelers': [{}]})
        out = transport.send_request(self.page, snap[AVAIL], body=body)
        self.assertEqual((out['ok'], out['status']), (True, 200))
        hit = self.api_hits(AVAIL)[before]
        self.assertEqual(hit['body'], body)
        self.assertEqual(hit['headers'].get('ksessionid'), 'FAKE-SESSION')
        out = transport.send_request(self.page, snap[ORDER])
        self.assertEqual(out['status'], 200)
        self.assertEqual(len(self.api_hits(ORDER)), 1)   # 준비 때는 막혔고 발사 1회만 도달
        # 메모리 발송은 페이지 후킹의 캡처를 덮어쓰지 않는다(원본 fetch 사용).
        self.assertEqual(transport.snapshot(self.page), {})

    def test_new_generation_does_not_mix_previous_captures(self):
        # 검토 165a57e4 P1: 같은 SPA 문서에서 재준비가 조회까지만 가면 이전 운임·주문을 섞지 않는다.
        self.page.goto(SITE + '/spa/partial')
        self.page.evaluate(SITE_JS.replace('\n', ' '))     # 첫 준비: 세 요청(주문은 막지 않은 로컬 픽스처)
        self.page.wait_for_timeout(500)
        self.assertEqual(sorted(transport.snapshot(self.page)), sorted([AVAIL, FARE, ORDER]))
        since = transport.begin_generation(self.page)
        self.page.evaluate('window.again()')
        self.page.wait_for_timeout(500)
        snap = transport.snapshot(self.page, since=since)
        self.assertEqual(sorted(snap), [AVAIL])
        self.assertEqual(snap[AVAIL].headers.get('ksessionId'), 'NEW')

    def test_record_older_than_the_generation_is_ignored(self):
        # 비우기 뒤에도 이전 시각의 기록이 들어오면(늦게 끝난 본문 읽기 등) 이번 세대로 쓰지 않는다.
        self.page.goto(SITE + '/spa/partial')
        since = transport.begin_generation(self.page)
        self.page.evaluate("""(t) => window.__KE_TX.seen.push({path: '%s', url: location.origin + '%s',
            origin: location.origin, method: 'POST', headers: {}, body: '{}', complete: true, at: t - 10})"""
                           % (FARE, FARE), since)
        self.assertEqual(transport.snapshot(self.page, since=since), {})
        self.assertEqual(sorted(transport.snapshot(self.page)), [FARE])

    def test_another_origin_document_never_sends(self):
        _, snap = self.prepare()
        self.page.goto('https://other.example/')
        before = len(self.reached)
        out = transport.send_request(self.page, snap[AVAIL])
        self.assertEqual(out, {'ok': False, 'error': 'origin-changed'})
        self.assertEqual(len(self.reached), before)


if __name__ == '__main__':
    unittest.main()
