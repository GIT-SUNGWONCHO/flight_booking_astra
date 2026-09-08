"""Reproduce a lost cabin selection / delayed fare total before Next."""
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class FareSelectionTests(unittest.TestCase):
    def test_next_waits_for_checked_radio_and_priced_total(self):
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context()
            context.add_init_script((ROOT/'userscript/ke-award-macro.user.js').read_text(encoding='utf-8'))
            page = context.new_page()
            for update_total in (True, False):
                body = '''<meta charset="utf-8"><body>
                  <section>대한항공 운항
                    <input type="radio" id="fare" name="fare">
                    <label for="fare" style="display:block;padding:20px">일반석 35,000 마일</label>
                  </section>
                  <div id="payment-widget"><span id="total">0 마일 + 0 원</span>
                    <button id="next" onclick="window.nextClicked=true">다음</button>
                  </div><script>
                    document.querySelector('#fare').addEventListener('change', () => {
                      if (UPDATE) setTimeout(() => document.querySelector('#total').textContent='35,000 마일 + 376,500 원', 600);
                    });
                  </script>'''.replace('UPDATE', 'true' if update_total else 'false')
                page.route('https://ke.fixture/**', lambda route, request, body=body: route.fulfill(
                    content_type='text/html; charset=utf-8', body=body))
                page.goto('https://ke.fixture/booking/select-award-flight/departure')
                page.evaluate('''() => {
                  const R=KE_REC;
                  R.state.steps=[{sel:'#next',text:'다음',url:'/booking/select-award-flight/departure',requireSelectedCabin:true}];
                  Object.assign(R.state,{idx:0,cabin:'일반석',problem:false,stepTimeoutMs:2000,retryClickMs:100,settleMs:0,maxSettleMs:0});
                  R.play();
                }''')
                page.wait_for_timeout(300)
                self.assertFalse(page.evaluate('!!window.nextClicked'))
                page.wait_for_function('!KE_REC.state.playing', timeout=10000)
                self.assertEqual(page.evaluate('!!window.nextClicked'), update_total)
                self.assertEqual(page.evaluate('KE_REC.state.problem'), not update_total)
                page.unroute('https://ke.fixture/**')
            browser.close()


if __name__ == '__main__':
    unittest.main()
