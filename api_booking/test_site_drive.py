"""D1 인계 관찰기·게이트 인계의 로컬 시험. 가짜 컨텍스트/페이지만 쓴다.

Playwright 의 실제 이벤트 순서·route 차단이나 사이트 게이트 동작의 증거가 아니다.
주문 참조 출처(baggagePolicies GET 쿼리 orderId)는 수집 20260912-232135-f66cf6b8
정상 UI 2회 관측이며, API 재전송 주문 뒤 게이트 재진입에서의 동작은 미관측이다.
"""
import itertools
import sys
from unittest import mock
import types
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
import site_drive  # noqa: E402
from handoff import ORDER_PATH  # noqa: E402

SITE = 'https://www.koreanair.com'
ORDER_URL = SITE + ORDER_PATH
BAGGAGE_URL = SITE + '/api/et/ibeSupport/baggagePolicies'
FARE_URL = SITE + '/api/ap/booking/avail/fareInformation'
# 9/13 08:17 게이트 재진입 화면 단서와 같은 형태. 캡처 주문과 API 주문이 모두 이 문구였다.
SAME_TEXT = ['09월 07일 (화)', '1명 (1 석)', '35,000 마일', '325,500 원']


class FakeRequest:
    def __init__(self, url, method, frame, post_data=None):
        self.url, self.method, self._frame, self.post_data = url, method, frame, post_data

    @property
    def frame(self):
        if self._frame is None:
            raise RuntimeError('service worker request has no frame')
        return self._frame


class FakeRoute:
    def __init__(self, request, fail):
        self.request, self._fail, self.aborted = request, fail, False

    def abort(self, error_code=None):
        if self._fail:
            raise RuntimeError('abort failed')
        self.aborted = True


class FakeContext:
    def __init__(self, *, route_first=True, abort_fails=False):
        self.routes, self.listeners, self.reached_server = [], [], []
        self.route_first, self.abort_fails = route_first, abort_fails

    def route(self, url, handler):
        self.routes.append((url, handler))

    def unroute(self, url, handler):
        self.routes = [r for r in self.routes if not (r[0] == url and r[1] == handler)]

    def on(self, event, handler):
        assert event == 'request'
        self.listeners.append(handler)

    def remove_listener(self, event, handler):
        self.listeners.remove(handler)

    def emit(self, url, method='POST', frame=None, body=None):
        req, aborted = FakeRequest(url, method, frame, body), []

        def routes():
            for match, handler in list(self.routes):
                if match(url):
                    route = FakeRoute(req, self.abort_fails)
                    handler(route)
                    aborted.append(route.aborted)
                    return

        def listeners():
            for handler in list(self.listeners):
                handler(req)

        for step in ((routes, listeners) if self.route_first else (listeners, routes)):
            step()
        if not any(aborted):
            self.reached_server.append(urlsplit(url).path)


