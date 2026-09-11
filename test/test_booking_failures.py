"""달력 경로 이탈·재조회 수명·조회 오류를 실제 브라우저 픽스처로 재현. 실사이트 요청 없음."""
import json
import sys
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
HOST = 'https://ke.test'
CAL = '/booking/calendar-fare-bonus'
DEP = '/booking/select-award-flight/departure'
API = '/api/ap/booking/avail/awardAvailability'
VALID = {'upsellBoundAvailList': []}


class BookingFailures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
        cls.js = (ROOT/'userscript/ke-award-macro.user.js').read_text(encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.ctx = self.browser.new_context()
        self.addCleanup(self.ctx.close)
        self.ctx.add_init_script(self.js)
        self.ctx.route('**/*', lambda r: r.fulfill(content_type='text/html', body='<html><body>시험</body></html>'))
        self.page = self.ctx.new_page()

    def land(self, path):
        self.page.goto(HOST+path)
        self.page.wait_for_function('window.KE_REC && window.KE_HUD && window.KE_PROBE')

    def play(self, steps):
        self.page.evaluate('''steps => {
            KE_REC.state.steps=steps; KE_REC.state.expectDate='08-22';
            KE_REC.state.cabin='프레스티지'; KE_REC.state.stepTimeoutMs=1500;
            KE_REC.state.openWaitMaxMs=2500; KE_REC.reset(); KE_REC.play();
        }''', steps)

    def stopped(self):
        self.page.wait_for_function('KE_REC.state.problem && !KE_REC.state.playing && !KE_REC.state.playAfterReload', timeout=10000)
        return self.page.evaluate('KE_REC.state.message')

    def test_calendar_leaving_route_does_not_click_stale_date(self):
        self.land(CAL)
        self.page.evaluate('''() => {
            document.body.insertAdjacentHTML('beforeend','<div id="dep-fare-22">22 08월 22일</div>');
            window.clicked=0;document.querySelector('#dep-fare-22').onclick=()=>clicked++;
            history.replaceState(null,'','/booking/search');
        }''')
        self.play([{'dynamicDate':True, 'url':CAL}])
        self.assertIn('달력 화면 이탈', self.stopped())
        self.assertEqual(self.page.evaluate('clicked'),0)
        self.assertEqual(self.page.evaluate('KE_REC.state.navigation.at(-1).path'),'/booking/search')
        self.page.reload()
        self.page.wait_for_function('window.KE_REC')
        self.assertIn('달력 화면 이탈', self.page.evaluate('KE_REC.state.message'))
        self.assertFalse(self.page.evaluate('KE_REC.state.playing'))

    def test_calendar_unopened_then_open_and_p_disappears(self):
        loads=[]
        def calendar(route):
            loads.append(1)
            enabled=len(loads)>1
            route.fulfill(content_type='text/html',body='''<div id="dep-fare-21">21 08월 21일 E</div>
                <div id="dep-fare-22" aria-disabled="'''+('false' if enabled else 'true')+'''">22 08월 22일 <span id="p">P</span></div>
                <script>window.clicked=0;document.querySelector('#dep-fare-22').onclick=()=>{clicked++;document.querySelector('#p').remove();};</script>''')
        self.ctx.route(HOST+CAL, calendar)
        self.land(CAL)
        self.play([{'dynamicDate':True,'url':CAL}])
        self.page.wait_for_function('window.clicked===1',timeout=10000)
        self.assertEqual(len(loads),2)
        self.assertEqual(self.page.locator('#p').count(),0)
        self.assertEqual(self.page.evaluate('KE_REC.state.idx'),1)

    def test_unopened_calendar_stops_and_terminal_message_survives_reload(self):
        self.ctx.route(HOST+CAL,lambda r:r.fulfill(content_type='text/html',body='<div id="dep-fare-21">21 08월 21일</div>'))
        self.land(CAL)
        self.play([{'dynamicDate':True,'url':CAL}])
        msg=self.stopped()
        self.assertIn('안 열렸습니다',msg)
        self.page.reload()
        self.page.wait_for_function('window.KE_REC')
        self.assertEqual(self.page.evaluate('KE_REC.state.message'),msg)
        self.assertFalse(self.page.evaluate('KE_REC.state.playAfterReload'))

    def test_http_and_business_errors_are_not_sold_out(self):
        for status,body,kind in [(504,{},'http-error'),(403,{},'http-error'),
                                (200,{'code':503},'application-error'),(200,{},'schema-error')]:
            with self.subTest(status=status,kind=kind):
                self.land(DEP)
                self.ctx.route(HOST+API,lambda r,s=status,b=body:r.fulfill(status=s,content_type='application/json',body=json.dumps(b)))
                self.page.evaluate('url => fetch(url).then(r=>r.text())',API)
                self.page.wait_for_function("KE_PROBE.availabilityState()?.state !== 'pending'")
                self.play([{'dynamicCabin':True,'url':DEP}])
                msg=self.stopped()
                self.assertIn(kind,msg)
                self.assertIn('좌석 상태 판정 불가',msg)
                self.assertNotIn('매진',msg)

    def test_pending_request_prevents_click_on_old_visible_seat(self):
        pending=[]
        self.ctx.route(HOST+API,lambda r:pending.append(r))
        self.land(DEP)
        self.page.evaluate('''url => {
            document.body.insertAdjacentHTML('beforeend','<div>대한항공 운항<button id="seat">프레스티지 62500 마일</button></div>');
            window.clicked=0;document.querySelector('#seat').onclick=()=>clicked++;
            fetch(url).catch(()=>{});
        }''',API)
        self.play([{'dynamicCabin':True,'url':DEP}])
        self.assertIn('응답 대기 시간 초과', self.stopped())
        self.assertEqual(self.page.evaluate('clicked'),0)

    def test_late_old_error_does_not_replace_new_response(self):
        pending=[]
        self.ctx.route(HOST+API,lambda r:pending.append(r))
        self.land(DEP)
        self.page.evaluate('url=>{fetch(url);fetch(url);}',API)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(pending),2)
        pending[1].fulfill(content_type='application/json',body=json.dumps(VALID))
        self.page.wait_for_function("KE_PROBE.availabilityState()?.state==='valid'")
        pending[0].fulfill(status=504,body='old error')
        self.page.wait_for_timeout(100)
        self.assertEqual(self.page.evaluate('KE_PROBE.availabilityState().state'),'valid')

    def test_xhr_network_failure_is_reported(self):
        self.ctx.route(HOST+API,lambda r:r.abort('failed'))
        self.land(DEP)
        self.page.evaluate("url=>{let x=new XMLHttpRequest();x.open('POST',url);x.send('{}');}",API)
        self.page.wait_for_function("KE_PROBE.availabilityState()?.state==='network-error'")
        self.play([{'dynamicCabin':True,'url':DEP}])
        self.assertIn('network-error',self.stopped())


if __name__=='__main__':
    unittest.main()
