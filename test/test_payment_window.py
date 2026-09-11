"""Verify payment-window acceptance locally; no external requests or approval clicks."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
from payment_window import inspect_payment_window, inspect_new_payment_windows, payment_provider
from manual_booking import CONFIGURE
import json
from playwright.sync_api import sync_playwright


class PaymentWindowTests(unittest.TestCase):
    def test_direction_policy_and_configuration(self):
        self.assertEqual(payment_provider('ICN','CDG'),'npay')
        self.assertEqual(payment_provider('CDG','ICN'),'hyundai')
        self.assertEqual(payment_provider('FCO','ICN'),'hyundai')
        with self.assertRaises(ValueError):payment_provider('CDG','FCO')
        steps=json.loads((Path(__file__).resolve().parents[1]/'ke_award/steps.json').read_text(encoding='utf-8'))['steps']
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True);page=browser.new_page()
            for provider in ('npay','hyundai','npay'):
                page.evaluate('''steps=>{
                    window.KE_REC={state:{},pause(){},loadBaked(){this.state.steps=steps},reset(){},save(){}};
                    window.KE_HUD={state:{},save(){},render(){}};
                }''',steps)
                result=page.evaluate(CONFIGURE,{'target':'2027-09-05','cabin':'프레스티지',
                    'openAt':'2026-09-10 09:00:00','paymentProvider':provider})
                actual=page.evaluate('KE_REC.state.steps')
                self.assertEqual(result['paymentProvider'],provider)
                self.assertFalse(result['armed']);self.assertEqual(result['leadMs'],2500)
                self.assertEqual(len(actual),17)
                self.assertEqual(actual[14]['text'],'Npay' if provider=='npay' else '한국발행 신용/체크카드')
                self.assertEqual(actual[14]['alt'],[])
                self.assertEqual(actual[15]['ensure'],'현대카드')
                self.assertEqual(actual[15]['onlyIfPrev'],'한국발행')
            browser.close()

    def test_real_checkout_required_and_approval_untouched(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            cases = [
                ('대한항공 결제 376,000원 <button onclick="window.approved=true">결제 승인</button>', True),
                ('네이버페이 홈페이지 결제 <button>상품 보기</button>', False),
                ('대한항공 결제 376,000원 오류가 발생했습니다 <button>닫기</button>', False),
                ('대한항공 결제 376,000원 <button hidden>결제 승인</button>', False),
                ('대한항공 결제 376,000원 네이버 로그인 <button>로그인</button>', False),
                ('대한항공 결제 376,000원 <button>닫기</button>', False),
            ]
            for body, expected in cases:
                page.route('https://pay.naver.com/**', lambda route, request, body=body: route.fulfill(
                    content_type='text/html; charset=utf-8', body='<meta charset="utf-8"><body>'+body))
                page.goto('https://pay.naver.com/fixture-checkout')
                self.assertEqual(inspect_payment_window(page)['ready'], expected)
                self.assertFalse(page.evaluate('!!window.approved'))
                page.unroute('https://pay.naver.com/**')
            page.goto('about:blank')
            self.assertFalse(inspect_payment_window(page)['ready'])
            for host, expected in [('m.pay.naver.com', True), ('m.pay.naver.com.example.test', False)]:
                url=f'https://{host}/instantPay/nfPayment/fixture'
                page.route(url,lambda route:route.fulfill(content_type='text/html; charset=utf-8',
                    body='<meta charset="utf-8"><body>대한항공 결제 349,500원 <button onclick="window.approved=true">동의하고 결제하기</button>'))
                page.goto(url)
                self.assertEqual(inspect_payment_window(page)['ready'],expected)
                self.assertFalse(page.evaluate('!!window.approved'))
            for host, expected in [('ansimclick.hyundaicard.com', False),
                                   ('ansimclick.hyundaicard.com.example.test', False)]:
                url = f'https://{host}/xacs3/fixture.jsp'
                page.route(url, lambda route: route.fulfill(content_type='text/html; charset=utf-8',
                    body='<meta charset="utf-8"><body><button onclick="window.approved=true">앱카드 결제</button><button>PIN번호 결제</button>'))
                page.goto(url)
                self.assertEqual(inspect_payment_window(page)['ready'], expected)
                if host=='ansimclick.hyundaicard.com':
                    result=inspect_payment_window(page)
                    self.assertTrue(result['providerWindowObserved'])
                    self.assertEqual(result['stage'],'card-authentication-method-selection')
                self.assertFalse(page.evaluate('!!window.approved'))
            browser.close()

    def test_only_new_npay_succeeds_and_other_tabs_do_not_hide_diagnostics(self):
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            context=browser.new_context()
            def page_at(url,body):
                page=context.new_page()
                page.route(url,lambda route:route.fulfill(content_type='text/html; charset=utf-8',body=body))
                page.goto(url);return page
            old=page_at('https://pay.naver.com/old','대한항공 100원 <button>결제하기</button>')
            card=page_at('https://ansimclick.hyundaicard.com/xacs3/test','<button>앱카드 결제</button><button>PIN번호 결제</button>')
            irrelevant=page_at('https://example.test/notice','안내')
            result=inspect_new_payment_windows([old,card,irrelevant],{old})
            self.assertFalse(result['ready']);self.assertTrue(result['providerWindowObserved'])
            self.assertTrue(inspect_new_payment_windows([old,card,irrelevant],{old},'hyundai')['ready'])
            self.assertFalse(inspect_new_payment_windows([old,card],{old,card},'hyundai')['ready'])
            login=page_at('https://m.pay.naver.com/login-test','네이버 로그인 <button>로그인</button>')
            result=inspect_new_payment_windows([old,card,login,irrelevant],{old})
            self.assertEqual(result['stage'],'login-required')
            new=page_at('https://m.pay.naver.com/new','대한항공 100원 <button onclick="window.approved=true">동의하고 결제하기</button>')
            result=inspect_new_payment_windows([old,new,card,irrelevant],{old})
            self.assertTrue(result['ready']);self.assertEqual(result['stage'],'npay-checkout')
            self.assertFalse(inspect_payment_window(new,'hyundai')['ready'])
            self.assertFalse(inspect_payment_window(card,'undefined')['ready'])
            card.evaluate("document.body.insertAdjacentHTML('beforeend','오류가 발생했습니다')")
            self.assertFalse(inspect_payment_window(card,'hyundai')['ready'])
            self.assertFalse(card.evaluate('!!window.approved'))
            self.assertFalse(new.evaluate('!!window.approved'))
            browser.close()


if __name__ == '__main__':
    unittest.main()
