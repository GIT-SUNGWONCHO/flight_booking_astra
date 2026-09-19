"""live_order 실행기의 준비·발사 흐름 시험. 브라우저·전송 없음.

판정 픽스처는 test_pipeline 의 것을 쓴다(필드 이름은 수집 20260912-232135-f66cf6b8 구조,
값은 합성).
"""
from datetime import datetime, timedelta
import json
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
import live_order  # noqa: E402
import transport  # noqa: E402
from test_pipeline import (CAP_AWARD, CAP_FARE, CAP_ORDER, award_response,  # noqa: E402
                           fare_response, order_response)

SITE = 'https://www.koreanair.com'
GATE_URL = SITE + '/payment/gate/RT/NR'
CAL_URL = SITE + '/booking/calendar-fare-bonus'


def _caps(origin=SITE, age=0.0):
    at = time.time() - age
    def cap(path, body):
        return transport.Capture(path, origin, 'POST', at, origin + path, {'h': 'v'}, body)
    return {live_order.AVAIL: cap(live_order.AVAIL, CAP_AWARD),
            live_order.FARE: cap(live_order.FARE, CAP_FARE),
            live_order.ORDER: cap(live_order.ORDER, CAP_ORDER)}


DEFAULT_CLOCK = {'ok': True, 'offset': 0.0, 'uncertainty': 0.01, 'source': 'test'}
FAILED_CLOCK = {'ok': False, 'offset': 0.0, 'uncertainty': None, 'source': 'unmeasured-local-clock'}

FULL_STEPS = {'calendar': True, 'date': True, 'search': True, 'fare': True, 'next': True,
              'passenger': True, 'contact': True,
              'orderRequests': {'seen': 1, 'blocked': 1, 'unblocked': 0},
              'siteRequests': {'awardAvailability': 1, 'fareInformation': 1, 'inputTravellers': 1}}
BACK_OK = {'cells': 30, 'orderRequests': {'seen': 0, 'blocked': 0, 'unblocked': 0},
           'siteRequests': {}}


class FakeClock:
    """live_order 의 datetime.now(KST)·time.sleep 을 대신한다."""

    def __init__(self, start, on_sleep=None, oversleep=0.0):
        self.t = start
        self.on_sleep = on_sleep
        self.oversleep = oversleep

    def now(self, tz=None):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds + self.oversleep)
        if self.on_sleep:
            self.on_sleep(self)


class FlowHarness(unittest.TestCase):
    """가짜 Playwright·가짜 전송으로 main 을 돌린다. 사이트 접속 없음. 시험 메서드는 없다."""

    def run_main(self, *extra, intent=None, snap=None, steps=None, back=None,
                 on_calendar=True, capture_error=None, send_override=None, tmp=None,
                 replies=None, mileage='100000', reuse=True, clock=None, initial_url=GATE_URL,
                 clock_states=None, send_hook=None):
        if tmp is None:
            tmp = Path(tempfile.mkdtemp())
            self.addCleanup(shutil.rmtree, tmp, True)
        self.tmp = tmp
        # 실행일 2099-01-01 KST 08:00 으로 고정한다(D4: 실행일은 오늘이어야 한다).
        clock = clock or FakeClock(datetime(2099, 1, 1, 8, 0, 0, tzinfo=live_order.KST))
        self.clock = clock
        day = '2099-01-01'
        if intent is not None:
            (tmp / f'order-intent-{day}.json').write_text(json.dumps(intent), encoding='utf-8')
        calls = []
        page = types.SimpleNamespace(url=initial_url, evaluate=mock.Mock(return_value={}))
        self.page = page
        ctx = types.SimpleNamespace(pages=[page])
        connect = mock.Mock(return_value=types.SimpleNamespace(contexts=[ctx]))
        pw = types.SimpleNamespace(chromium=types.SimpleNamespace(connect_over_cdp=connect))
        manager = mock.MagicMock()
        manager.__enter__.return_value = pw
        fake_api = types.ModuleType('playwright.sync_api')
        fake_api.sync_playwright = lambda: manager
        self.since_seen = []

        def begin(_page):
            calls.append('generation')
            return 1234.5

        def snapshot(_page, since=None):
            calls.append('snapshot')
            self.since_seen.append(since)
            return _caps() if snap is None else snap

        def capture_pass(_page, label, **kw):
            calls.append('capture')
            if capture_error:
                raise capture_error
            return dict(FULL_STEPS if steps is None else steps)

        def back_to_calendar(_page, **kw):
            calls.append('calendar')
            _page.url = CAL_URL
            return dict(BACK_OK if back is None else back)

        def send(_page, cap, body=None):
            name = cap.path.rsplit('/', 1)[-1]
            calls.append('send:' + name + '@' + _page.url.rsplit('/', 1)[-1])
            if send_hook:
                hooked = send_hook(name, body)
                if hooked is not None:
                    return hooked
            if send_override and name in send_override:
                return send_override[name]
            reply = {'awardAvailability': award_response(), 'fareInformation': fare_response(),
                     'inputTravellers': order_response(), **(replies or {})}[name]
            return {'ok': True, 'status': 200, 'elapsedMs': 1.0,
                    'body': reply if isinstance(reply, str) else json.dumps(reply)}

        self.logs = []
        # NTP 는 부르지 않는다. 기본은 오프셋0·불확실성10ms 측정 성공.
        states = list(clock_states or [])
        self.clock_calls = []

        def fake_measure():
            self.clock_calls.append(clock.now())
            state = states.pop(0) if states else DEFAULT_CLOCK
            return dict(state, measuredAt=0)
        argv = ['live_order.py', '--date', '2027-09-09', '--capture-date', '09월 07일',
                '--day', day, *(['--own-mileage', mileage] if mileage else []),
                *(['--reuse-member-check'] if reuse else []), *extra]
        with mock.patch.dict(sys.modules, {'playwright.sync_api': fake_api}), \
                mock.patch.object(sys, 'argv', argv), \
                mock.patch.object(live_order, 'STATE', tmp), \
                mock.patch.object(live_order, 'log', self.logs.append), \
                mock.patch.object(live_order.transport, 'arm'), \
                mock.patch.object(live_order.transport, 'install'), \
                mock.patch.object(live_order.transport, 'begin_generation', begin), \
                mock.patch.object(live_order.transport, 'snapshot', snapshot), \
                mock.patch.object(live_order.transport, 'send_request', send), \
                mock.patch.object(live_order.site_drive, 'capture_pass', capture_pass), \
                mock.patch.object(live_order.site_drive, 'on_calendar', return_value=on_calendar), \
                mock.patch.object(live_order.site_drive, 'return_to_calendar', back_to_calendar), \
                mock.patch.object(live_order, 'datetime', clock), \
                mock.patch.object(live_order, 'measure_clock', fake_measure), \
                mock.patch.object(live_order, 'alert'), \
                mock.patch.object(live_order.time, 'sleep', clock.sleep):
            code = live_order.main()
        return code, connect, calls, self.intent_state(day)

    def intent_state(self, day='2099-01-01'):
        path = self.tmp / f'order-intent-{day}.json'
        return json.loads(path.read_text(encoding='utf-8'))['state'] if path.exists() else None

    def sends(self, calls):
        return [c for c in calls if c.startswith('send:')]

    def summary(self):
        return next(x for x in self.logs if x.startswith('구간별 호출 수'))



