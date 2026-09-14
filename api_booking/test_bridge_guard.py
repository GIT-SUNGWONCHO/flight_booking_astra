"""가짜 결제 앱·합성 참조를 사용하는 격리 시험. 실제 사이트 앱 시험 아님."""
import time
import unittest
from playwright.sync_api import sync_playwright
import handoff
from bridge_guard import BridgeGuard

SITE='https://www.koreanair.com'
GATE=SITE+'/payment/gate/RT/NR'
REF='/api/et/ibeSupport/baggagePolicies?orderId='


class GuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND'])
    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.ctx=self.browser.new_context(service_workers='block');self.addCleanup(self.ctx.close)
        self.hits=[];self.script=''
        def serve(route):
            self.hits.append(route.request.url)
            if '/api/' in route.request.url:route.fulfill(status=200,body='{}',content_type='application/json')
            else:route.fulfill(status=200,content_type='text/html',body='<script>'+self.script+'</script>')
        self.ctx.route('**/*',serve)
        self.pg=self.ctx.new_page();self.pg.goto(SITE+'/booking/calendar-fare-bonus')
        self.ordered=time.monotonic();self.guard=BridgeGuard(self.ctx,page=self.pg)
        self.guard.start();self.addCleanup(self.guard.stop)
    def inspect(self):
        return self.guard.inspect_gate(reference='SYNTHETIC',ordered_at=self.ordered,expected_url=GATE,timeout_ms=1000)
    def load(self,script):
        self.script=script;self.pg.goto(GATE)
    def test_same_reference_and_late_order_remains_blocked(self):
        self.load("fetch('"+REF+"SYNTHETIC')")
        self.assertTrue(self.inspect()['matched'])
        self.pg.evaluate('(p)=>fetch(p,{method:"POST"}).catch(()=>null)',handoff.ORDER_PATH)
        self.assertEqual(self.inspect()['stage'],'new-order-blocked')
        self.assertFalse(any(handoff.ORDER_PATH in u for u in self.hits))
    def test_each_selection_path_is_blocked(self):
        self.load('')
        for path in handoff.SELECTION_PATHS:
            self.pg.evaluate('(p)=>fetch(p,{method:"POST"}).catch(()=>null)',path)
        self.assertEqual(self.inspect()['stage'],'selection-request')
        self.assertFalse(any(any(p in u for p in handoff.SELECTION_PATHS) for u in self.hits))
    def test_other_reference_is_refused(self):
        self.load("fetch('"+REF+"OTHER')")
        self.assertEqual(self.inspect()['stage'],'other-order')
    def test_wrong_page_is_refused(self):
        self.assertEqual(self.inspect()['stage'],'unexpected-page')
    def test_missing_reference_is_not_success(self):
        self.load('')
        self.assertEqual(self.inspect()['stage'],'reference-unobserved')
    def test_other_tab_reference_is_not_success(self):
        self.load('');other=self.ctx.new_page();other.goto(SITE+'/')
        other.evaluate('(p)=>fetch(p)',REF+'SYNTHETIC')
        self.assertFalse(self.inspect()['matched'])
    def test_page_close_is_not_success(self):
        self.pg.close();self.assertEqual(self.inspect()['stage'],'page-closed')
    def test_stopped_guard_cannot_report_match(self):
        self.load("fetch('"+REF+"SYNTHETIC')")
        self.assertTrue(self.inspect()['matched'])
        self.guard.stop()
        with self.assertRaisesRegex(ValueError,'guard-not-started'):self.inspect()


if __name__=='__main__':unittest.main()
