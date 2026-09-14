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
                 replies=None, mileage='100000', reuse=True, clock=None, initial_url=GATE_URL):
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
            if send_override and name in send_override:
                return send_override[name]
            reply = {'awardAvailability': award_response(), 'fareInformation': fare_response(),
                     'inputTravellers': order_response(), **(replies or {})}[name]
            return {'ok': True, 'status': 200, 'elapsedMs': 1.0,
                    'body': reply if isinstance(reply, str) else json.dumps(reply)}

        self.logs = []
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
        code, _, calls, _ = self.run_main('--dry', on_calendar=False)
        self.assertEqual(code, 2)
        self.assertEqual(self.sends(calls), [])

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
                                            'fire': {'awardAvailability': 2}, 'handoff': {}})

    def test_invalid_entries_are_refused(self):
        ledger = live_order.Ledger()
        for args in (('other', 'x'), ('fire', 1), ('fire', 'x', True), ('fire', 'x', 1.0)):
            with self.assertRaises(ValueError):
                ledger.add(*args)

if __name__ == '__main__':unittest.main()