class PrepAndFireFlowTests(FlowHarness):
    """D2 준비·발사 흐름. 확인하는 것은 호출 순서와 거부 조건이다. 실사이트에서 주문 요청이
    막히는지, 달력 문서에서 보낸 요청을 서버가 받는지는 확인하지 않는다.
    """

    def test_unresolved_order_blocks_even_a_dry_run(self):
        # D1 에서 확인한 --dry 의 미해결 주문 검사 우회를 막았다.
        code, connect, calls, _ = self.run_main('--dry', intent={'state': 'unknown'})
        self.assertEqual((code, connect.call_count, calls), (2, 0, []))

    def test_dry_run_prepares_this_generation_returns_to_calendar_and_sends_from_memory(self):
        code, _, calls, intent = self.run_main('--dry')
        self.assertEqual(code, 0)
        self.assertEqual(calls, ['generation', 'capture', 'snapshot', 'calendar',
                                 'send:awardAvailability@calendar-fare-bonus',
                                 'send:fareInformation@calendar-fare-bonus'])
        self.assertEqual(self.since_seen, [1234.5])
        self.assertEqual(intent, live_order.PREP_CLEAN)
        self.assertIn("'inputTravellers-blocked': 1", self.summary())
        self.assertIn("'awardAvailability-fetched': 1", self.summary())
        self.assertNotIn('inputTravellers-attempt', self.summary())

    def test_clean_prep_marker_does_not_block_the_next_run(self):
        self.run_main('--dry')
        code, connect, _, _ = self.run_main('--dry', tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (0, 1))

    def test_order_is_sent_once_from_the_calendar_document(self):
        code, _, calls, intent = self.run_main()
        self.assertEqual(code, 0)
        self.assertEqual(self.sends(calls),
                         ['send:awardAvailability@calendar-fare-bonus',
                          'send:fareInformation@calendar-fare-bonus',
                          'send:inputTravellers@calendar-fare-bonus'])
        self.assertEqual(intent, 'ordered')
        self.assertFalse(any('FAKEPNR' in x for x in self.logs))

    def test_capture_date_is_required(self):
        code, _, calls, _ = self.run_main('--dry', '--capture-date', '')
        self.assertEqual(code, 2)
        self.assertEqual(calls, [])

    def test_unblocked_prep_order_stops_before_any_send(self):
        steps = dict(FULL_STEPS, orderRequests={'seen': 1, 'blocked': 0, 'unblocked': 1})
        code, _, calls, intent = self.run_main('--dry', steps=steps)
        self.assertEqual(code, 2)
        self.assertEqual(self.sends(calls), [])
        self.assertEqual(intent, 'prep-order-possible')

    def test_unblocked_order_before_a_prep_error_is_still_recorded(self):
        # 검토 165a57e4 P1: 미차단 요청 뒤 예외가 나도 주문 가능 기록이 남아야 한다.
        steps = {'date': True, 'error': 'TimeoutError',
                 'orderRequests': {'seen': 1, 'blocked': 0, 'unblocked': 1}, 'siteRequests': {}}
        code, _, calls, intent = self.run_main('--dry', steps=steps)
        self.assertEqual((code, intent), (2, 'prep-order-possible'))
        code, connect, _, _ = self.run_main('--dry', tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_prep_crash_leaves_a_blocking_marker(self):
        with self.assertRaises(RuntimeError):
            self.run_main('--dry', capture_error=RuntimeError('crash'))
        self.assertEqual(self.intent_state(), live_order.PREPARING)
        code, connect, _, _ = self.run_main('--dry', tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_incomplete_prep_is_not_used(self):
        steps = dict(FULL_STEPS, contact=False)
        code, _, calls, _ = self.run_main('--dry', steps=steps)
        self.assertEqual(code, 2)
        self.assertNotIn('snapshot', calls)
        self.assertEqual(self.sends(calls), [])

    def test_missing_path_in_this_generation_is_refused(self):
        partial = {live_order.AVAIL: _caps()[live_order.AVAIL]}
        code, _, calls, _ = self.run_main('--dry', snap=partial)
        self.assertEqual(code, 2)
        self.assertEqual(self.sends(calls), [])

    def test_unblocked_order_while_returning_to_calendar_stops(self):
        back = dict(BACK_OK, orderRequests={'seen': 1, 'blocked': 0, 'unblocked': 1})
        code, _, calls, intent = self.run_main('--dry', back=back)
        self.assertEqual((code, intent), (2, 'prep-order-possible'))
        self.assertEqual(self.sends(calls), [])

    def test_calendar_not_drawn_stops(self):
        code, _, calls, _ = self.run_main('--dry', back=dict(BACK_OK, cells=0))
        self.assertEqual(code, 2)
        self.assertEqual(self.sends(calls), [])

    def test_leaving_the_calendar_before_fire_stops(self):
        # 검토 165a57e4 P2: 달력 복귀 뒤 셀이 사라지면 발사하지 않는다.
        # 2026-09-19: 주문 전 무효는 재준비 코드(3)로 끝내 체인이 다시 준비하게 한다.
        code, _, calls, _ = self.run_main('--dry', on_calendar=False)
        self.assertEqual(code, live_order.EXIT_REPREPARE)
        self.assertEqual(self.sends(calls), [])

    def test_leaving_the_calendar_during_the_wait_asks_for_reprepare(self):
        # 9/19 01:40 실측: 약 70분 방치된 9232 가 로그아웃되고 홈으로 돌아갔다.
        def drift(clock):
            if clock.t >= datetime(2099, 1, 1, 8, 50, tzinfo=live_order.KST):
                self.page.url = 'https://www.koreanair.com/'

        clock = FakeClock(datetime(2099, 1, 1, 8, 40, 0, tzinfo=live_order.KST), on_sleep=drift)
        code, _, calls, intent = self.run_main('--at', '09:00:00', clock=clock)
        self.assertEqual(code, live_order.EXIT_REPREPARE)
        self.assertEqual(self.sends(calls), [])
        self.assertNotEqual(intent, 'sending')

    def test_capture_from_another_origin_is_never_sent(self):
        code, _, calls, _ = self.run_main('--dry', snap=_caps(origin='https://other.example'))
        self.assertEqual(code, 2)
        self.assertEqual(self.sends(calls), [])

    def test_unsent_request_is_not_counted_as_fetched(self):
        # 검토 165a57e4 P2: 오리진 불일치로 보내지 않은 요청을 호출로 세지 않는다.
        code, _, _, _ = self.run_main('--dry', send_override={
            'awardAvailability': {'ok': False, 'error': 'origin-changed'}})
        self.assertEqual(code, 2)
        self.assertIn("'awardAvailability-not-sent': 1", self.summary())
        self.assertNotIn('awardAvailability-fetched', self.summary())


class FireJudgementTests(FlowHarness):
    """D3 완료 조건: 잘못된 목표·매진·미개방·업무 오류·잔액 부족·세션 만료에서 주문 0회."""

    def assert_no_order(self, *extra, **kw):
        code, _, calls, intent = self.run_main(*extra, **kw)
        self.assertEqual(code, 2)
        self.assertNotIn('send:inputTravellers@calendar-fare-bonus', calls)
        self.assertNotEqual(intent, 'sending')
        return calls

    def test_wrong_target_dimensions(self):
        for extra in (('--date', '2027-09-10'), ('--flight', '905'), ('--destination', 'FCO'),
                      ('--origin', 'GMP')):
            with self.subTest(extra):
                calls = self.assert_no_order(*extra)
                self.assertNotIn('send:fareInformation@calendar-fare-bonus', calls)

    def test_sold_out_not_open_and_business_errors(self):
        for replies in ({'awardAvailability': award_response(soldout=True)},
                        {'awardAvailability': {'code': 'ERT.10032'}},
                        {'awardAvailability': {'errorCode': 'E'}},
                        {'fareInformation': {'code': 'E', 'status': 'x', 'message': 'm'}},
                        {'fareInformation': fare_response(family='KEBONUSEY')}):
            with self.subTest(list(replies)):
                self.assert_no_order(replies=replies)

    def test_insufficient_or_unconfirmed_balance(self):
        self.assert_no_order(mileage='62499')
        self.assert_no_order(mileage=None)
        code, _, calls, _ = self.run_main('--dry', mileage=None)
        self.assertEqual(code, 2)
        self.assertIn('필수 검증·주문 준비 판정=mileage-unverified', ' '.join(self.logs))

    def test_session_expired_or_login_page(self):
        for result in ({'ok': True, 'status': 401, 'body': ''},
                       {'ok': True, 'status': 200, 'body': '<html>login</html>'}):
            with self.subTest(result['status']):
                self.assert_no_order(send_override={'awardAvailability': result})
                self.assert_no_order(send_override={'fareInformation': result})

    def test_order_response_for_another_target_is_not_handed_off(self):
        bad = order_response(family='KEBONUSEY')
        code, _, calls, intent = self.run_main('--gate-only', replies={'inputTravellers': bad})
        self.assertEqual((code, intent), (2, 'unknown'))
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)

    def test_failed_order_inspection_keeps_unknown_and_does_not_resend(self):
        bad = order_response()
        bad['pnrFareInfo']['amount'] = '123'
        with mock.patch.object(live_order.recovery, 'inspect_failure') as inspect:
            code, _, calls, intent = self.run_main('--inspect-failure', '--gate-only',
                replies={'inputTravellers': bad})
        self.assertEqual((code, intent), (2, 'unknown'))
        inspect.assert_called_once()
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)
        self.assertTrue((self.tmp / 'order-permit.json').exists())
        code, connect, _, _ = self.run_main(tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_real_inspector_eof_and_interrupt_keep_single_send(self):
        bad = order_response()
        bad['pnrFareInfo']['amount'] = '123'
        for error in (EOFError, KeyboardInterrupt):
            with self.subTest(error=error), mock.patch('builtins.input', side_effect=error):
                code, _, calls, intent = self.run_main('--inspect-failure',
                    replies={'inputTravellers':bad})
            self.assertEqual((code, intent), (2, 'unknown'))
            self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)
            self.assertTrue((self.tmp / 'order-permit.json').exists())

    def test_judge_exception_keeps_response_without_exception_text(self):
        with mock.patch.object(live_order.pipeline.Pipeline, 'judge_order',
                               side_effect=RuntimeError('SECRET-PAYLOAD')), \
                mock.patch.object(live_order.recovery, 'inspect_failure') as inspect:
            code, _, calls, intent = self.run_main('--inspect-failure')
        self.assertEqual((code, intent), (2, 'unknown'))
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)
        inspect.assert_called_once()
        self.assertIn('body', inspect.call_args.args[0])
        self.assertNotIn('SECRET', '\n'.join(self.logs))
        record = json.loads((self.tmp / 'order-intent-2099-01-01.json').read_text())
        self.assertEqual(record['why'], 'order-judge-exception')

    def test_member_check_reuse_must_be_explicit(self):
        self.assert_no_order(reuse=False)
        self.assertIn('판정=member-evidence-other-itinerary', ' '.join(self.logs))

    def test_order_without_hk_status_is_not_handed_off(self):
        data = order_response()
        del data['boundList'][0]['segmentList'][0]['status']
        code, _, calls, intent = self.run_main('--gate-only', replies={'inputTravellers': data})
        self.assertEqual((code, intent), (2, 'unknown'))

    def test_scheduled_fire_keeps_the_booking_target(self):
        # 검토 2331c183 P1: --at 대기 뒤에도 예매 목표가 발사 시각으로 덮이지 않는다.
        clock = FakeClock(datetime(2099, 1, 1, 8, 59, 58, tzinfo=live_order.KST))
        code, _, calls, intent = self.run_main('--at', '09:00:00', clock=clock)
        self.assertEqual(code, 0)
        self.assertIn('send:inputTravellers@calendar-fare-bonus', calls)
        self.assertGreaterEqual(clock.t, datetime(2099, 1, 1, 9, 0, 0, tzinfo=live_order.KST))

    def test_fare_mileage_from_the_new_response_is_used(self):
        self.assert_no_order(mileage='70000', replies={'fareInformation': fare_response(mileage='80000')})