class FakePage:
    """게이트 이동 시 사이트가 보낼 요청과 화면 문구를 흉내 낸다."""

    def __init__(self, context, *, on_gate=(), summary=SAME_TEXT, on_click=None,
                 before_nav=(), goto_error=None):
        self.context, self.on_gate, self.summary = context, list(on_gate), list(summary)
        self.url = SITE + '/booking/calendar-fare-bonus'
        self.gotos, self.clicked, self._last_id = [], [], None
        self.on_click = on_click or {}
        self.before_nav, self.goto_error = list(before_nav), goto_error
        self.main_frame = object()
        self.nav_listeners = []
        self.mouse = types.SimpleNamespace(click=self._click)
        # 게이트 후반 모델: 동의 버튼 → 모달(스크롤 2번) → 확인 → 켜짐. 켜진 동의를 누르면 꺼진다.
        self.agree = {'btn-resv-agree-1': 'off', 'btn-resv-agree-3': 'off'}
        self.no_modal = set()          # 눌러도 모달이 뜨지 않는 동의
        self.modal, self.scrolls, self.npay = None, 0, False
        self.npay_clickable = True
        self.after_confirm = {}        # 확인 뒤 부작용: {동의 id: (다른 동의 id, 상태)}
        self.deduct = (200, '{"ok": true}')   # 마일리지 적용 때 차감 API 응답. None 이면 응답 없음

    def expect_response(self, predicate, timeout=None):
        page = self

        class Waiter:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                if exc_type:
                    return False
                if page.deduct is None or 'btnAwardUseMileageApply' not in page.clicked:
                    raise TimeoutError('no response')
                status, text = page.deduct
                url = SITE + site_drive.DEDUCT_PATH
                assert predicate(types.SimpleNamespace(url=url))
                self.value = types.SimpleNamespace(url=url, status=status, text=lambda: text)
                return False
        return Waiter()

    def on(self, event, handler):
        assert event == 'framenavigated'
        self.nav_listeners.append(handler)

    def remove_listener(self, event, handler):
        self.nav_listeners.remove(handler)

    def send(self, spec, frame='main'):
        url, method, *body = spec
        self.context.emit(url, method, self.main_frame if frame == 'main' else frame,
                          body[0] if body else None)

    def goto(self, url, **kw):
        self.gotos.append(url)
        for spec in self.before_nav:      # 이동 직전 이전 문서가 보낸 요청
            self.send(spec)
        if self.goto_error:
            raise self.goto_error
        self.url = url
        for handler in list(self.nav_listeners):
            handler(self.main_frame)
        for spec in self.on_gate:
            self.send(*spec) if isinstance(spec[0], tuple) else self.send(spec)

    def wait_for_timeout(self, ms):
        pass

    def evaluate(self, js, arg=None):
        if 'aria-pressed' in js:   # agree_state
            return self.agree.get(arg, 'missing')
        if 'checked===true' in js:  # _radio_checked
            return self.npay if arg == 'rad-naverpay' else None
        if isinstance(arg, str):   # _box_by_id
            if arg == 'btnScrollDown' and not (self.modal and self.scrolls > 0):
                return None
            if arg == 'btnConfirm' and not (self.modal and self.scrolls == 0):
                return None
            self._last_id = arg
            return {'x': 10, 'y': 100}
        if 'innerHeight' in js:
            return 900
        if 'seen' in js:           # read_gate_summary
            return list(self.summary)
        return None

    def _click(self, x, y):
        target = self._last_id
        self.clicked.append(target)
        if target in self.agree:
            if self.agree[target] == 'on':
                self.agree[target] = 'off'
            elif target not in self.no_modal:
                self.modal, self.scrolls = target, 2
        elif target == 'btnScrollDown' and self.modal:
            self.scrolls -= 1
        elif target == 'btnConfirm' and self.modal:
            done, self.modal = self.modal, None
            if self.agree[done] != 'unknown':
                self.agree[done] = 'on'
            if done in self.after_confirm:
                other, state = self.after_confirm[done]
                self.agree[other] = state
        elif target == 'rad-naverpay' and self.npay_clickable:
            self.npay = True
        for spec in self.on_click.get(target, ()):
            self.send(spec)


def _clock():
    counter = itertools.count(1)
    return lambda: float(next(counter))


def _baggage(ref):
    # 관측 계약: GET 쿼리. 생략된 다른 파라미터 1개를 흉내 낸다.
    return (BAGGAGE_URL + '?orderId=%s&other=1' % ref, 'GET')


ORDER_REQ = (ORDER_URL, 'POST')
FARE_REQ = (FARE_URL, 'POST')


class HandoffWatchTests(unittest.TestCase):
    def start(self, ctx):
        watch = site_drive.HandoffWatch(ctx, clock=_clock())
        watch.start()
        return watch

    def test_order_request_is_aborted_in_either_event_order(self):
        for route_first in (True, False):
            ctx = FakeContext(route_first=route_first)
            watch = self.start(ctx)
            ctx.emit(ORDER_URL)
            recs = watch.records()
            self.assertEqual(len(recs), 1, route_first)
            self.assertTrue(recs[0]['blocked'])
            self.assertNotIn(ORDER_PATH, ctx.reached_server)

    def test_failed_abort_is_not_recorded_as_blocked(self):
        ctx = FakeContext(abort_fails=True)
        watch = self.start(ctx)
        ctx.emit(ORDER_URL)
        self.assertFalse(watch.records()[0]['blocked'])
        self.assertIn(ORDER_PATH, ctx.reached_server)
        self.assertEqual(watch.judge('NEWREF', 0.).state, 'new-order-requested')

    def test_only_the_reference_value_is_kept(self):
        ctx = FakeContext()
        watch = self.start(ctx)
        ctx.emit(BAGGAGE_URL + '?orderId=NEWREF&secret=PII', 'GET')
        ctx.emit(ORDER_URL)
        for rec in watch.records():
            self.assertNotIn('PII', repr(rec))
        self.assertEqual(watch.records()[0]['reference'], 'NEWREF')

    def test_requests_without_a_frame_are_not_target(self):
        page = FakePage(FakeContext())
        watch = site_drive.HandoffWatch(page.context, page=page, clock=_clock())
        watch.start()
        page.context.emit(BAGGAGE_URL + '?orderId=NEWREF', 'GET', None)
        self.assertFalse(watch.records()[0]['target'])

    def test_unrelated_requests_are_ignored(self):
        ctx = FakeContext()
        watch = self.start(ctx)
        ctx.emit(SITE + '/api/pp/payment/GetAvailablePaymentType')
        self.assertEqual(watch.records(), [])

    def test_stop_removes_route_and_listener(self):
        ctx = FakeContext()
        watch = self.start(ctx)
        watch.stop()
        self.assertEqual((ctx.routes, ctx.listeners), ([], []))
        ctx.emit(ORDER_URL)
        self.assertEqual(watch.records(), [])

    def test_cannot_start_twice(self):
        watch = self.start(FakeContext())
        with self.assertRaises(RuntimeError):
            watch.start()


