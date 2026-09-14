"""저장 모델 후보의 합성 계약과 격리 Chromium 재로딩 시험. 실사이트 요청 없음."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
import unittest
from playwright.sync_api import sync_playwright

import state_bridge as bridge
from test_pipeline import fare_response, order_response, ok
import test_travellers as traveller_fixtures


class Fixture:
    def setup_fixture(self):
        base = traveller_fixtures.TravellersTests()
        base.setUp()
        base.quote = replace(base.quote, amount=Decimal(0), total_amount=Decimal(325500),
                             page_ticket='FAKE-TICKET')
        base.evidence = replace(base.evidence, quote=base.quote)
        self.quote, self.request = base.quote, base.build()
        self.fare, self.order = ok(fare_response()), ok(order_response())
        empty = {'model':None,'errors':None,'isPending':False,'isFailure':False}
        self.storage = {k:json.dumps(empty) for k in bridge.KEYS[:2]}
        self.storage.update(resvStatus=json.dumps({'processType':'RT','flowType':'NR',
            'isLoginExpired':False,'isSessionExpired':False,'isPNRConfirmed':False,
            'resData':None,'agreeStatus':None}),
            keAirSearchCriteria=json.dumps({'bounds':[{'originLocationCode':'ICN',
                'destinationLocationCode':'CDG','departureDateTime':'2027-09-09 00:00:00'}]}))

    def build(self, **kw):
        args=dict(storage=self.storage,request=self.request,quote=self.quote,
                  fare_response=self.fare,order_response=self.order,now=104.)
        args.update(kw)
        return bridge.build_patch(**args)


class BridgeTests(Fixture, unittest.TestCase):
    def setUp(self): self.setup_fixture()

    def test_validated_payloads_only_and_consent_unchanged(self):
        before=deepcopy(self.storage)
        patch=self.build()
        self.assertEqual(set(patch.after),set(bridge.KEYS[:2]))
        self.assertEqual(json.loads(patch.after['inputTravellers'])['model'],json.loads(self.order['body']))
        self.assertEqual(self.storage,before)
        self.assertNotIn('FAKEPNR',repr(patch))

    def test_rejects_wrong_order_and_changed_amount(self):
        for change in ('route','amount','status','error'):
            body=json.loads(self.order['body'])
            if change=='route':body['boundList'][0]['segmentList'][0]['arrivalAirport']='FCO'
            if change=='amount':body['pnrFareInfo']['amount']='1'
            if change=='status':body['boundList'][0]['segmentList'][0]['status']='XX'
            if change=='error':body['errorCode']='SYNTHETIC'
            with self.subTest(change=change),self.assertRaises(ValueError):self.build(order_response=ok(body))

    def test_rejects_wrong_fare_ticket_and_stale_response(self):
        data=json.loads(self.fare['body']);data['pageTicket']='OTHER'
        with self.assertRaisesRegex(ValueError,'fare-context'):self.build(fare_response=ok(data))
        for now in (102.,134.,float('nan')):
            with self.subTest(now=now),self.assertRaises(ValueError):self.build(now=now)

    def test_rejects_search_for_different_capture_date(self):
        s=dict(self.storage);data=json.loads(s['keAirSearchCriteria'])
        data['bounds'][0]['departureDateTime']='2027-09-07 00:00:00'
        s['keAirSearchCriteria']=json.dumps(data)
        with self.assertRaisesRegex(ValueError,'search-target'):self.build(storage=s)

    def test_observed_seoul_city_search_still_requires_icn_order(self):
        s=dict(self.storage);data=json.loads(s['keAirSearchCriteria'])
        data['bounds'][0].update(originLocationCode='sel',originLocationAirportType='CTY',
                                 originLocationCountryCode='KR')
        s['keAirSearchCriteria']=json.dumps(data)
        self.build(storage=s)
        bad=json.loads(self.order['body']);bad['boundList'][0]['segmentList'][0]['departureAirport']='GMP'
        with self.assertRaisesRegex(ValueError,'unverified-order'):self.build(storage=s,order_response=ok(bad))
        data['bounds'][0]['originLocationAirportType']='APO'
        s['keAirSearchCriteria']=json.dumps(data)
        with self.assertRaisesRegex(ValueError,'search-target'):self.build(storage=s)

    def test_utc_serialized_kst_calendar_day(self):
        s=dict(self.storage); data=json.loads(s['keAirSearchCriteria'])
        data['bounds'][0]['departureDateTime']='2027-09-08T15:00:00.000Z'
        s['keAirSearchCriteria']=json.dumps(data)
        self.build(storage=s)
        data['bounds'][0]['departureDateTime']='2027-09-07T15:00:00.000Z'
        s['keAirSearchCriteria']=json.dumps(data)
        with self.assertRaisesRegex(ValueError,'search-target'):self.build(storage=s)

    def test_refuses_to_replace_existing_order_or_agreements(self):
        for key,field,value in [('inputTravellers','model',{'pnr':'OLD'}),
                                ('resvStatus','agreeStatus',{'checked':True}),
                                ('resvStatus','isSessionExpired',True)]:
            s=dict(self.storage);data=json.loads(s[key]);data[field]=value;s[key]=json.dumps(data)
            with self.subTest(field=field),self.assertRaises(ValueError):self.build(storage=s)


class BrowserBridgeTests(Fixture, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw=sync_playwright().start()
        cls.browser=cls.pw.chromium.launch(headless=True,args=['--host-resolver-rules=MAP * ~NOTFOUND'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.pw.stop()

    def setUp(self):
        self.setup_fixture()
        self.ctx=self.browser.new_context(service_workers='block')
        self.addCleanup(self.ctx.close)
        self.requests=[]
        def serve(route):
            self.requests.append(route.request.url)
            route.fulfill(status=200,content_type='text/html',body='<title>fixture</title>')
        self.ctx.route('**/*',serve)
        self.page=self.ctx.new_page();self.page.goto('https://bridge.invalid/')
        self.page.evaluate('(s)=>Object.entries(s).forEach(([k,v])=>sessionStorage.setItem(k,v))',self.storage)

    def test_storage_survives_reload_without_fixture_api_requests(self):
        patch=self.build();self.assertEqual(bridge.fixture_apply(self.page,patch)['state'],'models-written')
        self.page.reload()
        restored=self.page.evaluate('()=>JSON.parse(sessionStorage.getItem("inputTravellers"))')
        self.assertEqual(restored['model']['pnr'],'FAKEPNR')
        self.assertEqual(self.page.evaluate('()=>sessionStorage.getItem("resvStatus")'),self.storage['resvStatus'])
        self.assertFalse(any('/api/' in u for u in self.requests))

    def test_changed_context_refused_without_writes(self):
        patch=self.build()
        self.page.evaluate('()=>sessionStorage.setItem("resvStatus","changed")')
        self.assertEqual(bridge.fixture_apply(self.page,patch)['state'],'context-changed')
        self.assertEqual(self.page.evaluate('()=>sessionStorage.getItem("inputTravellers")'),self.storage['inputTravellers'])

    def test_partial_write_failure_restores_both_original_values(self):
        self.page.evaluate('''()=>{
          const original=Storage.prototype.setItem;let failed=false;
          Storage.prototype.setItem=function(k,v){
            if(k==='inputTravellers'&&!failed){failed=true;throw new Error('fixture');}
            return original.call(this,k,v);
          };
        }''')
        self.assertEqual(bridge.fixture_apply(self.page,self.build())['state'],'write-failed-restored')
        restored=self.page.evaluate('(keys)=>Object.fromEntries(keys.map(k=>[k,sessionStorage.getItem(k)]))',list(bridge.KEYS))
        self.assertEqual(restored,self.storage)

    def test_real_origin_is_refused_even_in_network_isolated_fixture(self):
        self.page.goto('https://www.koreanair.com/')
        self.assertEqual(bridge.fixture_apply(self.page,self.build())['state'],'fixture-only')
        self.assertIsNone(self.page.evaluate('()=>sessionStorage.getItem("inputTravellers")'))


if __name__=='__main__':unittest.main()