class SendSafetyTests(FlowHarness):
    """D4: 배타 전송권·실행일/발사 시각 고정·늦은 무장 차단·재시작 차단."""

    def permit_file(self):
        return self.tmp / 'order-permit.json'

    def test_order_takes_the_permit_and_a_restart_is_refused(self):
        code, _, calls, _ = self.run_main()
        self.assertEqual(code, 0)
        self.assertTrue(self.permit_file().exists())
        # 응답을 받고 의도 기록이 풀렸다고 가정해도 전송권이 남아 같은 날 새 주문을 막는다.
        (self.tmp / 'order-intent-2099-01-01.json').write_text(json.dumps({'state': 'resolved'}),
                                                              encoding='utf-8')
        code, connect, calls, _ = self.run_main(tmp=self.tmp)
        self.assertEqual((code, connect.call_count, calls), (2, 0, []))

    def test_restart_after_midnight_is_still_refused(self):
        # 검토 9aaff071 P1: 자정 직전 주문 뒤 다음 날 기본 실행일로 재시작해도 막는다.
        self.run_main()
        (self.tmp / 'order-intent-2099-01-01.json').write_text(json.dumps({'state': 'resolved'}),
                                                              encoding='utf-8')
        next_day = FakeClock(datetime(2099, 1, 2, 8, 0, 0, tzinfo=live_order.KST))
        code, connect, _, _ = self.run_main('--day', '2099-01-02', tmp=self.tmp, clock=next_day)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_unresolved_intent_of_a_previous_day_blocks(self):
        (self.tmp_dir() / 'order-intent-2098-12-31.json').write_text(
            json.dumps({'state': 'unknown'}), encoding='utf-8')
        code, connect, _, _ = self.run_main(tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (2, 0))

    def tmp_dir(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        return self.tmp

    def test_dry_run_never_takes_the_permit(self):
        code, _, _, _ = self.run_main('--dry')
        self.assertEqual(code, 0)
        self.assertFalse(self.permit_file().exists())

    def test_permit_taken_by_another_process_before_send(self):
        def other_process_wins():
            self.permit_file().write_text('{"runId": "other"}', encoding='utf-8')
            return {'ok': True, 'status': 200, 'body': json.dumps(fare_response()), 'elapsedMs': 1.0}

        class Racing(dict):
            def __contains__(self, key):
                return key == 'fareInformation'

            def __getitem__(self, key):
                return other_process_wins()
        code, _, calls, intent = self.run_main(send_override=Racing(hooked=True))
        self.assertEqual(code, 2)
        self.assertNotIn('send:inputTravellers@calendar-fare-bonus', calls)
        self.assertIn("'inputTravellers-permit-denied': 1", self.summary())
        self.assertNotEqual(intent, 'sending')

    def test_send_exception_leaves_unknown_intent_and_permit(self):
        class Boom(dict):
            def __contains__(self, key):
                return key == 'inputTravellers'

            def __getitem__(self, key):
                raise RuntimeError('page closed')
        with self.assertRaises(RuntimeError):
            self.run_main(send_override=Boom(hooked=True))
        self.assertEqual(self.intent_state(), 'unknown')
        self.assertTrue(self.permit_file().exists())
        code, connect, _, _ = self.run_main(tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_start_after_the_fire_time_is_refused(self):
        clock = FakeClock(datetime(2099, 1, 1, 9, 0, 1, tzinfo=live_order.KST))
        code, connect, calls, _ = self.run_main('--at', '09:00:00', clock=clock)
        self.assertEqual((code, connect.call_count, calls), (2, 0, []))

    def test_run_day_must_be_today(self):
        clock = FakeClock(datetime(2098, 12, 31, 23, 0, 0, tzinfo=live_order.KST))
        code, connect, _, _ = self.run_main('--at', '09:00:00', clock=clock)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_arm_after_the_fire_time_is_refused(self):
        arm = Path(tempfile.mkdtemp()) / 'armed'
        self.addCleanup(shutil.rmtree, arm.parent, True)
        fire = datetime(2099, 1, 1, 9, 0, 0, tzinfo=live_order.KST)

        def arm_at_fire_time(clock):
            if clock.t >= fire:
                arm.write_text('go', encoding='utf-8')
        # 무장 파일이 발사 시각에 맞춰 생겨 대기 루프를 빠져나온 경우: 무장 확인 뒤 검사가 막는다.
        clock = FakeClock(datetime(2099, 1, 1, 8, 59, 58, tzinfo=live_order.KST),
                          on_sleep=arm_at_fire_time)
        code, _, calls, _ = self.run_main('--at', '09:00:00', '--arm-file', str(arm), clock=clock)
        self.assertEqual(code, 2)
        self.assertEqual([c for c in calls if c.startswith('send:')], [])
        self.assertIn('발사 시각이 지난 뒤 무장이 확인됐다 - 발사하지 않는다', self.logs)

    def test_never_armed_before_the_fire_time_is_refused(self):
        arm = Path(tempfile.mkdtemp()) / 'armed'
        self.addCleanup(shutil.rmtree, arm.parent, True)
        clock = FakeClock(datetime(2099, 1, 1, 8, 59, 58, tzinfo=live_order.KST))
        code, _, calls, _ = self.run_main('--at', '09:00:00', '--arm-file', str(arm), clock=clock)
        self.assertEqual(code, 2)
        self.assertEqual([c for c in calls if c.startswith('send:')], [])
        self.assertIn('발사 시각까지 무장되지 않았다 - 발사하지 않는다', self.logs)

    def test_arm_before_the_fire_time_fires_once(self):
        arm = Path(tempfile.mkdtemp()) / 'armed'
        self.addCleanup(shutil.rmtree, arm.parent, True)
        clock = FakeClock(datetime(2099, 1, 1, 8, 59, 50, tzinfo=live_order.KST),
                          on_sleep=lambda c: arm.write_text('go', encoding='utf-8'))
        code, _, calls, _ = self.run_main('--at', '09:00:00', '--arm-file', str(arm), clock=clock)
        self.assertEqual(code, 0)
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)

    def test_waking_up_too_late_does_not_fire(self):
        # 대기 중 절전 등으로 늦게 깨면(허용 3초 초과) 늦은 주문을 보내지 않는다.
        clock = FakeClock(datetime(2099, 1, 1, 8, 59, 58, tzinfo=live_order.KST), oversleep=5.0)
        code, _, calls, _ = self.run_main('--at', '09:00:00', clock=clock)
        self.assertEqual(code, 2)
        self.assertEqual([c for c in calls if c.startswith('send:')], [])

    def test_status_is_read_only(self):
        self.run_main()
        before = sorted(p.name for p in self.tmp.iterdir())
        with mock.patch.object(live_order, 'STATE', self.tmp), \
                mock.patch.object(live_order, 'log', lambda m: None), \
                mock.patch.object(sys, 'argv', ['live_order.py', '--date', '2027-09-09',
                                                '--day', '2099-01-01', '--status']):
            self.assertEqual(live_order.main(), 2)
        self.assertEqual(sorted(p.name for p in self.tmp.iterdir()), before)


class ContinuePaymentTests(FlowHarness):
    """D5 연결: 주문 뒤 후반 결과가 완료일 때만 exit 0. 어느 경우도 재주문하지 않는다."""

    def run_with(self, outcome):
        seen = {}

        def fake_pass(page, **kw):
            seen.update(kw)
            return outcome
        with mock.patch.object(live_order.site_drive, 'payment_pass', fake_pass):
            result = self.run_main('--continue-payment')
        return result, seen

    def intent(self):
        return json.loads((self.tmp / 'order-intent-2099-01-01.json').read_text(encoding='utf-8'))

    def test_completed_back_half_exits_zero_and_records_the_window(self):
        (code, _, calls, _), seen = self.run_with({'completed': True, 'stage': 'npay-checkout',
                                                   'order': 'same-order-reference', 'counts': {}})
        self.assertEqual(code, 0)
        self.assertEqual((seen['origin'], seen['destination']), ('ICN', 'CDG'))
        self.assertEqual(str(seen['amount']), '325500')
        self.assertEqual(seen['reference'], 'FAKEPNR')
        rec = self.intent()
        self.assertEqual((rec['state'], rec['handoff'], rec['paymentWindowReached']),
                         ('ordered', 'npay-checkout', True))
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)
        self.assertFalse(any('FAKEPNR' in x for x in self.logs))

    def test_incomplete_back_half_exits_two_and_keeps_the_order_blocking(self):
        (code, _, calls, _), _ = self.run_with({'completed': False, 'stage': 'provider-window:login-required',
                                                'order': 'same-order-reference', 'counts': {}})
        self.assertEqual(code, 2)
        rec = self.intent()
        self.assertEqual((rec['state'], rec['paymentWindowReached']), ('ordered', False))
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)
        code, connect, _, _ = self.run_main(tmp=self.tmp)
        self.assertEqual((code, connect.call_count), (2, 0))


