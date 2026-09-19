"""D1 인계 관찰기를 격리 headless Chromium 에서 시험한다. 실사이트 연결 없음.

모든 요청은 route 가 로컬 응답으로 채우고, DNS 도 브라우저 안에서 전부 실패시킨다.
확인하는 것은 Playwright route 차단·request 이벤트·해제의 실제 동작이다.
사이트 게이트가 재진입 때 어떤 요청을 보내는지는 여기서 알 수 없다(미관측).
실제 9232 는 CDP 연결이고 서비스 워커를 막지 않으므로 이 시험과 조건이 다르다.
"""
import json
import time
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

import site_drive
from handoff import ORDER_PATH

BAGGAGE = '/api/et/ibeSupport/baggagePolicies'
TEXT = '<div>09월 07일 (화)</div><div>35,000 마일</div><div>325,500 원</div>'


def gate_html(actions):
    """kind: fetch/xhr/beacon 은 POST 본문, get/xhr-get 은 GET(관측 계약의 참조 요청 형태)."""
    js = []
    for kind, path, body in actions:
        p, b = json.dumps(path), json.dumps(body)
        if kind == 'get':
            js.append("fetch(%s).catch(()=>{});" % p)
        elif kind == 'xhr-get':
            js.append("(()=>{const x=new XMLHttpRequest();x.open('GET',%s);x.send();})();" % p)
        elif kind == 'fetch':
            js.append("fetch(%s,{method:'POST',headers:{'Content-Type':'application/json'},"
                      "body:%s}).catch(()=>{});" % (p, b))
        elif kind == 'xhr':
            js.append("(()=>{const x=new XMLHttpRequest();x.open('POST',%s);"
                      "x.setRequestHeader('Content-Type','application/json');x.send(%s);})();" % (p, b))
        elif kind == 'beacon':
            js.append("navigator.sendBeacon(%s,new Blob([%s],{type:'application/json'}));" % (p, b))
    return '<title>gate fixture</title>' + TEXT + '<script>' + ''.join(js) + '</script>'


class HandoffWatchBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(
            headless=True, args=['--host-resolver-rules=MAP * ~NOTFOUND'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_gate(self, actions, reference='NEWREF', other_tab=None):
        context = self.browser.new_context(service_workers='block')
        self.addCleanup(context.close)
        reached = []

        def fixture(route):
            path = urlsplit(route.request.url).path
            reached.append(path)
            if path.startswith('/payment/gate'):
                route.fulfill(status=200, content_type='text/html; charset=utf-8',
                              body=gate_html(actions))
            elif path.startswith('/api/'):
                route.fulfill(status=200, content_type='application/json', body='{}')
            else:
                route.fulfill(status=200, content_type='text/html', body='<title>start</title>')

        context.route('**/*', fixture)
        page = context.new_page()
        page.goto('https://www.koreanair.com/booking/calendar-fare-bonus')
        if other_tab is not None:
            # 같은 컨텍스트의 다른 탭이 인계 중에 참조 요청을 계속 보낸다.
            tab = context.new_page()
            tab.goto('https://www.koreanair.com/other-tab')
            tab.evaluate("p => { window.__t = setInterval(() => fetch(p).catch(()=>{}), 200); }",
                         other_tab)
        logs = []
        out = site_drive.gate_handoff(page, date='2027-09-07', mileage='35,000',
                                      reference=reference, ordered_at=time.monotonic(),
                                      log=logs.append, wait_ms=1500)
        return out, reached, logs, page

    def test_same_reference_passes(self):
        out, reached, logs, _ = self.run_gate([('xhr-get', BAGGAGE + '?orderId=NEWREF&x=1', '')])
        self.assertEqual(out['order'], 'same-order-reference')
        self.assertTrue(out['matched'])
        self.assertIn(BAGGAGE, reached)
        self.assertNotIn('NEWREF', repr(out) + ''.join(logs))

    def test_previous_reference_is_refused(self):
        out, _, _, _ = self.run_gate([('get', BAGGAGE + '?orderId=OLDREF&x=1', '')])
        self.assertEqual(out['order'], 'other-order')
        self.assertFalse(out['matched'])

    def test_post_body_reference_is_not_the_observed_contract(self):
        out, _, _, _ = self.run_gate([('fetch', BAGGAGE, '{"orderId":"NEWREF"}')])
        self.assertEqual(out['order'], 'reference-unreadable')
        self.assertFalse(out['matched'])

    def test_reference_from_another_tab_does_not_pass(self):
        # 검토 d2574cfe P1: 대상 게이트는 참조를 보내지 않고 다른 탭만 새 참조를 보낸다.
        out, reached, _, _ = self.run_gate([], other_tab=BAGGAGE + '?orderId=NEWREF')
        self.assertIn(BAGGAGE, reached)
        self.assertEqual(out['order'], 'reference-unobserved')
        self.assertFalse(out['matched'])

    def test_page_order_request_never_reaches_the_network(self):
        out, reached, _, _ = self.run_gate([('fetch', ORDER_PATH, '{}'),
                                            ('get', BAGGAGE + '?orderId=NEWREF', '')])
        self.assertNotIn(ORDER_PATH, reached)
        self.assertEqual(out['order'], 'new-order-blocked')
        self.assertFalse(out['matched'])

    def test_beacon_order_request_is_blocked_or_reported(self):
        # 전송 경로가 달라도 조용히 통과하면 안 된다. 막혔으면 blocked, 나갔으면 requested.
        out, reached, _, _ = self.run_gate([('beacon', ORDER_PATH, '{}'),
                                            ('get', BAGGAGE + '?orderId=NEWREF', '')])
        self.assertFalse(out['matched'])
        if ORDER_PATH in reached:
            self.assertEqual(out['order'], 'new-order-requested')
        else:
            self.assertEqual(out['order'], 'new-order-blocked')

    def test_guard_is_removed_after_handoff(self):
        _, reached, _, page = self.run_gate([('get', BAGGAGE + '?orderId=NEWREF', '')])
        page.evaluate("p => fetch(p,{method:'POST',body:'{}'}).then(r=>r.status)", ORDER_PATH)
        self.assertIn(ORDER_PATH, reached)


if __name__ == '__main__':
    unittest.main()


CARD_HTML = """<title>card fixture</title>
<label><input type=radio name=pm id=rad-kor {kor}> 한국발행 신용/체크카드</label>
<label><input type=radio name=pm id=rad-other> 해외발행 카드</label>
<select id=sel-korCardCompany style="display:{show}"><option value="">카드사 선택</option>
<option value=SS>삼성카드</option>{hd}<option value=KB>KB국민카드</option></select>
<script>
  document.getElementById('rad-kor').addEventListener('change', () =>
    document.getElementById('sel-korCardCompany').style.display = 'block');
  window.changes = 0;
  document.getElementById('sel-korCardCompany').addEventListener('change', () => window.changes++);
</script>"""


class HyundaiCardSelectTests(unittest.TestCase):
    """현대카드 선택기(select_hyundai_card)를 로컬 HTML 로 시험한다. 실사이트 DOM 과 같다고 보장하지 않는다."""

    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_select(self, kor='checked', show='block', hd='<option value=HD>현대카드</option>'):
        page = self.browser.new_page()
        page.set_content(CARD_HTML.format(kor=kor, show=show, hd=hd))
        out = site_drive.select_hyundai_card(page, log=lambda *_: None, pace=0.1)
        return out, page

    def test_default_korean_card_selects_hyundai_with_change_event(self):
        out, page = self.run_select()
        self.assertTrue(out['verified'], out)
        self.assertEqual(out['select'], 'set')
        self.assertEqual(page.evaluate('window.changes'), 1)
        self.assertIn('현대카드', page.evaluate(
            "(() => { const s = document.getElementById('sel-korCardCompany'); return s.options[s.selectedIndex].text })()"))

    def test_clicks_korean_card_label_when_list_hidden(self):
        out, _ = self.run_select(kor='', show='none')
        self.assertTrue(out['verified'], out)
        self.assertTrue(out.get('koreanCardClicked'))

    def test_missing_hyundai_option_is_not_verified(self):
        out, _ = self.run_select(hd='')
        self.assertFalse(out['verified'])
        self.assertEqual(out['result'], 'option-missing')