class GateHandoffTests(unittest.TestCase):
    def run_handoff(self, page, reference='NEWREF'):
        self.logs = []
        return site_drive.gate_handoff(page, date='2027-09-07', mileage='35,000',
                                       reference=reference, ordered_at=0.,
                                       log=self.logs.append, clock=_clock(), wait_ms=0)

    def test_previous_order_with_identical_text_is_refused(self):
        page = FakePage(FakeContext(), on_gate=[_baggage('OLDREF')])
        out = self.run_handoff(page)
        self.assertTrue(out['hits']['date'] and out['hits']['mileage'] and out['hits']['krw'])
        self.assertEqual(out['order'], 'other-order')
        self.assertFalse(out['matched'])
        self.assertEqual(page.clicked, [])

    def test_same_reference_and_text_passes(self):
        out = self.run_handoff(FakePage(FakeContext(), on_gate=[_baggage('NEWREF')]))
        self.assertEqual(out['order'], 'same-order-reference')
        self.assertTrue(out['matched'])

    def test_text_alone_is_not_enough(self):
        out = self.run_handoff(FakePage(FakeContext()))
        self.assertEqual(out['order'], 'reference-unobserved')
        self.assertFalse(out['matched'])

    def test_reference_alone_is_not_enough(self):
        page = FakePage(FakeContext(), on_gate=[_baggage('NEWREF')],
                        summary=['09월 08일 (수)', '62,500 마일', '325,500 원'])
        out = self.run_handoff(page)
        self.assertEqual(out['order'], 'same-order-reference')
        self.assertFalse(out['matched'])

    def test_gate_that_sends_an_order_is_blocked_and_fails(self):
        ctx = FakeContext()
        page = FakePage(ctx, on_gate=[ORDER_REQ, _baggage('NEWREF')])
        out = self.run_handoff(page)
        self.assertEqual(out['order'], 'new-order-blocked')
        self.assertFalse(out['matched'])
        self.assertNotIn(ORDER_PATH, ctx.reached_server)

    def test_selection_restart_fails(self):
        out = self.run_handoff(FakePage(FakeContext(),
                                        on_gate=[FARE_REQ, _baggage('NEWREF')]))
        self.assertEqual(out['order'], 'selection-request')
        self.assertFalse(out['matched'])

    def test_new_reference_from_another_tab_does_not_pass(self):
        # 검토 d2574cfe P1: 대상 게이트는 참조를 안 보내고 다른 탭이 새 참조를 보낸다.
        other_tab = object()
        page = FakePage(FakeContext(), on_gate=[(_baggage('NEWREF'), other_tab)])
        out = self.run_handoff(page)
        self.assertEqual(out['order'], 'reference-unobserved')
        self.assertFalse(out['matched'])

    def test_request_from_the_document_before_navigation_does_not_pass(self):
        page = FakePage(FakeContext(), before_nav=[_baggage('NEWREF')])
        out = self.run_handoff(page)
        self.assertEqual(out['order'], 'reference-unobserved')

    def test_navigation_error_releases_the_guard(self):
        ctx = FakeContext()
        page = FakePage(ctx, goto_error=TimeoutError('goto'))
        with self.assertRaises(TimeoutError):
            self.run_handoff(page)
        self.assertEqual((ctx.routes, ctx.listeners, page.nav_listeners), ([], [], []))

    def test_missing_reference_does_not_navigate(self):
        ctx = FakeContext()
        page = FakePage(ctx, on_gate=[_baggage('NEWREF')])
        out = self.run_handoff(page, reference=None)
        self.assertEqual(out['order'], 'missing-reference')
        self.assertEqual((page.gotos, ctx.routes), ([], []))

    def test_watch_is_removed_and_reference_never_logged(self):
        ctx = FakeContext()
        out = self.run_handoff(FakePage(ctx, on_gate=[_baggage('NEWREF')]))
        self.assertEqual((ctx.routes, ctx.listeners), ([], []))
        self.assertNotIn('NEWREF', repr(out) + ''.join(self.logs))