class LedgerTests(unittest.TestCase):
    def test_counts_by_phase_without_values(self):
        ledger = live_order.Ledger()
        ledger.add('prep', 'inputTravellers-blocked')
        ledger.add('fire', 'awardAvailability', 2)
        self.assertEqual(ledger.summary(), {'prep': {'inputTravellers-blocked': 1},
                                            'fire': {'awardAvailability': 2}, 'handoff': {},
                                            'observe': {}, 'health': {}})

    def test_invalid_entries_are_refused(self):
        ledger = live_order.Ledger()
        for args in (('other', 'x'), ('fire', 1), ('fire', 'x', True), ('fire', 'x', 1.0)):
            with self.assertRaises(ValueError):
                ledger.add(*args)


NOT_OPEN = {'code': 'ERT.10032'}
EMPTY = {'emptyFare': True, 'currency': 'KRW'}


def ok_json(payload):
    return {'ok': True, 'status': 200, 'elapsedMs': 40.0, 'body': json.dumps(payload)}


class OpenRetryTests(FlowHarness):
    """P1: 시계 보정·시각 기준 재시도·선발사. 개방 09:00:00, 기본 불확실성 10ms.

    재시도 판정은 응답 코드가 아니라 보낸 시각이다. 신뢰 경계(개방+불확실성) 전에 보낸 조회의
    부정 응답은 전부 재시도하고, 경계 뒤에는 기존 판정을 신뢰한다(not-open 만 상한 안 재조회).
    """
    OPEN = datetime(2099, 1, 1, 9, 0, 0, tzinfo=live_order.KST)

    def fire_with(self, award_replies, *extra, start=(8, 59, 58), clock_states=None,
                  observe_reply=None, probe_replies=None):
        replies = list(award_replies)
        self.award_times = []
        self.observe_times = []
        self.probe_times = []

        def hook(name, body):
            if name != 'awardAvailability':
                return None
            date = json.loads(body)['segmentList'][0]['departureDate']
            if date == '20270907' and probe_replies is not None:
                self.probe_times.append(self.clock.t)
                reply = probe_replies.pop(0) if len(probe_replies) > 1 else probe_replies[0]
                return reply if 'status' in reply else ok_json(reply)
            if date != '20270909':
                self.observe_times.append(self.clock.t)
                return ok_json(observe_reply if observe_reply is not None else NOT_OPEN)
            self.award_times.append(self.clock.t)
            reply = replies.pop(0) if replies else NOT_OPEN
            return ok_json(reply)
        clock = FakeClock(datetime(2099, 1, 1, *start, tzinfo=live_order.KST))
        return self.run_main(*extra, clock=clock, send_hook=hook, clock_states=clock_states)

    def timing(self):
        files = sorted(self.tmp.glob('fire-timing-2099-01-01-*.json'))
        self.assertEqual(len(files), 1)
        return json.loads(files[0].read_text(encoding='utf-8'))

    def test_every_negative_before_the_boundary_is_retried_then_one_order(self):
        # 선발사 500ms: 08:59:59.5 부터 150ms 간격. 네 번 모두 경계(09:00:00.010) 전 송신.
        code, _, calls, intent = self.fire_with(
            [NOT_OPEN, EMPTY, award_response(soldout=True), {'errorCode': 'E'}, award_response()],
            '--at', '09:00:00', '--pre-fire-ms', '500')
        self.assertEqual((code, intent), (0, 'ordered'))
        self.assertEqual(self.sends(calls).count('send:inputTravellers@calendar-fare-bonus'), 1)
        self.assertEqual(len(self.award_times), 5)
        self.assertLess(self.award_times[0], self.OPEN)
        t = self.timing()
        self.assertEqual([x['reason'] for x in t['attempts']],
                         ['before-trust-boundary'] * 4 + ['selected'])
        self.assertEqual([x['verdict'] for x in t['attempts'][:3]],
                         ['not-open', 'no-target', 'sold-out'])
        self.assertEqual(t['preFireMs'], 500)
        self.assertEqual(t['attempts'][0]['shape']['code'], 'ERT.10032')
        self.assertEqual(t['attempts'][1]['shape']['emptyFare'], True)
        self.assertIn("'awardAvailability-retry': 4", self.summary())

    def test_negative_after_the_boundary_is_trusted(self):
        # 선발사 0: 첫 조회 09:00:00.001(경계 전) 매진은 재시도, 두 번째(.151) 매진은 신뢰.
        sold = award_response(soldout=True)
        code, _, calls, intent = self.fire_with([sold, sold, award_response()], '--at', '09:00:00')
        self.assertEqual(code, 2)
        self.assertNotIn('send:inputTravellers@calendar-fare-bonus', calls)
        self.assertEqual(len(self.award_times), 2)
        self.assertGreaterEqual(self.award_times[0], self.OPEN)
        self.assertEqual([x['reason'] for x in self.timing()['attempts']],
                         ['before-trust-boundary', 'trusted-verdict'])
        self.assertNotEqual(intent, 'sending')

    def test_no_target_and_business_error_after_the_boundary_stop(self):
        for reply in (EMPTY, {'errorCode': 'E'}):
            with self.subTest(reply):
                code, _, calls, _ = self.fire_with([reply, reply, award_response()],
                                                   '--at', '09:00:00')
                self.assertEqual((code, len(self.award_times)), (2, 2))

    def test_transient_error_after_the_boundary_is_retried(self):
        # 2026-09-20 사용자 결정: ERT.3002("잠시 후 다시 시도")는 좌석 판정이 아니다 → 경계 뒤에도 재조회.
        busy = {'code': 'ERT.3002', 'message': '정상적으로 처리되지 않았습니다. 잠시 후 다시 시도해 주세요.'}
        code, _, calls, intent = self.fire_with([NOT_OPEN, busy, busy, award_response()], '--at', '09:00:00')
        self.assertEqual((code, intent), (0, 'ordered'))
        self.assertEqual(len(self.award_times), 4)
        self.assertEqual([x['reason'] for x in self.timing()['attempts']][1:3], ['transient-error'] * 2)
        self.assertEqual(calls.count('send:inputTravellers@calendar-fare-bonus'), 1)

    def test_other_business_error_after_the_boundary_still_stops(self):
        code, _, _, _ = self.fire_with([NOT_OPEN, {'code': 'ERT.9999'}, award_response()], '--at', '09:00:00')
        self.assertEqual((code, len(self.award_times)), (2, 2))
        self.assertEqual(self.timing()['attempts'][-1]['reason'], 'trusted-verdict')

    def test_not_open_after_the_boundary_keeps_polling_within_limits(self):
        code, _, calls, intent = self.fire_with([NOT_OPEN] * 5 + [award_response()],
                                                '--at', '09:00:00')
        self.assertEqual((code, intent), (0, 'ordered'))
        self.assertEqual(len(self.award_times), 6)
        self.assertEqual([x['reason'] for x in self.timing()['attempts']][1:5], ['not-open'] * 4)

    def test_retry_cap(self):
        code, _, calls, _ = self.fire_with([], '--at', '09:00:00', '--open-retry-max', '2')
        self.assertEqual((code, len(self.award_times)), (2, 3))
        self.assertEqual(self.timing()['attempts'][-1]['reason'], 'retry-cap')
        self.assertNotIn('send:inputTravellers@calendar-fare-bonus', calls)

    def test_retry_deadline(self):
        # 150ms 간격: .001 .151 .301 뒤 다음 송신(.451)이 마감(.400)을 넘는다.
        code, _, _, _ = self.fire_with([], '--at', '09:00:00', '--open-retry-until-ms', '400')
        self.assertEqual((code, len(self.award_times)), (2, 3))
        self.assertEqual(self.timing()['attempts'][-1]['reason'], 'retry-deadline')

    def test_gaps_are_sequential_not_bursts(self):
        self.fire_with([NOT_OPEN] * 3 + [award_response()], '--at', '09:00:00')
        gaps = [(b - a).total_seconds() for a, b in zip(self.award_times, self.award_times[1:])]
        self.assertTrue(all(g >= 0.15 for g in gaps), gaps)

    def test_immediate_fire_has_no_retry_window(self):
        code, _, _, _ = self.fire_with([NOT_OPEN, award_response()], start=(8, 0, 0))
        self.assertEqual((code, len(self.award_times)), (2, 1))
        self.assertEqual(self.timing()['attempts'][0]['reason'], 'no-open-time')

    def test_clock_offset_moves_the_local_fire_time(self):
        # 기준시각이 로컬보다 2초 빠르면 로컬 08:59:58 에 보정 09:00:00 이 된다.
        ahead = dict(DEFAULT_CLOCK, offset=2.0)
        code, _, _, _ = self.fire_with([award_response()], '--at', '09:00:00',
                                       start=(8, 59, 50), clock_states=[ahead, ahead])
        self.assertEqual(code, 0)
        local = self.award_times[0]
        self.assertGreaterEqual(local, datetime(2099, 1, 1, 8, 59, 58, tzinfo=live_order.KST))
        self.assertLess(local, datetime(2099, 1, 1, 8, 59, 58, 100000, tzinfo=live_order.KST))
        self.assertEqual(self.timing()['clock']['offsetMs'], 2000.0)

    def test_failed_clock_forbids_pre_fire(self):
        code, _, _, _ = self.fire_with([award_response()], '--at', '09:00:00', '--pre-fire-ms', '500',
                                       clock_states=[FAILED_CLOCK, FAILED_CLOCK])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(self.award_times[0], self.OPEN)
        t = self.timing()
        self.assertEqual((t['preFireMs'], t['clock']['uncertaintyMs']), (0, None))
        self.assertIn('선발사 금지', ' '.join(self.logs))

    def test_failed_clock_widens_the_trust_boundary(self):
        # 불확실성을 모르면 경계 여유는 --unmeasured-clock-margin-ms(기본 3초)다.
        sold = award_response(soldout=True)
        code, _, _, _ = self.fire_with([sold] * 3 + [award_response()], '--at', '09:00:00',
                                       clock_states=[FAILED_CLOCK, FAILED_CLOCK])
        self.assertEqual((code, len(self.award_times)), (0, 4))

    def test_failed_final_measurement_drops_pre_fire(self):
        code, _, _, _ = self.fire_with([award_response()], '--at', '09:00:00', '--pre-fire-ms', '500',
                                       clock_states=[DEFAULT_CLOCK, FAILED_CLOCK])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(self.award_times[0], self.OPEN)
        self.assertEqual([m['label'] for m in self.timing()['clock']['measurements']],
                         ['start', 'final'])
        self.assertIn('선발사 500ms → 0', ' '.join(self.logs))

    def test_successful_final_measurement_restores_pre_fire(self):
        # 2026-09-20 콜드: 시작 측정 실패·최종 측정 성공인데 선발사가 0 으로 남았다.
        code, _, _, _ = self.fire_with([award_response()], '--at', '09:00:00', '--pre-fire-ms', '500',
                                       clock_states=[FAILED_CLOCK, DEFAULT_CLOCK])
        self.assertEqual(code, 0)
        self.assertLess(self.award_times[0], self.OPEN)
        self.assertGreaterEqual(self.award_times[0], self.OPEN - timedelta(milliseconds=500))
        self.assertEqual(self.timing()['preFireMs'], 500)
        self.assertIn('선발사 500ms 복원', ' '.join(self.logs))

    def test_large_uncertainty_forbids_pre_fire(self):
        wide = dict(DEFAULT_CLOCK, uncertainty=0.2)
        code, _, _, _ = self.fire_with([award_response()], '--at', '09:00:00', '--pre-fire-ms', '500',
                                       clock_states=[wide, wide])
        self.assertEqual(code, 0)
        self.assertGreaterEqual(self.award_times[0], self.OPEN)

    def test_final_measurement_bypasses_the_cache(self):
        self.fire_with([award_response()], '--at', '09:00:00', start=(8, 58, 0))
        self.assertEqual(len(self.clock_calls), 2)
        self.assertGreaterEqual(self.clock_calls[1], datetime(2099, 1, 1, 8, 59, 0, tzinfo=live_order.KST))

    def test_parameter_limits(self):
        for extra in (('--at', '09:00:00', '--pre-fire-ms', '3001'), ('--pre-fire-ms', '100'),
                      ('--at', '09:00:00', '--open-retry-max', '-1'),
                      ('--observe-date', '2027-09-10', '--observe-count', '4'),
                      ('--observe-date', '2027-09-09'),
                      ('--at', '09:00:00', '--observe-date', '2027-09-10')):
            with self.subTest(extra):
                code, connect, _, _ = self.fire_with([award_response()], *extra)
                self.assertEqual((code, connect.call_count), (2, 0))

    def test_observe_unopened_date_records_shape_only(self):
        code, _, calls, intent = self.fire_with(
            [award_response()], '--observe-date', '2027-09-10', '--observe-count', '2',
            start=(8, 0, 0))
        self.assertEqual((code, intent), (0, 'ordered'))
        self.assertEqual(len(self.observe_times), 2)
        self.assertEqual(calls.count('send:fareInformation@calendar-fare-bonus'), 1)
        t = self.timing()
        self.assertEqual([o['verdict'] for o in t['observe']], ['not-open', 'not-open'])
        self.assertEqual(t['observe'][0]['shape']['code'], 'ERT.10032')
        self.assertIn("'observe': {'awardAvailability-attempt': 2}", self.summary())

    def test_timing_evidence_has_no_identifiers(self):
        self.fire_with([award_response(soldout=True), award_response()],
                       '--at', '09:00:00', '--pre-fire-ms', '100')
        raw = next(self.tmp.glob('fire-timing-*.json')).read_text(encoding='utf-8')
        for secret in ('NEW-PR', 'NEW-F', 'NEW-EY', 'FAKE-TICKET', 'FAKEPNR'):
            self.assertNotIn(secret, raw)
        shape = json.loads(raw)['attempts'][0]['shape']
        self.assertEqual(shape['targetFlight']['family'], {'soldout': True, 'seatCount': '3'})


