"""실행기 상태 인계 분기: 사이트 미접속, 가짜 전송과 후반 처리 사용."""
from unittest import mock
import types
import unittest
import live_order
from test_live_order import FlowHarness, CAL_URL
from test_pipeline import fare_response, order_response


class RunnerTests(FlowHarness):
    def run_bridge(self, bridge_ok=True, payment_ok=True):
        connection=types.SimpleNamespace(result={'matched':bridge_ok,'sessionUnchanged':True,
            'displayHints':{'date':True,'mileage':True,'krw':True},'stage':'same-order-reference'},
            guard=object(),close=mock.Mock())
        with mock.patch.object(live_order.connected_bridge,'bind',return_value=types.SimpleNamespace(storage={})) as bind, \
             mock.patch.object(live_order.connected_bridge.state_bridge,'validate_prepared_storage'), \
             mock.patch.object(live_order.connected_bridge,'preflight',return_value=True), \
             mock.patch.object(live_order.connected_bridge,'connect',return_value=connection) as connect, \
             mock.patch.object(live_order.site_drive,'payment_pass',return_value={
                 'completed':payment_ok,'stage':'npay-checkout' if payment_ok else 'login-required'}) as payment, \
             mock.patch.object(live_order.recovery,'inspect_failure') as inspect:
            result=self.run_main('--state-bridge','--continue-payment','--inspect-failure',
                '--family','KEBONUSEY','--capture-date','09월 09일',initial_url=CAL_URL,
                replies={'fareInformation':fare_response(family='KEBONUSEY',mileage='35000'),
                         'inputTravellers':order_response(family='KEBONUSEY',mileage='35000')})
        return result,connection,bind,connect,payment,inspect
    def test_single_send_single_bridge_existing_watch_payment(self):
        (code,_,calls,intent),connection,bind,connect,payment,inspect=self.run_bridge()
        self.assertEqual((code,intent),(0,'ordered'))
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'),1)
        bind.assert_called_once();connect.assert_called_once();payment.assert_called_once()
        self.assertFalse(payment.call_args.kwargs['navigate'])
        self.assertIs(payment.call_args.kwargs['existing_watch'],connection.guard)
        connection.close.assert_called_once();inspect.assert_not_called()
    def test_bridge_failure_never_clicks_payment(self):
        (code,_,calls,intent),connection,_,_,payment,inspect=self.run_bridge(bridge_ok=False)
        self.assertEqual((code,intent),(2,'ordered'))
        payment.assert_not_called();inspect.assert_called_once();connection.close.assert_called_once()
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'),1)
    def test_payment_failure_retains_response_and_no_retry(self):
        (code,_,calls,_),_,_,_,_,inspect=self.run_bridge(payment_ok=False)
        self.assertEqual(code,2);inspect.assert_called_once()
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'),1)
    def test_other_capture_date_needs_matching_capture_iso_before_browser(self):
        # 2026-09-19: 프레스티지·예약 발사·다른 캡처 날짜는 허용. 단 캡처 날짜가 다르면
        # --capture-iso 가 라벨과 같은 날짜로 있어야 한다.
        for extra in ((),('--capture-iso','2027-09-08'),
                      ('--capture-date','09월 09일','--capture-iso','2027-09-10')):
            code,connect,calls,_=self.run_main('--state-bridge','--continue-payment','--inspect-failure',*extra)
            self.assertEqual((code,connect.call_count,calls),(2,0,[]))
    def test_unready_stored_context_prevents_all_fire_requests(self):
        with mock.patch.object(live_order.connected_bridge,'bind',
                               return_value=types.SimpleNamespace(storage={})), \
             mock.patch.object(live_order.connected_bridge.state_bridge,'validate_prepared_storage',
                               side_effect=ValueError('unready-context')):
            code,_,calls,_=self.run_main('--state-bridge','--continue-payment','--inspect-failure',
                '--family','KEBONUSEY','--capture-date','09월 09일',initial_url=CAL_URL)
        # 무장 점검이 캡처 직후 저장 상태를 먼저 걸러 재준비 코드로 끝낸다.
        self.assertEqual(code,live_order.EXIT_REPREPARE)
        self.assertFalse(any(c.startswith('send:') for c in calls))

    def test_scheduled_prestige_binds_before_fire_and_hands_icn_arrival_to_user(self):
        # 2026-09-19: 프레스티지·다른 캡처 날짜·--at 허용. 결속은 T-15초에 미리 잡고,
        # ICN 도착(현대카드)은 결제수단 직전 정지(user-payment-method)를 성공으로 끝낸다.
        # 2026-09-20: 현대카드 창 도착(hyundai-card-window)도 사용자에게 넘기는 성공이다.
        for pay in ({'completed':False,'stage':'user-payment-method','handedToUser':True},
                    {'completed':True,'stage':'hyundai-card-window','handedToUser':True}):
          with self.subTest(pay['stage']):
            from datetime import datetime
            from test_live_order import FakeClock
            clock=FakeClock(datetime(2099,1,1,8,59,0,tzinfo=live_order.KST))
            bound=[]
            connection=types.SimpleNamespace(result={'matched':True,'sessionUnchanged':True,
                'displayHints':{'date':True,'mileage':True,'krw':True},'stage':'same-order-reference'},
                guard=object(),close=mock.Mock())
            def bind(*_a,**kw):
                bound.append((clock.t,kw.get('search_date')))
                return types.SimpleNamespace(storage={})
            with mock.patch.object(live_order.connected_bridge,'bind',side_effect=bind), \
                 mock.patch.object(live_order.connected_bridge.state_bridge,'validate_prepared_storage'), \
                 mock.patch.object(live_order.connected_bridge,'preflight',return_value=True), \
                 mock.patch.object(live_order.connected_bridge,'connect',return_value=connection), \
                 mock.patch.object(live_order.site_drive,'payment_pass',return_value=dict(pay)) as payment, \
                 mock.patch.object(live_order.recovery,'inspect_failure') as inspect:
                code,_,calls,intent=self.run_main('--state-bridge','--continue-payment','--inspect-failure',
                    '--capture-iso','2027-09-07','--at','09:00:00',initial_url=CAL_URL,clock=clock,
                    send_hook=lambda name,body: None if name!='awardAvailability'
                        or '20270909' in body else {'ok':True,'status':200,'elapsedMs':1.0,
                        'body':__import__('json').dumps(__import__('test_pipeline').award_response(
                            date='20270907112000'))})
            self.assertEqual((code,intent),(0,'ordered'))
            self.assertEqual(len(bound),1)
            when,search=bound[0]
            self.assertEqual(search,'2027-09-07')
            self.assertGreaterEqual(when,datetime(2099,1,1,8,59,44,tzinfo=live_order.KST))
            self.assertLess(when,datetime(2099,1,1,9,0,0,tzinfo=live_order.KST))
            self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'),1)
            payment.assert_called_once();inspect.assert_not_called()

    def test_failed_final_preflight_never_sends_order(self):
        with mock.patch.object(live_order.connected_bridge,'bind',return_value=types.SimpleNamespace(storage={})), \
             mock.patch.object(live_order.connected_bridge.state_bridge,'validate_prepared_storage'), \
             mock.patch.object(live_order.connected_bridge,'preflight',side_effect=ValueError('session-changed')):
            code,_,calls,_=self.run_main('--state-bridge','--continue-payment','--inspect-failure',
                '--family','KEBONUSEY','--capture-date','09월 09일',initial_url=CAL_URL)
        self.assertEqual(code,2)
        self.assertTrue(any(c.startswith('send:fareInformation') for c in calls))
        self.assertFalse(any(c.startswith('send:inputTravellers') for c in calls))