NPAY_READY = {'ready': True, 'provider': 'pay.naver.com', 'stage': 'npay-checkout',
              'amountMatched': True, 'amountVerdict': 'matched'}


def _npay_session(ref):
    return (SITE + '/api/pp/payment/NaverPay', 'POST',
            '{"payment": {"reservationRecLoc": "%s", "amount": "1"}}' % ref)


class PaymentPassTests(unittest.TestCase):
    def run_pass(self, page, window=None, **kw):
        self.logs = []
        self.window_calls = []

        def fake_window(_page, original, **wkw):
            self.window_calls.append(wkw)
            return dict(window if window is not None else NPAY_READY)
        args = dict(flight='901', date='2027-09-07', mileage='35,000', reference='NEWREF',
                    ordered_at=0., log=self.logs.append, clock=_clock(), wait_ms=0,
                    destination='CDG', amount=325500, pace=0)
        args.update(kw)
        page.context.pages = []
        with mock.patch.object(site_drive, 'wait_provider_window', fake_window):
            return site_drive.payment_pass(page, **args)

    def gate(self, **kw):
        ctx = FakeContext()
        on_click = {'btn-payment': [_npay_session('NEWREF')], **kw.pop('on_click', {})}
        return ctx, FakePage(ctx, on_gate=[_baggage('NEWREF')], on_click=on_click, **kw)

    def test_full_back_half_completes_only_with_window_amount_and_session_reference(self):
        ctx, page = self.gate()
        out = self.run_pass(page)
        self.assertTrue(out['completed'], out.get('stage'))
        self.assertEqual(out['stage'], 'npay-checkout')
        self.assertTrue(out['npayReferenceMatched'])
        self.assertEqual(page.agree, {'btn-resv-agree-1': 'on', 'btn-resv-agree-3': 'on'})
        self.assertEqual(page.clicked.count('btn-payment'), 1)
        self.assertEqual(self.window_calls[0]['expected'], 'npay')
        self.assertEqual(self.window_calls[0]['amount'], 325500)

    def test_already_checked_agreement_is_not_clicked_off(self):
        # 9/13 08:33: 이미 동의한 게이트에서 동의를 다시 눌러 체크가 풀렸다.
        ctx, page = self.gate()
        page.agree['btn-resv-agree-1'] = 'on'
        out = self.run_pass(page)
        self.assertNotIn('btn-resv-agree-1', page.clicked)
        self.assertEqual(out['steps']['btn-resv-agree-1']['result'], 'already-on')
        self.assertTrue(out['completed'])

    def test_no_modal_and_still_off_fails_before_mileage(self):
        ctx, page = self.gate()
        page.no_modal.add('btn-resv-agree-1')
        out = self.run_pass(page)
        self.assertEqual(out['stage'], 'agree-failed:btn-resv-agree-1')
        self.assertNotIn('btnAwardUseMileageApply', page.clicked)
        self.assertNotIn('btn-payment', page.clicked)

    def test_agreement_turned_off_by_the_second_modal_blocks_payment(self):
        ctx, page = self.gate()
        page.after_confirm['btn-resv-agree-3'] = ('btn-resv-agree-1', 'off')
        out = self.run_pass(page)
        self.assertEqual(out['stage'], 'agree-turned-off:btn-resv-agree-1')
        self.assertNotIn('btn-payment', page.clicked)

    def test_unknown_agreement_marker_stops_without_clicking(self):
        # 검토 2ee60c2b P1: 상태를 확인할 수 없는 동의로는 진행하지 않고, 켜진 것일 수 있어 누르지도 않는다.
        ctx, page = self.gate()
        page.agree = {'btn-resv-agree-1': 'unknown', 'btn-resv-agree-3': 'unknown'}
        out = self.run_pass(page)
        self.assertEqual(out['stage'], 'agree-failed:btn-resv-agree-1')
        self.assertEqual(out['steps']['btn-resv-agree-1']['result'], 'state-unknown')
        self.assertEqual(page.clicked, [])

    def test_agreement_that_stays_unknown_after_confirm_stops(self):
        ctx, page = self.gate()
        original = page._click

        def confirm_makes_unknown(x, y):
            was_confirm = page._last_id == 'btnConfirm'
            done = page.modal
            original(x, y)
            if was_confirm:
                page.agree[done] = 'unknown'
        page.mouse.click = confirm_makes_unknown
        out = self.run_pass(page)
        self.assertEqual(out['stage'], 'agree-failed:btn-resv-agree-1')
        self.assertNotIn('btnAwardUseMileageApply', page.clicked)

    def test_mileage_apply_needs_an_ok_deduct_response(self):
        # 검토 2ee60c2b P2: 좌표 클릭 성공은 적용 증거가 아니다.
        for deduct, stage in ((None, 'mileage-deduct-response-unobserved'),
                              ((500, '{}'), 'mileage-deduct-http-error'),
                              ((200, '{"code": "E1"}'), 'mileage-deduct-business-error'),
                              ((200, '<html>'), 'mileage-deduct-unreadable')):
            with self.subTest(stage):
                ctx, page = self.gate()
                page.deduct = deduct
                out = self.run_pass(page)
                self.assertEqual(out['stage'], stage)
                self.assertNotIn('btn-payment', page.clicked)

    def test_npay_not_selected_never_clicks_payment(self):
        ctx, page = self.gate()
        page.npay_clickable = False
        out = self.run_pass(page)
        self.assertEqual(out['stage'], 'npay-not-selected')
        self.assertNotIn('btn-payment', page.clicked)

    def test_other_direction_is_not_handled(self):
        ctx, page = self.gate()
        out = self.run_pass(page, origin='CDG', destination='ICN')
        self.assertEqual(out['stage'], 'unsupported-provider')
        self.assertEqual((page.gotos, page.clicked), ([], []))

    def test_card_pg_login_or_no_window_is_not_complete(self):
        for window in ({'ready': False, 'reason': 'unrecognized-payment-provider'},
                       {'ready': False, 'provider': 'pay.naver.com', 'stage': 'login-required',
                        'loginRequired': True},
                       {'ready': False, 'reason': 'agreement-warning-on-gate'},
                       {'ready': False, 'reason': 'no-new-payment-window'}):
            with self.subTest(window):
                ctx, page = self.gate()
                out = self.run_pass(page, window=window)
                self.assertFalse(out['completed'])
                self.assertTrue(out['stage'].startswith('provider-window:'))

    def test_amount_or_session_reference_mismatch_is_not_complete(self):
        ctx, page = self.gate()
        out = self.run_pass(page, window={**NPAY_READY, 'amountMatched': False,
                                          'amountVerdict': 'ambiguous'})
        self.assertEqual((out['completed'], out['stage']), (False, 'provider-amount-ambiguous'))
        ctx, page = self.gate()
        out = self.run_pass(page, amount=None)
        self.assertEqual((out['completed'], out['stage']), (False, 'provider-amount-matched'))
        ctx, page = self.gate(on_click={'btn-payment': []})
        out = self.run_pass(page)
        self.assertEqual((out['completed'], out['stage']), (False, 'npay-reference-unobserved'))
        ctx, page = self.gate(on_click={'btn-payment': [_npay_session('OLDREF')]})
        out = self.run_pass(page)
        self.assertEqual((out['completed'], out['stage']), (False, 'order-changed-after-payment'))

    def test_existing_gate_is_never_clicked(self):
        page = FakePage(FakeContext(), on_gate=[_baggage('NEWREF')])
        out = self.run_pass(page, navigate=False, reference=None, ordered_at=None)
        self.assertEqual(out['order'], 'unverifiable-existing-gate')
        self.assertEqual((page.gotos, page.clicked), ([], []))

    def test_previous_order_gate_is_never_clicked(self):
        page = FakePage(FakeContext(), on_gate=[_baggage('OLDREF')])
        out = self.run_pass(page)
        self.assertFalse(out['matched'])
        self.assertEqual(page.clicked, [])

    def test_order_attempt_during_clicks_stops_before_payment(self):
        ctx = FakeContext()
        page = FakePage(ctx, on_gate=[_baggage('NEWREF')],
                        on_click={'btnAwardUseMileageApply': [ORDER_REQ]})
        out = self.run_pass(page)
        self.assertTrue(out['matched'])
        self.assertIn('btnAwardUseMileageApply', page.clicked)
        self.assertNotIn('btn-payment', page.clicked)
        self.assertEqual(out['orderBeforePayment'], 'new-order-blocked')
        self.assertNotIn(ORDER_PATH, ctx.reached_server)
        self.assertEqual((ctx.routes, ctx.listeners), ([], []))

    def test_order_attempt_after_payment_click_fails_the_handoff(self):
        # 검토 d2574cfe P1: 결제하기 클릭 뒤 나온 주문 요청도 실패로 전파한다.
        ctx = FakeContext()
        page = FakePage(ctx, on_gate=[_baggage('NEWREF')],
                        on_click={'btn-payment': [ORDER_REQ]})
        out = self.run_pass(page)
        self.assertIn('btn-payment', page.clicked)
        self.assertEqual(out['orderAfterPayment'], 'new-order-blocked')
        self.assertFalse(out['matched'])
        self.assertNotIn(ORDER_PATH, ctx.reached_server)