class ResponseShapeTests(unittest.TestCase):
    TARGET = live_order.Target('2027-09-09', 'ICN', 'CDG', 'KEBONUSPR', 'KE', '901')

    def test_free_text_codes_are_reduced_to_type(self):
        shape = live_order.response_shape(
            ok_json({'code': '고객님 정보 홍길동', 'errorMessage': 'x', 'currency': 'USD'}), self.TARGET)
        self.assertEqual(shape['code'], '<str>')
        self.assertTrue(shape['errorMessagePresent'])
        self.assertEqual(shape['currency'], 'USD')

    def test_non_json_and_transport_errors(self):
        self.assertEqual(live_order.response_shape({'ok': True, 'status': 200, 'body': '<html>'},
                                                   self.TARGET)['body'], 'not-json')
        self.assertEqual(live_order.response_shape({'ok': False, 'error': 'origin-changed'},
                                                   self.TARGET)['transportError'], 'origin-changed')


class MeasureClockCacheTests(unittest.TestCase):
    """runtime.measure_clock 의 30분 캐시: 1800초가 지나면 다시 잰다(09:00 발사 때 만료 확인)."""

    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import runtime
        self.runtime = runtime
        patcher = mock.patch.dict('os.environ', {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)

    def measure(self, cached_age):
        import os
        now = datetime.now(live_order.KST).timestamp()
        os.environ['KE_CLOCK'] = json.dumps({'ok': True, 'offset': 9.9, 'uncertainty': 0.01,
                                             'source': 'cached', 'measuredAt': now - cached_age})
        with mock.patch('ke_award.clock._query', return_value=(0.123, 0.02)) as query:
            state = self.runtime.measure_clock()
        return state, query.call_count

    def test_fresh_cache_is_reused(self):
        state, n = self.measure(100)
        self.assertEqual((state['source'], n), ('cached', 0))

    def test_expired_cache_is_measured_again(self):
        # 08:20 준비 때 잰 값은 09:00(40분 뒤)에 만료되어 새로 잰다.
        state, n = self.measure(40 * 60)
        self.assertEqual(n, 2)
        self.assertEqual(state['offset'], 0.123)

    def test_fire_clock_always_bypasses_the_cache(self):
        import os
        os.environ['KE_CLOCK'] = json.dumps({'ok': True, 'offset': 9.9, 'uncertainty': 0.01,
                                             'source': 'cached',
                                             'measuredAt': datetime.now(live_order.KST).timestamp()})
        clock = live_order.FireClock(3.0)
        with mock.patch('ke_award.clock._query', return_value=(0.123, 0.02)) as query:
            self.assertTrue(clock.measure('final'))
        self.assertEqual((query.call_count, clock.offset, clock.uncertainty), (2, 0.123, 0.01))


PROBE_OK = award_response(date='20270907112000')
PROBE_USD = dict(award_response(date='20270907112000'), currency='USD')


class ReadinessTests(OpenRetryTests):
    """P2: 미개방 형태 대조·무장 점검·세션 점검. OpenRetryTests 의 하네스를 쓴다."""
    CAPTURE = ('--capture-iso', '2027-09-07')

    def test_not_open_shape_keeps_polling_after_the_boundary(self):
        sig = self.tmp_file({'status': 200, 'emptyFare': True, 'keys': ['currency', 'emptyFare']})
        code, _, _, intent = self.fire_with([EMPTY] * 4 + [award_response()], '--at', '09:00:00',
                                            '--not-open-shape', str(sig))
        self.assertEqual((code, intent), (0, 'ordered'))
        self.assertEqual([x['reason'] for x in self.timing()['attempts']][1:4], ['not-open-shape'] * 3)

    def test_not_open_shape_never_covers_sold_out_or_other_shapes(self):
        sig = self.tmp_file({'status': 200, 'emptyFare': True, 'keys': ['currency', 'emptyFare']})
        sold = award_response(soldout=True)
        code, _, _, _ = self.fire_with([sold, sold], '--at', '09:00:00', '--not-open-shape', str(sig))
        self.assertEqual((code, len(self.award_times)), (2, 2))
        code, _, _, _ = self.fire_with([{'errorCode': 'E'}] * 2, '--at', '09:00:00',
                                       '--not-open-shape', str(sig))
        self.assertEqual((code, len(self.award_times)), (2, 2))

    def test_bad_not_open_shape_file_is_refused(self):
        code, connect, _, _ = self.fire_with([], '--at', '09:00:00',
                                             '--not-open-shape', str(self.tmp_file({'x': 1})))
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_arm_probe_passes_with_krw_and_target_flight(self):
        code, _, _, intent = self.fire_with([award_response()], *self.CAPTURE, start=(8, 0, 0),
                                            probe_replies=[PROBE_OK])
        self.assertEqual((code, intent), (0, 'ordered'))
        self.assertEqual(len(self.probe_times), 1)
        self.assertIn("'health': {'arm-awardAvailability': 1}", self.summary())

    def test_arm_probe_failures(self):
        for reply, want in ((PROBE_USD, live_order.EXIT_REPREPARE),
                            ({'ok': True, 'status': 401, 'body': ''}, live_order.EXIT_REPREPARE),
                            ({'ok': False, 'error': 'TypeError'}, live_order.EXIT_REPREPARE),
                            (award_response(date='20270907112000', flight='905'), 2)):
            with self.subTest(want=want, reply=str(reply)[:40]):
                code, _, calls, _ = self.fire_with([award_response()], *self.CAPTURE,
                                                   start=(8, 0, 0), probe_replies=[reply])
                self.assertEqual(code, want)
                self.assertEqual(self.award_times, [])
                self.assertNotIn('send:inputTravellers@calendar-fare-bonus', calls)

    def test_health_check_runs_once_before_fire(self):
        code, _, _, _ = self.fire_with([award_response()], *self.CAPTURE, '--at', '09:00:00',
                                       '--health-at', '08:50:00', start=(8, 40, 0),
                                       probe_replies=[PROBE_OK])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.probe_times), 2)
        health = self.probe_times[1]
        self.assertGreaterEqual(health, datetime(2099, 1, 1, 8, 50, 0, tzinfo=live_order.KST))
        self.assertLess(health, datetime(2099, 1, 1, 8, 51, 0, tzinfo=live_order.KST))

    def test_health_failure_stops_before_fire_with_reprepare_code(self):
        code, _, calls, intent = self.fire_with(
            [award_response()], *self.CAPTURE, '--at', '09:00:00', '--health-at', '08:50:00',
            start=(8, 40, 0), probe_replies=[PROBE_OK, {'ok': True, 'status': 401, 'body': ''}])
        self.assertEqual(code, live_order.EXIT_REPREPARE)
        self.assertEqual(self.award_times, [])
        self.assertNotEqual(intent, 'sending')

    def test_health_time_must_precede_fire(self):
        for extra in (('--health-at', '08:50:00'), ('--at', '09:00:00', '--health-at', '09:00:00')):
            code, connect, _, _ = self.fire_with([], *extra)
            self.assertEqual((code, connect.call_count), (2, 0))

    def test_capture_iso_must_match_the_label(self):
        code, connect, _, _ = self.fire_with([], '--capture-iso', '2027-09-08')
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_state_dir_cannot_be_the_live_folder(self):
        live = str(live_order.ROOT / 'dev-shots' / 'state')
        code, connect, _, _ = self.fire_with([], '--state-dir', live)
        self.assertEqual((code, connect.call_count), (2, 0))

    def test_rehearsal_state_dir_keeps_the_permit_out_of_the_live_folder(self):
        rehearsal = self.make_tmp()
        with mock.patch.object(live_order, 'STATE', live_order.STATE):
            code, _, _, _ = self.fire_with([award_response()], '--state-dir', str(rehearsal),
                                           start=(8, 0, 0))
        self.assertEqual(code, 0)
        self.assertTrue((rehearsal / 'order-permit.json').exists())
        self.assertFalse((self.tmp / 'order-permit.json').exists())

    def test_observe_is_allowed_with_at_only_for_rehearsal_state_dir(self):
        rehearsal = self.make_tmp()
        with mock.patch.object(live_order, 'STATE', live_order.STATE):
            code, _, _, _ = self.fire_with([award_response()], '--at', '09:00:00',
                                           '--observe-date', '2027-09-10',
                                           '--state-dir', str(rehearsal))
        self.assertEqual(code, 0)
        self.assertEqual(len(self.observe_times), 1)

    def make_tmp(self):
        path = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, path, True)
        return path

    def tmp_file(self, data):
        path = self.make_tmp() / 'shape.json'
        path.write_text(json.dumps(data), encoding='utf-8')
        return path


