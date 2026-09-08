"""Verify payment-window acceptance locally; no external requests or approval clicks."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
from payment_window import inspect_payment_window
from playwright.sync_api import sync_playwright


class PaymentWindowTests(unittest.TestCase):
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
            for host, expected in [('ansimclick.hyundaicard.com', True),
                                   ('ansimclick.hyundaicard.com.example.test', False)]:
                url = f'https://{host}/xacs3/fixture.jsp'
                page.route(url, lambda route: route.fulfill(content_type='text/html; charset=utf-8',
                    body='<meta charset="utf-8"><body><button onclick="window.approved=true">앱카드 결제</button><button>PIN번호 결제</button>'))
                page.goto(url)
                self.assertEqual(inspect_payment_window(page)['ready'], expected)
                self.assertFalse(page.evaluate('!!window.approved'))
            browser.close()


if __name__ == '__main__':
    unittest.main()