class AmountVerdictTests(unittest.TestCase):
    """검토 2ee60c2b P1: 부분 문자열로 금액을 맞추지 않는다."""

    def test_verdicts(self):
        cases = (('결제금액 325,500원', 'matched'), ('325,500 원 · 합계 325,500원', 'matched'),
                 ('결제금액 1,325,500원', 'mismatch'), ('상품 325,500원 포인트 0원', 'ambiguous'),
                 ('300,000원', 'mismatch'), ('금액 없음', 'not-visible'), ('325,5000원', 'not-visible'))
        for text, want in cases:
            with self.subTest(text):
                self.assertEqual(site_drive.amount_verdict(text, 325500), want)
        self.assertEqual(site_drive.amount_verdict('325,500원', None), 'no-expected')

AVAIL_REQ = (SITE + '/api/ap/booking/avail/awardAvailability', 'POST')


class CaptureFakePage(FakePage):
    """capture_pass 의 달력→검색→운임→다음→승객·연락처 흐름을 흉내 낸다."""

    def __init__(self, context, *, contact_sends=(ORDER_REQ,), cells=35, **kw):
        super().__init__(context, **kw)
        self.cells, self.contact_sends, self._last_text = cells, list(contact_sends), None

    def evaluate(self, js, arg=None):
        if 'n +=' in js:                       # calendar_cells
            return self.cells
        if '선택됨' in js:                      # select_date
            return {'x': 1, 'y': 1, 'selected': False, 'soldout': False}
        if '항공편명' in js:                    # select_fare
            return {'x': 2, 'y': 2, 'label': 'fare'}
        if 'kds-button' in js:                  # _box_by_text
            self._last_text = arg
            return {'x': 3, 'y': 3}
        return super().evaluate(js, arg)

    def _click(self, x, y):
        if self._last_text == '^검색$':
            self.url = SITE + '/booking/select-award-flight/departure'
            self.send(AVAIL_REQ)
        elif self._last_text == '^다음$':
            self.url = SITE + '/payment/gate/RT/NR'
            self.send(FARE_REQ)
        self._last_text = None
        if self._last_id == 'submit-contact':
            for spec in self.contact_sends:
                self.send(spec)
        self.clicked.append(self._last_id)
        self._last_id = None


