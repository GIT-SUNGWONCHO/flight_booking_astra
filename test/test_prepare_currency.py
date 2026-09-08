"""KRW preflight: apply redraw, wrong target, already ready, bounded failure."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
from prepare_currency import prepare_krw, DEP, CAL
from playwright.sync_api import sync_playwright


class CurrencyPreparationTest(unittest.TestCase):
    def test_currency_redraw_and_deadlines(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            def serve(route):
                if CAL in route.request.url:
                    html = '''<button id="day">09-01</button><button onclick="location.href='%s'">검색</button>
                    <script>window.KE_UTIL={findOpenDate:(p,d)=>d==='09-01'?document.querySelector('#day'):null,fireClick:e=>e.click()};</script>''' % DEP
                else:
                    html = '''<button id="currencyBtn" onclick="document.querySelector('#filter-currency').hidden=false">통화 EUR</button>
                    <div id="filter-currency" hidden><label onclick="window.chosen=true">KRW</label><button class="filter__apply" onclick="if(window.chosen){sessionStorage.krw='1';location.href='%s'}">적용</button></div>
                    <button id="submit-contact" onclick="throw Error('must not create an order')">주문</button>
                    <script>if(sessionStorage.krw)document.querySelector('#currencyBtn').innerText='통화 KRW';</script>''' % CAL
                route.fulfill(content_type='text/html; charset=utf-8', body=html)
            page.route('https://ke.test/**', serve)
            page.goto('https://ke.test' + DEP)
            result = prepare_krw(page, '09-01', 5000)
            self.assertEqual(result, {'changed': True, 'verified': True, 'calendarReturns': 1})
            self.assertFalse(prepare_krw(page, '09-01', 2000)['changed'])
            page.evaluate('sessionStorage.clear()')
            page.reload()
            with self.assertRaises(Exception):
                prepare_krw(page, '09-02', 700)
            self.assertIn(CAL, page.url)
            browser.close()


if __name__ == '__main__':
    unittest.main()
