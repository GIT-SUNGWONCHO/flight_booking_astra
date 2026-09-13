"""D5 후반 연결을 격리 headless Chromium 에서 시험한다. 실사이트 연결 없음.

모든 요청은 route 가 로컬 픽스처로 채우고 DNS 도 브라우저 안에서 전부 실패시킨다.
게이트·Npay 픽스처의 요소 id 는 기존 17단계(ke_award/steps.json)와 9/13 기록의 id 를 쓰고,
동의 모달 동작은 test/fixture/twoagree.html(사용자 확인 2026-08-25)을 따른다.
실제 사이트 DOM·실제 Npay 화면과 같다는 증거가 아니다. 결제창 판정은 운영
dev/payment_window.py 를 그대로 쓴다.
"""
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'dev'))
from playwright.sync_api import sync_playwright  # noqa: E402

import site_drive  # noqa: E402

SITE = 'https://www.koreanair.com'
NPAY = 'https://pay.naver.com/fixture/checkout'


def gate_html(cfg):
    agree1 = 'true' if cfg.get('agree1_on') else 'false'
    npay_disabled = 'disabled' if cfg.get('npay_disabled') else ''
    marker3 = '' if cfg.get('agree_no_marker') else 'aria-pressed="false"'
    return """<title>gate</title><meta charset="utf-8">
<style>body{font:14px sans-serif;margin:0;padding:70px 10px}button,label{display:inline-block;margin:4px}
#m{position:fixed;inset:0;background:rgba(0,0,0,.5);display:none;align-items:center;justify-content:center}
#m.on{display:flex}.box{background:#fff;padding:20px}#warn{display:none}#warn.on{display:block}</style>
<div>09월 07일 (화)</div><div>35,000 마일</div><div>325,500 원</div>
<button id="btn-resv-agree-1" aria-pressed="%s">동의</button>
<button id="btn-resv-agree-3" %s>동의</button>
<button id="btnAwardUseMileageApply">적용</button>
<input type="radio" name="pay" id="rad-card"><label for="rad-card">카드</label>
<input type="radio" name="pay" id="rad-naverpay" %s><label for="rad-naverpay">Npay</label>
<button id="btn-payment">결제하기</button>
<div id="warn">항공권의 운임 규정 및 운송 약관 보기의 내용을 확인 후 체크해 주세요</div>
<div id="m"><div class="box"><button id="btnScrollDown">아래로 스크롤</button>
<button id="btnConfirm" style="display:none">확인</button></div></div>
<script>
fetch('/api/et/ibeSupport/baggagePolicies?orderId=%s&x=1').catch(()=>{});
let cur=null, n=0; window.__off=false; window.__mileage=0;
function openModal(id){cur=id;n=0;btnScrollDown.style.display='';btnConfirm.style.display='none';m.className='on';}
for (const id of ['btn-resv-agree-1','btn-resv-agree-3']) document.getElementById(id).onclick=function(){
  if (this.getAttribute('aria-pressed')==='true'){this.setAttribute('aria-pressed','false');window.__off=true;return;}
  openModal(id);};
btnScrollDown.onclick=function(){n++; if(n>=2){this.style.display='none';btnConfirm.style.display='';}};
btnConfirm.onclick=function(){m.className='';document.getElementById(cur).setAttribute('aria-pressed','true');};
btnAwardUseMileageApply.onclick=function(){window.__mileage++;
  fetch('/api/et/bonusDeduct/bonusBookingDeductMileage',{method:'POST',body:'{}'}).catch(()=>{});};
document.getElementById('btn-payment').onclick=function(){
  const on=['btn-resv-agree-1','btn-resv-agree-3'].every(i=>document.getElementById(i).getAttribute('aria-pressed')==='true');
  if(!on){warn.className='on';return;}
  localStorage.setItem('paid','1');
  if(document.getElementById('rad-naverpay').checked){
    const w=%s;
    fetch('/api/pp/payment/NaverPay',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({payment:{reservationRecLoc:'%s',amount:'325500'}})})
      .then(()=>{ if (w) w.location.href='%s';});
  } else { window.open('https://dpay.bluewalnut.co.kr/fixture'); }
};
</script>""" % (agree1, marker3, npay_disabled, cfg.get('gate_ref', 'NEWREF'),
               'null' if cfg.get('no_popup') else "window.open('about:blank')",
               cfg.get('npay_ref', 'NEWREF'), NPAY)


def npay_html(cfg):
    if cfg.get('npay_login'):
        return '<meta charset="utf-8"><title>로그인</title>네이버 로그인 아이디 찾기 비밀번호 찾기'
    return ('<meta charset="utf-8"><title>Npay</title><h1>네이버페이 결제</h1><div>대한항공</div>'
            '<div>%s원</div><button onclick="window.__clicked=(window.__clicked||0)+1">결제하기</button>'
            % cfg.get('npay_amount', '325,500'))


class PaymentBackHalfBrowserTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True,
                                             args=['--host-resolver-rules=MAP * ~NOTFOUND'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def run_pass(self, **cfg):
        context = self.browser.new_context(service_workers='block', viewport={'width': 1000, 'height': 700})
        self.addCleanup(context.close)
        self.reached = []

        def fixture(route):
            parts = urlsplit(route.request.url)
            self.reached.append((parts.netloc, parts.path, route.request.method))
            html = 'text/html; charset=utf-8'
            if parts.netloc == 'pay.naver.com':
                route.fulfill(status=200, content_type=html, body=npay_html(cfg))
            elif parts.netloc == 'dpay.bluewalnut.co.kr':
                route.fulfill(status=200, content_type=html, body='<meta charset="utf-8">카드 결제')
            elif parts.path.startswith('/payment/gate'):
                route.fulfill(status=200, content_type=html, body=gate_html(cfg))
            elif parts.path == '/api/et/bonusDeduct/bonusBookingDeductMileage':
                route.fulfill(status=200, content_type='application/json',
                              body='{"code": "E1"}' if cfg.get('deduct_error') else '{"ok": true}')
            elif parts.path.startswith('/api/'):
                route.fulfill(status=200, content_type='application/json', body='{}')
            elif parts.path == '/other-tab':
                route.fulfill(status=200, content_type=html,
                              body="<script>localStorage.removeItem('paid');const t=setInterval(()=>{"
                                   "if(localStorage.getItem('paid')){clearInterval(t);window.open('%s');}},100)"
                                   "</script>" % NPAY)
            else:
                route.fulfill(status=200, content_type=html, body='<title>start</title>')

        context.route('**/*', fixture)
        if cfg.get('other_tab'):
            # 같은 컨텍스트의 다른 탭이 결제하기 뒤에 Npay 창을 연다(인계 대상이 연 창이 아님).
            context.new_page().goto(SITE + '/other-tab')
        page = context.new_page()
        page.goto(SITE + '/booking/calendar-fare-bonus')
        self.logs = []
        out = site_drive.payment_pass(page, flight='901', date='2027-09-07', mileage='35,000',
                                      reference='NEWREF', ordered_at=0.0, log=self.logs.append,
                                      wait_ms=800, destination='CDG', amount=325500, pace=0.05,
                                      window_timeout_ms=4000)
        return out, page, context

    def popup(self, context, page):
        return [p for p in context.pages if p.opener() is page]

    def test_back_half_reaches_the_npay_window_for_this_order(self):
        out, page, context = self.run_pass()
        self.assertTrue(out['completed'], (out.get('stage'), self.logs))
        self.assertEqual(out['paymentWindow']['stage'], 'npay-checkout')
        self.assertTrue(out['npayReferenceMatched'])
        self.assertEqual(page.evaluate("() => [document.getElementById('btn-resv-agree-1').getAttribute('aria-pressed'),"
                                       " document.getElementById('btn-resv-agree-3').getAttribute('aria-pressed'), window.__mileage]"),
                         ['true', 'true', 1])
        popups = self.popup(context, page)
        self.assertEqual(len(popups), 1)
        self.assertIsNone(popups[0].evaluate('() => window.__clicked'))   # 제공자 창 안에서는 누르지 않는다
        self.assertEqual([r for r in self.reached if r[0] == 'dpay.bluewalnut.co.kr'], [])

    def test_already_checked_agreement_stays_on(self):
        out, page, _ = self.run_pass(agree1_on=True)
        self.assertTrue(out['completed'], out.get('stage'))
        self.assertFalse(page.evaluate('() => window.__off'))
        self.assertEqual(out['steps']['btn-resv-agree-1']['result'], 'already-on')

    def test_login_window_is_not_complete(self):
        out, _, _ = self.run_pass(npay_login=True)
        self.assertFalse(out['completed'])
        self.assertEqual(out['stage'], 'provider-window:login-required')

    def test_amount_mismatch_is_not_complete(self):
        out, _, _ = self.run_pass(npay_amount='300,000')
        self.assertEqual((out['completed'], out['stage']), (False, 'provider-amount-mismatch'))
        # 검토 2ee60c2b P1: 부분 문자열 325,500 이 들어 있는 1,325,500원도 통과하지 않는다.
        out, _, _ = self.run_pass(npay_amount='1,325,500')
        self.assertEqual((out['completed'], out['stage']), (False, 'provider-amount-mismatch'))

    def test_window_opened_by_another_tab_is_not_complete(self):
        # 검토 2ee60c2b P1: 대상 게이트가 창을 못 열고 다른 탭이 Npay 창을 열었다.
        out, _, _ = self.run_pass(no_popup=True, other_tab=True)
        self.assertFalse(out['completed'])
        self.assertEqual(out['stage'], 'provider-window:no-new-payment-window')
        self.assertGreaterEqual(out['paymentWindow']['otherTabWindows'], 1)

    def test_agreement_without_state_marker_is_not_clicked(self):
        out, page, _ = self.run_pass(agree_no_marker=True)
        self.assertEqual(out['stage'], 'agree-failed:btn-resv-agree-3')
        self.assertIsNone(page.evaluate("() => document.getElementById('btn-resv-agree-3').getAttribute('aria-pressed')"))

    def test_mileage_deduct_error_stops_before_payment(self):
        out, page, context = self.run_pass(deduct_error=True)
        self.assertEqual(out['stage'], 'mileage-deduct-business-error')
        self.assertEqual(self.popup(context, page), [])

    def test_session_for_another_order_is_refused(self):
        out, _, _ = self.run_pass(npay_ref='OLDREF')
        self.assertEqual((out['completed'], out['stage']), (False, 'order-changed-after-payment'))

    def test_npay_not_selectable_never_opens_a_window(self):
        out, page, context = self.run_pass(npay_disabled=True)
        self.assertEqual(out['stage'], 'npay-not-selected')
        self.assertEqual(self.popup(context, page), [])

    def test_previous_order_gate_is_never_clicked(self):
        out, page, _ = self.run_pass(gate_ref='OLDREF')
        self.assertEqual(out['stage'], 'gate')
        self.assertEqual(page.evaluate("() => document.getElementById('btn-resv-agree-1').getAttribute('aria-pressed')"),
                         'false')


if __name__ == '__main__':
    unittest.main()