class CapturePassGuardTests(unittest.TestCase):
    def run_pass(self, page):
        self.logs = []
        return site_drive.capture_pass(page, '09월 07일', log=self.logs.append,
                                       clock=_clock(), settle_ms=0)

    def test_site_order_request_is_blocked_and_counted(self):
        ctx = FakeContext()
        page = CaptureFakePage(ctx)
        page.url = SITE + '/booking/calendar-fare-bonus'
        steps = self.run_pass(page)
        self.assertTrue(steps['passenger'] and steps['contact'])
        self.assertEqual(steps['orderRequests'], {'seen': 1, 'blocked': 1, 'unblocked': 0})
        self.assertEqual(steps['siteRequests'],
                         {'awardAvailability': 1, 'fareInformation': 1, 'inputTravellers': 1})
        self.assertNotIn(ORDER_PATH, ctx.reached_server)
        self.assertEqual((ctx.routes, ctx.listeners, page.nav_listeners), ([], [], []))

    def test_failed_block_is_reported_as_unblocked(self):
        ctx = FakeContext(abort_fails=True)
        page = CaptureFakePage(ctx)
        page.url = SITE + '/booking/calendar-fare-bonus'
        steps = self.run_pass(page)
        self.assertEqual(steps['orderRequests']['unblocked'], 1)
        self.assertIn(ORDER_PATH, ctx.reached_server)

    def test_error_is_returned_with_counts_and_guard_released(self):
        ctx = FakeContext()
        page = CaptureFakePage(ctx, goto_error=TimeoutError('goto'))
        page.url = SITE + '/'                  # 달력이 아니므로 goto 부터 한다
        steps = self.run_pass(page)
        self.assertEqual(steps['error'], 'TimeoutError')
        self.assertFalse(site_drive.capture_complete(steps))
        self.assertEqual((ctx.routes, ctx.listeners, page.nav_listeners), ([], [], []))

    def test_unblocked_order_before_an_error_is_still_counted(self):
        # 검토 165a57e4 P1: 미차단 주문 요청 뒤 예외가 나도 계수가 호출자에게 간다.
        ctx = FakeContext(abort_fails=True)
        page = CaptureFakePage(ctx)
        page.url = SITE + '/booking/calendar-fare-bonus'
        real_click = page._click

        def click_then_fail(x, y):
            was_contact = page._last_id == 'submit-contact'
            real_click(x, y)
            if was_contact:
                raise TimeoutError('after contact')
        page.mouse.click = click_then_fail
        steps = self.run_pass(page)
        self.assertEqual(steps['error'], 'TimeoutError')
        self.assertEqual(steps['orderRequests']['unblocked'], 1)

    def test_complete_pass_is_recognised(self):
        page = CaptureFakePage(FakeContext())
        page.url = SITE + '/booking/calendar-fare-bonus'
        self.assertTrue(site_drive.capture_complete(self.run_pass(page)))

    def test_undrawn_calendar_stops_without_clicking(self):
        page = CaptureFakePage(FakeContext(), cells=0)
        page.url = SITE + '/booking/calendar-fare-bonus'
        page.wait_for_timeout = lambda ms: None
        steps = self.run_pass(page)
        self.assertFalse(steps['calendar'])
        self.assertNotIn('date', steps)
        self.assertEqual((page.clicked, steps['orderRequests']['seen']), ([], 0))


