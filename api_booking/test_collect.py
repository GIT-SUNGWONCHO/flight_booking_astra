"""수집기 비밀정보 비저장·연결·실패 관측 시험. 외부 요청 없음."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from collect import Collector
from contracts import ContractRecorder
from evidence import FlowEvidence


class Request:
    def __init__(self, path, data=None, response=None, status=200):
        self.url = 'https://www.koreanair.com' + path
        self.method = 'POST'
        self.post_data = json.dumps(data)
        self.timing = {'startTime': 1000, 'responseEnd': 100}
        self._response = SimpleNamespace(status=status, body=lambda: json.dumps(response).encode())

    def response(self):
        return self._response


class Tests(unittest.TestCase):
    def test_segment_status_requires_exact_path(self):
        c = ContractRecorder()
        p = c.project({'status':'private-status','boundList':[{'segmentList':[{'status':'HK'}]}],
                       'passenger':{'status':'HK'}})
        self.assertNotIn('observedValue',p['fields']['status'])
        self.assertNotIn('observedValue',p['fields']['passenger']['fields']['status'])
        self.assertEqual(p['fields']['boundList']['items'][0]['fields']['segmentList']['items'][0]['fields']['status']['observedValue'],'HK')

    def test_outside_allowlist_has_lifecycle_without_private_path(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d))
            a = Request('/api/pp/payment/private-order-id',{'pnr':'private-value'})
            b = Request('/api/unknown/private-member-id')
            c.started(a); c.started(b); c.finished(a); c.failed(b); c.save('ended')
            raw = (Path(d)/'contracts.json').read_text(encoding='utf-8')
            self.assertNotIn('private-',raw)
            outside = json.loads(raw)['coverage']['outsideAllowlist']
            self.assertEqual([e['state'] for e in outside],['finished','network-failed'])
            self.assertFalse(c.summary()['allTrackedRequestsRecorded'])

    def test_matching_and_mismatching_fare_order_payment(self):
        import copy
        f = FlowEvidence({'origin':'ICN','destination':'CDG','date':'2027-09-04','currency':'KRW'})
        fare = {'boundList':[{'departureDateTime':'202709041230','segmentList':[
            {'departureAirport':'ICN','arrivalAirport':'CDG','flightNumber':'fixture-flight',
             'cabinClass':'fixture-cabin','fareFamily':'fixture-family'}]}],
            'pnrFareInfo':{'amount':'100','totalAmount':'100','mileage':'35000','currency':'KRW'}}
        a = '/api/ap/booking/avail/fareInformation';b='/api/ap/booking/traveller/inputTravellers'
        c='/api/pp/payment/NaverPay'
        f.observe(a,'response',fare,1,200)
        order = copy.deepcopy(fare); order['pnr']='private-order'
        good = f.observe(b,'response',order,2,200)
        self.assertTrue(good['itineraryMatchesFare']);self.assertTrue(good['targetRouteDateMatch'])
        payment=f.observe(c,'request',{'reservationRecLoc':'private-order','amount':'100.0','currency':'KRW'},3)
        self.assertTrue(payment['sameOrderReference']);self.assertTrue(payment['paymentAmountMatchesOrderAmount'])
        payment=f.observe(c,'request',{'reservationRecLoc':'wrong','amount':'101','currency':'USD'},4)
        self.assertFalse(payment['sameOrderReference']);self.assertFalse(payment['currencyMatchesOrder'])
        self.assertFalse(payment['paymentAmountMatchesOrderAmount'])
        f.begin(b)
        self.assertIsNone(f.observe(c,'request',{'reservationRecLoc':'private-order'},4)['sameOrderReference'])
        order['boundList'][0]['segmentList'][0]['arrivalAirport']='FCO'
        bad=f.observe(b,'response',order,5,200)
        self.assertFalse(bad['targetRouteDateMatch']);self.assertFalse(bad['itineraryMatchesFare'])
        self.assertNotIn('private-order',json.dumps(good));self.assertIsNone(bad['seatHoldTime'])
        f.observe(a,'response',fare,6,200)
        self.assertIsNone(f.observe(c,'request',{'reservationRecLoc':'private-order'},7)['sameOrderReference'])

    def test_missing_and_error_fields_do_not_prove_matches(self):
        f=FlowEvidence()
        a='/api/ap/booking/avail/fareInformation';b='/api/ap/booking/traveller/inputTravellers'
        f.observe(a,'response',{},1,200)
        result=f.observe(b,'response',{'code':'private-error','pnr':'private-pnr'},2,200)
        self.assertTrue(result['errorFieldPresent']);self.assertIsNone(result['itineraryMatchesFare'])
        self.assertIsNone(f.order)
        self.assertNotIn('private-',json.dumps(result))

    def test_pending_and_failed_requests_cannot_report_complete(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d))
            a = Request('/api/ap/booking/avail/fareInformation')
            b = Request('/api/ap/booking/traveller/inputTravellers')
            c.started(a); c.started(b); c.finished(a); c.save('ended')
            result = json.loads((Path(d)/'contracts.json').read_text(encoding='utf-8'))
            self.assertEqual(result['captureSummary']['pendingRequestIds'], [2])
            self.assertFalse(result['captureSummary']['allTrackedRequestsRecorded'])
            c.failed(b)
            self.assertEqual(c.summary()['networkFailed'], 1)
            self.assertFalse(c.summary()['allTrackedRequestsRecorded'])

    def test_browser_timing_is_not_callback_time(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d), reference_at=1)
            a = Request('/api/ap/booking/traveller/inputTravellers', response={'pnr':'private-pnr'})
            a.timing = {'startTime':2000,'requestStart':2,'responseStart':5900,'responseEnd':6000}
            with patch('collect.time.time', return_value=99):
                c.started(a); c.finished(a)
            entry = c.ledger[1]
            self.assertEqual(entry['requestCallback']['callbackAt'],99)
            self.assertAlmostEqual(entry['secondsFromReference']['requestSendStartAt'],1.002)
            self.assertEqual(entry['browserTimes']['responseCompleteAt'],8)
            self.assertTrue(c.summary()['allTrackedRequestsRecorded'])
            self.assertFalse(c.summary()['seatHoldVerified'])

    def test_bad_body_missing_timing_and_duplicate_start(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d))
            a = Request('/api/ap/booking/traveller/inputTravellers')
            a.timing = {'startTime':-1,'responseEnd':-1}
            a._response.body = lambda: b'<html>private-auth-redirect</html>'
            c.started(a); c.started(a); c.finished(a); c.save('ended')
            self.assertEqual(c.summary()['started'],1)
            self.assertFalse(c.summary()['allTrackedRequestsRecorded'])
            self.assertNotIn('browserTimes',c.ledger[1])
            self.assertEqual(c.ledger[1]['responseTimeSource'],'python-callback-fallback')
            self.assertNotIn('private-auth-redirect',(Path(d)/'contracts.json').read_text(encoding='utf-8'))

    def test_only_recognized_enum_values_are_saved(self):
        c = ContractRecorder()
        p = c.project({'segmentStatus':'HK','statusCode':'private-value',
                       'resultCode':'Success','currency':'KRW','message':'private-message',
                       'soldout':False,'pnr':'private-pnr'})
        self.assertEqual(p['fields']['segmentStatus']['observedValue'],'HK')
        self.assertTrue(p['fields']['statusCode']['unrecognizedValue'])
        self.assertFalse(p['fields']['soldout']['observedValue'])
        self.assertNotIn('private-',json.dumps(p))

    def test_http_denial_is_not_a_successful_booking(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d))
            a = Request('/api/ap/booking/avail/fareInformation',status=403)
            c.started(a);c.finished(a)
            self.assertEqual(c.summary()['httpNon200'],1)
            self.assertFalse(c.summary()['fullBookingFlowVerified'])

    def test_transient_windows_read_lock_does_not_end_capture(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d));replace=os.replace;attempts=[]
            def locked_once(source,target):
                attempts.append(1)
                if len(attempts)==1:raise PermissionError('fixture lock')
                return replace(source,target)
            with patch('collect.os.replace',side_effect=locked_once):c.save()
            self.assertEqual(len(attempts),2)
            self.assertEqual(json.loads((Path(d)/'contracts.json').read_text(encoding='utf-8'))['state'],'observing')

    def test_private_values_removed_and_links_preserved(self):
        c = ContractRecorder()
        a = '/api/ap/booking/avail/fareInformation'
        b = '/api/ap/booking/traveller/inputTravellers'
        c.record(a, 'response', 1, {'cartId':'cart-secret','givenName':'private-name',
                 'message':'private-message','url':'https://x.test/?token=private-token'}, 1)
        c.record(b, 'request', 2, {'cartId':'cart-secret','passenger':{'surname':'private-surname'}}, 2)
        encoded = json.dumps(c.snapshot())
        for secret in ['cart-secret','private-name','private-message','private-token','private-surname']:
            self.assertNotIn(secret, encoded)
        self.assertEqual(len(c.links), 1)

    def test_allowlist_errors_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            c = Collector(Path(d))
            unknown = Request('/api/login', {'password':'private-password'})
            c.started(unknown)
            self.assertFalse(c.requests)
            request = Request('/api/ap/booking/avail/fareInformation', {'flightId':'flight-secret'},
                              {'cartId':'cart-secret'}, status=403)
            c.started(request);c.finished(request);c.failed(request);c.save('ended')
            data = (Path(d)/'contracts.json').read_text(encoding='utf-8')
            self.assertNotIn('secret', data);self.assertNotIn('private-password', data)
            result = json.loads(data)
            self.assertEqual(result['state'], 'ended')
            self.assertEqual(result['events'][0]['status'], 403)
            self.assertEqual(result['errors'][0]['error'], 'requestfailed')
            self.assertFalse(result['coverage']['unknownPaymentEndpointsCaptured'])


if __name__ == '__main__':
    unittest.main()
