"""합성 응답·가짜 결제 앱의 통합 시험. DNS 실패+전 요청 로컬 처리."""
import json
from dataclasses import replace
import unittest
from unittest.mock import patch
from playwright.sync_api import sync_playwright
import connected_bridge as bridge
import handoff
import site_drive
from test_state_bridge import Fixture

APP='''<body><span>2027-09-09</span><span>62,500 마일</span><span>KRW</span>
<script>
 const s=JSON.parse(sessionStorage.getItem('inputTravellers'));
 fetch('/api/et/ibeSupport/baggagePolicies?orderId='+encodeURIComponent(s.model.pnr));
</script></body>'''


class ConnectedTests(Fixture,unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND'])
    @classmethod
    def tearDownClass(cls):cls.browser.close();cls.pw.stop()
    def setUp(self):
        self.setup_fixture();self.app=APP;self.hits=[]
        request_body=json.loads(self.request.body_json)
        request_body['travellerInfoList'][0]['travellerId']='T1'
        self.request=replace(self.request,body_json=json.dumps(request_body))
        self.ctx=self.browser.new_context(service_workers='block');self.addCleanup(self.ctx.close)
        self.ctx.add_cookies([{'name':'fixture-session','value':'S1','url':bridge.ORIGIN}])
        def serve(route):
            self.hits.append(route.request.url)
            route.fulfill(status=200,content_type='application/json' if '/api/' in route.request.url else 'text/html; charset=utf-8',
                body='{}' if '/api/' in route.request.url else self.app if route.request.url==site_drive.GATE else '<title>calendar</title>')
        self.ctx.route('**/*',serve)
        self.pg=self.ctx.new_page();self.pg.goto(site_drive.CALENDAR)
        self.pg.evaluate('(s)=>Object.entries(s).forEach(([k,v])=>sessionStorage.setItem(k,v))',
                         {**self.storage,'loggedInUserInfo':json.dumps({'model':{'fixtureUser':'U1'}})})
        self.binding=bridge.bind(self.pg,session=self.quote.session,subject=self.request.subject,now=100.)
    def connect(self):
        result=bridge.connect(self.binding,request=self.request,quote=self.quote,
            fare_response=self.fare,order_response=self.order,now=104.,timeout_ms=2000,
            passenger_fingerprint=bridge.pipeline.traveller_digest(self.request.body_json))
        self.addCleanup(result.close)
        return result

    def test_observed_cookie_rotation_does_not_change_identity(self):
        self.ctx.add_cookies([{'name':name,'value':'v1','url':bridge.ORIGIN}
                             for name in bridge.ROTATING_COOKIE_VALUES])
        before=bridge._session_stamp(self.pg)
        self.ctx.add_cookies([{'name':name,'value':'v2','url':bridge.ORIGIN}
                             for name in bridge.ROTATING_COOKIE_VALUES])
        self.assertEqual(before,bridge._session_stamp(self.pg))
        self.assertEqual({c['value'] for c in self.ctx.cookies() if c['name'] in ('bm_s','bm_sv')},{'v2'})

    def test_preflight_is_read_only_and_detects_changed_document(self):
        hits=list(self.hits)
        self.assertTrue(bridge.preflight(self.binding,target=self.quote.target,now=104.))
        self.assertEqual(hits,self.hits)
        self.assertFalse(self.binding.used)
        self.assertEqual(self.pg.evaluate('()=>sessionStorage.getItem("inputTravellers")'),self.storage['inputTravellers'])
        self.pg.reload()
        with self.assertRaisesRegex(ValueError,'document-changed'):
            bridge.preflight(self.binding,target=self.quote.target,now=104.)

    def test_preflight_rejects_expired_storage_and_identity(self):
        with self.assertRaisesRegex(ValueError,'changed-context'):
            bridge.preflight(self.binding,target=self.quote.target,now=221.)
        self.pg.evaluate('()=>sessionStorage.setItem("inputTravellers","{}")')
        with self.assertRaisesRegex(ValueError,'storage-changed'):
            bridge.preflight(self.binding,target=self.quote.target,now=104.)

    def test_retained_diagnosis_after_expiry_is_read_only(self):
        hits=list(self.hits)
        report=bridge.retained_report(self.binding,self.request,now=200.)
        self.assertFalse(report['withinResumeWindow'])
        self.assertTrue(report['documentMatches']);self.assertTrue(report['storageMatches'])
        self.assertTrue(report['sessionMatches']);self.assertFalse(self.binding.used)
        self.assertEqual(hits,self.hits)
        self.assertNotIn('FAKE',json.dumps(report))

    def test_other_cookie_change_and_rotating_cookie_removal_still_detected(self):
        self.ctx.add_cookies([{'name':'bm_s','value':'v1','url':bridge.ORIGIN}])
        before=bridge._session_stamp(self.pg)
        self.ctx.clear_cookies(name='bm_s')
        self.assertNotEqual(before,bridge._session_stamp(self.pg))
        before=bridge._session_stamp(self.pg)
        self.ctx.add_cookies([{'name':'fixture-session','value':'changed','url':bridge.ORIGIN}])
        self.assertNotEqual(before,bridge._session_stamp(self.pg))
    def test_models_navigation_reference_and_late_guard(self):
        result=self.connect()
        self.assertTrue(result.result['matched'])
        self.assertTrue(all(result.result['displayHints'].values()))
        self.assertFalse(result.result['paymentWindowReached'])
        self.assertEqual(self.pg.evaluate('()=>sessionStorage.getItem("resvStatus")'),self.storage['resvStatus'])
        self.pg.evaluate('(p)=>fetch(p,{method:"POST"}).catch(()=>null)',handoff.ORDER_PATH)
        self.assertFalse(any(handoff.ORDER_PATH in u for u in self.hits))
        self.assertEqual(result.guard.judge('FAKEPNR',104.).state,'new-order-blocked')
        with self.assertRaisesRegex(ValueError,'used'):self.connect()
    def test_cookie_rotation_on_navigation_uses_current_member_and_order(self):
        self.app=APP.replace('</script>',"document.cookie='ui-new-cookie=1;path=/';</script>")
        result=self.connect()
        self.assertTrue(result.result['matched'])
        self.assertFalse(result.result['cookieStampUnchanged'])
        self.assertTrue(result.result['sessionUnchanged'])
        self.pg.evaluate('()=>sessionStorage.setItem("loggedInUserInfo","{}")')
        self.assertFalse(result.guard.judge('FAKEPNR',104.).same_reference)

    def test_new_navigation_requires_observed_reference(self):
        self.app=APP.replace("fetch('/api/et/ibeSupport/baggagePolicies?orderId='+encodeURIComponent(s.model.pnr));",'')
        result=self.connect()
        self.assertFalse(result.result['matched'])
        self.assertEqual(result.result['stage'],'reference-unobserved')
    def test_display_renders_after_reference_response(self):
        self.app=APP.replace('<span>62,500 마일</span>','<span id="late"></span>').replace('</script>',
            "setTimeout(()=>document.getElementById('late').textContent='62,500 마일',500);</script>")
        result=self.connect()
        self.assertTrue(result.result['matched'])
        self.assertTrue(all(result.result['displayHints'].values()))

    def test_changed_model_after_good_network_reference_fails(self):
        result=self.connect()
        self.assertTrue(result.result['matched'])
        self.pg.evaluate('()=>{const x=JSON.parse(sessionStorage.inputTravellers);x.model.pnrFareInfo.totalAmount="1";sessionStorage.inputTravellers=JSON.stringify(x)}')
        self.assertFalse(result.guard.judge('FAKEPNR',104.).same_reference)
    def test_changed_cookie_refused_before_storage_write(self):
        self.ctx.add_cookies([{'name':'fixture-session','value':'S2','url':bridge.ORIGIN}])
        with self.assertRaisesRegex(ValueError,'session-changed'):self.connect()
        self.assertEqual(self.pg.url,site_drive.CALENDAR)
        self.assertEqual(self.pg.evaluate('()=>sessionStorage.getItem("inputTravellers")'),self.storage['inputTravellers'])
    def test_same_url_new_document_refused(self):
        self.pg.reload()
        result=self.connect()
        self.assertEqual(result.result['stage'],'document-changed')
        self.assertEqual(self.pg.url,site_drive.CALENDAR)
    def test_model_changed_since_binding_refused(self):
        self.pg.evaluate('()=>sessionStorage.setItem("inputTravellers","{}")')
        self.assertEqual(self.connect().result['stage'],'storage-changed')
    def test_different_reference_fails(self):
        self.app=APP.replace('s.model.pnr',"'OTHER'")
        self.assertEqual(self.connect().result['stage'],'other-order')
    def test_reorder_during_bootstrap_blocked(self):
        self.app=APP.replace('</script>',"fetch('"+handoff.ORDER_PATH+"',{method:'POST'}).catch(()=>{});</script>")
        self.assertEqual(self.connect().result['stage'],'new-order-blocked')
        self.assertFalse(any(handoff.ORDER_PATH in u for u in self.hits))
    def test_navigation_exception_retains_guard_and_no_raw_error(self):
        with patch.object(self.pg,'goto',side_effect=RuntimeError('SECRET')):
            result=self.connect()
        self.assertEqual(result.result['stage'],'bridge-error')
        self.assertNotIn('SECRET',repr(result.result))
        self.assertIsNotNone(result.guard.started)
    def test_invalid_order_does_not_write_or_navigate(self):
        bad=json.loads(self.order['body']);bad['pnrFareInfo']['totalAmount']='1';self.order['body']=json.dumps(bad)
        with self.assertRaises(ValueError):self.connect()
        self.assertEqual(self.pg.url,site_drive.CALENDAR)
    def test_response_passenger_must_match_prepared_request(self):
        bad=json.loads(self.order['body']);bad['travellerFareInfoList'][0]['travellerId']='OTHER'
        self.order['body']=json.dumps(bad)
        with self.assertRaisesRegex(ValueError,'unverified-order'):self.connect()
        self.assertEqual(self.pg.url,site_drive.CALENDAR)


if __name__=='__main__':unittest.main()
