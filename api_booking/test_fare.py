"""A3 합성 fixture/가짜 전송 시험. 실제 사이트 계약 증거가 아니다."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import unittest

import availability
from availability import Selection, Target
from fare import PATH, build_request, judge


class FareTests(unittest.TestCase):
    def setUp(self):
        self.target = Target('2027-09-09','ICN','CDG','KEBONUSPR','KE','901')
        self.selection = Selection('avail-new','session',self.target,'rec-new','flight-new',100.)
        self.template = {'currency':'KRW','recommendList':[{'recommendId':'old',
                           'flightId':'old','fixtureExtra':'preserve'}]}
        self.request = self.build()
        self.payload = {'pageTicket':'fixture-ticket','boundList':[{'segmentList':[{
            'departureAirport':'ICN','arrivalAirport':'CDG','departureDateTime':'20270909130000',
            'flightNumber':'901','operationCarrierCode':'KE','codeShare':False,
            'fareFamily':'KEBONUSPR','cabinClass':'C'}]}],
            'pnrFareInfo':{'currency':'KRW','amount':'100','totalAmount':'120','mileage':'62500'}}

    def build(self, **kw):
        args = dict(selection=self.selection,current_generation='avail-new',session='session',
            target=self.target,cabin='C',template=self.template,headers={'fixture':'fake'},now=101.)
        args.update(kw)
        return build_request(**args)

    def result(self, **kw):
        args = dict(request=self.request,status=200,payload=self.payload,
            generation=self.request.generation,current_availability_generation='avail-new',
            session='session',target=self.target,now=102.)
        args.update(kw)
        return judge(**args)

    def blocked(self, **kw):
        result = self.result(**kw)
        self.assertIsNone(result.quote)
        self.assertFalse(result.seat_hold_verified)

    def test_fake_transport_and_current_identifiers(self):
        calls=[]
        def fake(req):
            calls.append(req.path)
            return 200,deepcopy(self.payload)
        status, payload = fake(self.request)
        r = self.result(status=status,payload=payload)
        self.assertEqual(calls,[PATH])
        self.assertEqual(r.state,'validated')
        self.assertEqual(r.quote.mileage,62500)
        self.assertFalse(r.seat_hold_verified)
        self.assertTrue(self.request.offline_only)
        self.assertEqual(self.request.body['recommendList'][0]['recommendId'],'rec-new')
        self.assertEqual(self.request.body['recommendList'][0]['flightId'],'flight-new')
        self.assertEqual(self.template['recommendList'][0]['recommendId'],'old')
        self.assertNotIn('fixture-ticket',repr(r))

    def test_selection_mismatch_and_expiry_prevent_request(self):
        for kw in ({'current_generation':'old'},{'session':'other'}, {'now':106.},
                   {'now':99.},{'now':float('nan')},{'max_age':0},
                   {'target':replace(self.target,flight='903')},
                   {'selection':replace(self.selection,recommend_id='')}):
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                self.build(**kw)

    def test_request_does_not_extend_selection_expiry(self):
        late = self.build(now=104.)
        self.blocked(request=late,generation=late.generation,now=105.1)

    def test_new_availability_invalidates_inflight_fare(self):
        for kw in ({'generation':'old-fare'}, {'current_availability_generation':'newer'},
                   {'session':'other'}, {'target':replace(self.target,date='2027-09-10')}):
            self.blocked(**kw)

    def test_all_target_fields_and_date(self):
        for field,wrong in [('departureAirport','GMP'),('arrivalAirport','FCO'),
            ('flightNumber','903'),('operationCarrierCode','AF'),('codeShare',True),
            ('fareFamily','KEBONUSEY'),('cabinClass','Y'),
            ('departureDateTime','20270910130000'),('departureDateTime','20270909139999')]:
            data = deepcopy(self.payload)
            data['boundList'][0]['segmentList'][0][field] = wrong
            self.blocked(payload=data)

    def test_missing_and_multiple_segments(self):
        for segments in ([],[{},{}],[{}]):
            data=deepcopy(self.payload);data['boundList'][0]['segmentList']=segments
            self.blocked(payload=data)

    def test_amount_validation_and_currency(self):
        for field,values in [('amount',[None,True,'NaN','-1','130']),
                             ('totalAmount',[None,'Infinity','-1']),
                             ('mileage',[0,False,'-1','0.5','NaN']),('currency',['USD',None])]:
            for value in values:
                data=deepcopy(self.payload);data['pnrFareInfo'][field]=value
                self.blocked(payload=data)

    def test_http_json_business_errors_and_missing_ticket(self):
        for status in (401,403,429,500):self.blocked(status=status)
        for payload in ('<html>','{}','[]',{'code':'ERT.10032'}):self.blocked(payload=payload)
        for key,value in [('error',{'code':'failure'}),('success',False),
                          ('responseCode','unknown'),('pageTicket','')]:
            data=deepcopy(self.payload);data[key]=value
            self.blocked(payload=data)

    def test_template_shape(self):
        for template in ({},{'currency':'KRW','recommendList':[]},
                         {**self.template,'currency':'USD'}):
            with self.assertRaises(ValueError):self.build(template=template)

    def test_observed_template_without_currency_and_unknown_cabin(self):
        # 9/12 실측 운임 요청은 recommendList 만 있었다. cabinClass 실값은 기록되지 않았다.
        request = self.build(template={'recommendList':[{'recommendId':'old','flightId':'old'}]},
                             cabin=None)
        self.assertNotIn('currency', request.body)
        payload = deepcopy(self.payload)
        payload['boundList'][0]['segmentList'][0]['cabinClass'] = 'anything'
        self.assertEqual(self.result(request=request, generation=request.generation,
                                     payload=payload).state, 'validated')
        payload['boundList'][0]['segmentList'][0]['fareFamily'] = 'KEBONUSEY'
        self.assertEqual(self.result(request=request, generation=request.generation,
                                     payload=payload).state, 'target-mismatch')
        with self.assertRaises(ValueError):
            self.build(cabin='  ')

    def test_observed_business_error_shape_on_http_200(self):
        # 요청 39: 옛 식별자로 보낸 운임 요청이 HTTP 200 + code·status·message 로 돌아왔다(값은 합성).
        self.assertEqual(self.result(payload={'code': 'E', 'status': 'x', 'message': 'm'}).state,
                         'business-error')

    def test_availability_selection_feeds_fare_without_network(self):
        target = Target('2027-08-21','ICN','FCO','KEBONUSEY','KE','931')
        source = availability.build_request(target, {'currency':'KRW','travelers':[{'count':1}],
            'segmentList':[{'departureDate':'20270821','departureAirport':'ICN',
                            'arrivalAirport':'FCO'}]}, {}, 'session', 99.)
        data = json.loads((Path(__file__).resolve().parents[1] /
            'test/fixture/api/awardAvailability.json').read_text())
        data['upsellBoundAvailList'][0]['availFlightList'][0]['flightInfoList'][0].update(
            operationCarrierCode='KE',codeShare=False,departureDateTime='20270821132000')
        selection = availability.judge(source,200,data,source.generation,'session',100.).selection
        request = self.build(selection=selection,current_generation=source.generation,
                             target=target,cabin='Y')
        response = deepcopy(self.payload)
        response['boundList'][0]['segmentList'][0].update(arrivalAirport='FCO',
            departureDateTime='20270821132000',flightNumber='931',fareFamily='KEBONUSEY',cabinClass='Y')
        result = self.result(request=request,generation=request.generation,
            current_availability_generation=source.generation,target=target,payload=response)
        self.assertEqual(result.state,'validated')
        self.assertEqual(result.quote.availability_generation,source.generation)


if __name__ == '__main__':
    unittest.main()