class ReturnToCalendarTests(unittest.TestCase):
    def back(self, page):
        return site_drive.return_to_calendar(page, log=lambda m: None, settle_ms=0, clock=_clock())

    def test_returns_cell_count_and_counts_after_navigation(self):
        ctx = FakeContext()
        page = CaptureFakePage(ctx, cells=35)
        out = self.back(page)
        self.assertEqual(out['cells'], 35)
        self.assertEqual(out['orderRequests'], {'seen': 0, 'blocked': 0, 'unblocked': 0})
        self.assertEqual(page.gotos, [site_drive.CALENDAR])
        self.assertEqual((ctx.routes, ctx.listeners), ([], []))

    def test_order_request_while_returning_is_blocked(self):
        ctx = FakeContext()
        page = CaptureFakePage(ctx, on_gate=[ORDER_REQ])
        out = self.back(page)
        self.assertEqual(out['orderRequests'], {'seen': 1, 'blocked': 1, 'unblocked': 0})
        self.assertNotIn(ORDER_PATH, ctx.reached_server)

    def test_navigation_error_is_returned(self):
        ctx = FakeContext()
        out = self.back(CaptureFakePage(ctx, goto_error=TimeoutError('goto')))
        self.assertEqual((out['error'], out['cells']), ('TimeoutError', 0))
        self.assertEqual((ctx.routes, ctx.listeners), ([], []))


class OnCalendarTests(unittest.TestCase):
    def test_requires_calendar_path_and_cells(self):
        page = CaptureFakePage(FakeContext(), cells=35)
        page.url = SITE + '/booking/calendar-fare-bonus'
        self.assertTrue(site_drive.on_calendar(page))
        page.cells = 0
        self.assertFalse(site_drive.on_calendar(page))
        page.cells, page.url = 35, SITE + '/login'
        self.assertFalse(site_drive.on_calendar(page))


if __name__ == '__main__':
    unittest.main()