class ReleaseNoReferenceTests(unittest.TestCase):
    """예약번호 없는 unknown 의 사용자 확인 보관(삭제 아님). 사이트 접속 없음."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.run_id = 'abcdef123456'
        (self.tmp / 'order-intent-2099-01-01.json').write_text(json.dumps(
            {'day': '2099-01-01', 'state': 'unknown', 'why': 'business-error', 'runId': self.run_id}),
            encoding='utf-8')
        (self.tmp / 'order-permit.json').write_text(json.dumps({'runId': self.run_id}), encoding='utf-8')
        self.write_receipt(pnr=None)

    def write_receipt(self, pnr):
        d = self.tmp / 'order-evidence' / self.run_id
        d.mkdir(parents=True, exist_ok=True)
        (d / 'received.json').write_text(json.dumps({'runId': self.run_id, 'pnr': pnr, 'orderId': None,
            'diagnostic': {'state': 'received'}}), encoding='utf-8')

    def release(self, *extra):
        logs = []
        argv = ['live_order.py', '--date', '2027-09-09', '--day', '2099-01-01',
                '--release-no-reference', *extra]
        with mock.patch.object(sys, 'argv', argv), mock.patch.object(live_order, 'STATE', self.tmp),                 mock.patch.object(live_order, 'log', logs.append):
            return live_order.main(), logs

    def test_moves_intent_and_permit_into_released_folder(self):
        code, _ = self.release('--release-reason', '09:00 business-error, 예약번호 없음, 사용자 확인')
        self.assertEqual(code, 0)
        self.assertFalse((self.tmp / 'order-permit.json').exists())
        self.assertFalse((self.tmp / 'order-intent-2099-01-01.json').exists())
        kept = list((self.tmp / 'released').iterdir())
        self.assertEqual(len(kept), 1)
        self.assertTrue((kept[0] / 'order-permit.json').exists())
        self.assertTrue((kept[0] / 'order-intent-2099-01-01.json').exists())
        note = json.loads((kept[0] / 'release.json').read_text(encoding='utf-8'))
        self.assertIn('서버 해제 확인 아님', note['meaning'])

    def test_refuses_when_a_reference_exists_or_records_disagree(self):
        code, _ = self.release()
        self.assertEqual(code, 2)                        # 사유 없음
        self.write_receipt(pnr='FAKEPNR')
        code, _ = self.release('--release-reason', 'x')
        self.assertEqual(code, 2)
        self.write_receipt(pnr=None)
        (self.tmp / 'order-permit.json').write_text(json.dumps({'runId': 'ffffffffffff'}), encoding='utf-8')
        code, _ = self.release('--release-reason', 'x')
        self.assertEqual(code, 2)
        self.assertTrue((self.tmp / 'order-intent-2099-01-01.json').exists())
        self.assertFalse((self.tmp / 'released').exists())


if __name__ == '__main__':unittest.main()