class RetainedResumeTests(unittest.TestCase):
    def test_handoff_inspection_retains_original_response_and_disables_used_binding_resume(self):
        for used in (False,True):
            a,binding,pl=self.setup_context();binding.used=used;pl.quote=object()
            fare={};order={}
            with mock.patch.object(live_order.recovery,'inspect_failure',return_value='user-exit') as inspect:
                self.assertFalse(live_order.inspect_handoff(None,a,None,pl,binding,fare,order,'run'))
            self.assertIs(inspect.call_args.args[0],order)
            self.assertTrue(callable(inspect.call_args.kwargs['diagnose']))
            self.assertEqual(inspect.call_args.kwargs['resume'] is None,used)

    def test_resumed_handoff_failure_does_not_open_nested_inspection(self):
        a,binding,pl=self.setup_context();a.day='2026-09-14';pl.quote=object()
        order=types.SimpleNamespace(amounts_matched=False,payment_amounts_matched=True,
                                   amount_layout='observed-zero-to-total')
        with mock.patch.object(live_order.connected_bridge,'connect',side_effect=ValueError('session-changed')), \
             mock.patch.object(live_order.recovery,'inspect_failure') as inspect, \
             mock.patch.object(live_order,'record_intent'),mock.patch.object(live_order,'log'):
            self.assertEqual(live_order.bridged_payment(None,a,None,pl,binding,{}, {},order,'run',
                                                      inspect_on_failure=False),2)
        inspect.assert_not_called()

    def setup_context(self):
        return (types.SimpleNamespace(state_bridge=True),
                types.SimpleNamespace(used=False),
                types.SimpleNamespace(order_request=types.SimpleNamespace(created=100.),
                                      judge_order=mock.Mock()))
    def test_expired_or_used_context_does_not_send_or_handoff(self):
        for used,now in ((False,191.),(True,105.),(False,99.)):
            a,binding,pl=self.setup_context();binding.used=used
            with mock.patch.object(live_order.time,'monotonic',return_value=now), \
                 mock.patch.object(live_order,'send_counted') as send, \
                 mock.patch.object(live_order,'bridged_payment') as handoff, \
                 mock.patch.object(live_order,'log'):
                self.assertFalse(live_order.resume_retained_order(None,a,None,pl,binding,{}, {},'run'))
            send.assert_not_called();handoff.assert_not_called();pl.judge_order.assert_not_called()
    def test_same_objects_handoff_once_without_transport(self):
        a,binding,pl=self.setup_context();fare={};order={}
        pl.judge_order.return_value=types.SimpleNamespace(state='order-recorded',
            order=types.SimpleNamespace(payment_amounts_matched=True))
        with mock.patch.object(live_order.time,'monotonic',return_value=105.), \
             mock.patch.object(live_order,'send_counted') as send, \
             mock.patch.object(live_order,'bridged_payment',return_value=0) as handoff:
            self.assertTrue(live_order.resume_retained_order(None,a,None,pl,binding,fare,order,'run'))
        send.assert_not_called();handoff.assert_called_once()
        self.assertIs(handoff.call_args.args[5],fare)
        self.assertIs(handoff.call_args.args[6],order)

    def test_bridge_exception_preserves_amount_policy_evidence(self):
        a=types.SimpleNamespace(state_bridge=True,day='2026-09-14')
        order=types.SimpleNamespace(amounts_matched=False,payment_amounts_matched=True,
                                    amount_layout='observed-zero-to-total')
        pl=types.SimpleNamespace(order_request=object(),quote=object())
        with mock.patch.object(live_order.connected_bridge,'connect',side_effect=ValueError()), \
             mock.patch.object(live_order,'record_intent') as record, \
             mock.patch.object(live_order,'log'), \
             mock.patch.object(live_order.recovery,'inspect_failure'):
            self.assertEqual(live_order.bridged_payment(None,a,None,pl,None,{}, {},order,'run'),2)
        self.assertFalse(record.call_args.kwargs['amountsMatched'])
        self.assertTrue(record.call_args.kwargs['paymentAmountsMatched'])
        self.assertEqual(record.call_args.kwargs['amountLayout'],'observed-zero-to-total')

if __name__=='__main__':unittest.main()
