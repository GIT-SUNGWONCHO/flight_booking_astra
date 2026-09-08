"""Real recorder waits for a newly enabled date without reloading or stale seats."""
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'dev'))
from departure_live import fire_departure_live, refresh_departure
from test_skipcal import serve, HOST, DEP, STEPS, FX
from playwright.sync_api import sync_playwright


class DepartureLiveTest(unittest.TestCase):
    def test_open_transition_and_same_date_refusal(self):
        with sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            ctx=browser.new_context()
            ctx.add_init_script((ROOT/'userscript/ke-award-macro.user.js').read_text(encoding='utf-8'))
            ctx.route(HOST+'/**',serve)
            html=(FX/'select-award-flight/departure.html').read_text(encoding='utf-8')
            html=html.replace('location.href = u.toString();', "day='202708'+dd; document.querySelector('#depdate').textContent='08월 '+dd+'일'; ask();")
            ctx.route(DEP+'?*', lambda route: route.fulfill(content_type='text/html; charset=utf-8',body=html))
            page=ctx.new_page()
            page.goto(DEP+'?depDate=20270820&openTo=21')
            page.wait_for_function('window.KE_PROBE?.shownDate() === "08-20"')
            self.assertTrue(refresh_departure(page,'08-21')['ok'])
            self.assertFalse(refresh_departure(page,'08-21')['ok'])
            page.evaluate('() => {KE_REC.state.steps='+STEPS+'; KE_REC.state.expectDate="08-22"; KE_REC.state.cabin="프레스티지"; KE_REC.save();}')
            self.assertFalse(fire_departure_live(page,'08-21')['ok'])
            self.assertTrue(fire_departure_live(page,'08-22')['ok'])
            page.wait_for_timeout(300)
            self.assertNotIn('next',page.evaluate('window.__clicks'))
            page.evaluate('''() => {const b=[...document.querySelectorAll('#flexible-date button')].find(b=>b.textContent.includes('출발일 22 '));b.classList.add('-active'); b.textContent=b.textContent.replace('운항편 없음','선택 가능');}''')
            page.wait_for_function('(window.__clicks||[]).includes("next")',timeout=10000)
            self.assertEqual(page.evaluate('window.__loads'),1)
            self.assertEqual(page.evaluate('KE_PROBE.shownDate()'),'08-22')
            page.evaluate('''() => {
              KE_REC.pause('cross-month test');
              document.querySelector('#depdate').textContent='08월 30일';
              document.querySelector('#flexible-date').className='flexible-date__items';
              const dates=['08-27','08-28','08-29','08-30','08-31','09-01','09-02'];
              KE_PROBE.latestStripDates=()=>dates;
              document.querySelector('#flexible-date').innerHTML=dates.map(d=>'<li class="flexible-date__item"><button class="-active">출발일 '+d.slice(3)+' 선택 가능</button></li>').join('');
            }''')
            self.assertTrue(page.evaluate('!!KE_UTIL.findStripDate("09-01").el'))
            self.assertFalse(page.evaluate('!!KE_UTIL.findStripDate("10-01").el'))
            page.evaluate('document.querySelector("#flexible-date li").remove()')
            self.assertFalse(page.evaluate('!!KE_UTIL.findStripDate("09-01").el'))
            browser.close()

if __name__=='__main__': unittest.main()
