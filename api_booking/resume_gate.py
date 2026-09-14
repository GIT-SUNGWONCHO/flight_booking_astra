"""이미 모델이 적용된 결제 게이트를 허용 저장 증거와 대조하여 이어간다. 새 주문 없음."""
import argparse
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import time
import sys
import order_evidence
import site_drive
from bridge_guard import BridgeGuard
import permit
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'dev'))

def validate_model(model, saved):
    try:
        target = saved['target']
        fare = model['pnrFareInfo']
        bounds = model['boundList']
        if not (len(bounds) == 1 and len(bounds[0]['segmentList']) == 1):
            return False
        leg = bounds[0]['segmentList'][0]
        if not (saved['pnr'] and model['pnr'] == saved['pnr']):
            return False
        if not (leg['departureAirport'], leg['arrivalAirport'], leg['flightNumber'], leg['operationCarrierCode'], leg['fareFamily'], leg['departureDateTime'][:8], leg['status']) == (target['origin'], target['destination'], target['flight'], target['carrier'], target['family'], target['date'].replace('-', ''), 'HK'):
            return False
        if not fare['currency'] == saved['currency'] == 'KRW':
            return False
        if not Decimal(fare['totalAmount']) == Decimal(saved['totalAmount']) > 0:
            return False
        if not Decimal(fare['mileage']) == Decimal(saved['mileage']) > 0:
            return False
        ids = [x['travellerId'] for x in model['travellerFareInfoList']]
        if not (ids and all((type(x) is str and x for x in ids))):
            return False
        if not hashlib.sha256(json.dumps(sorted(ids)).encode()).hexdigest() == saved['passengerFingerprint']:
            return False
        return True
    except (KeyError, TypeError, ValueError, AssertionError, ArithmeticError):
        return False

def read_context(page):
    return page.evaluate("()=>({model:JSON.parse(sessionStorage.inputTravellers).model,\n      member:sessionStorage.getItem('loggedInUserInfo'),status:JSON.parse(sessionStorage.resvStatus)})")

class StoredOrderWatch(BridgeGuard):
    """이미 열린 게이트는 저장 모델과 수신 증거를 직접 대조. 네트워크 관측을 꾸미지 않는다."""
    def __init__(self,context,*,page,saved,member,existing_document=True):
        super().__init__(context,page=page)
        self.saved=saved;self.member=member
        self.existing_document=existing_document
    def start(self):
        super().start()
        # 이미 검증한 현재 게이트 문서를 첫 관측 세대로 사용. 과거 요청은 수집하지 않는다.
        if self.existing_document:self._documents=1
    def judge(self,reference,ordered_at):
        verdict=super().judge(reference,ordered_at)
        if verdict.state!='reference-unobserved' and not verdict.same_reference:
            return verdict
        if verdict.state=='reference-unobserved' and not self.existing_document:
            return verdict
        try:
            current=read_context(self._page)
            if (self._page.url==site_drive.GATE and reference==self.saved['pnr'] and current['member']==self.member
                and current['status'].get('isLoginExpired') is False
                and current['status'].get('isSessionExpired') is False
                and validate_model(current['model'],self.saved)):
                return verdict if verdict.same_reference else replace(verdict,state='same-order-stored-model',same_reference=True)
        except Exception:pass
        return replace(verdict,state='stored-order-context-mismatch',same_reference=False)

def applied_mileage(page,expected):
    observed=page.evaluate('''expected=>{
      const panel=document.getElementById('award-use-mileage');
      const heading=document.getElementById('expander-award-use-mileage');
      if(!panel||!heading)return false;
      const text=heading.textContent.replace(/\\s+/g,' ').trim();
      const cancel=[...panel.querySelectorAll('button')].some(b=>b.textContent.trim()==='적용취소');
      const values=[...text.matchAll(/([0-9][0-9,]*)\\s*마일/g)].map(m=>m[1].replace(/,/g,''));
      return panel.classList.contains('-completed')&&cancel&&values.length===1&&values[0]===expected.replace(/,/g,'');
    }''',expected)
    return {'clicked':False,'verified':observed is True,'result':'ui-applied-matching-mileage' if observed else 'ui-mileage-unverified'}

def ensure_mileage(page,expected,timeout_ms=8000):
    """기존 적용은 재클릭하지 않고, 미적용일 때 정상 적용 버튼을 한 번 누른다."""
    before=applied_mileage(page,expected)
    if before['verified']:return before
    button=page.locator('#btnAwardUseMileageApply')
    if button.count()!=1 or not button.is_visible() or not button.is_enabled():
        return {'clicked':False,'verified':False,'result':'apply-button-unavailable'}
    try:
        button.click(timeout=3000)
    except Exception:
        return {'clicked':False,'verified':False,'result':'apply-click-unconfirmed'}
    deadline=time.monotonic()+timeout_ms/1000
    while True:
        result=applied_mileage(page,expected)
        result['clicked']=True
        if result['verified'] or time.monotonic()>=deadline:return result
        page.wait_for_timeout(100)

def run(page, saved, log=print):
    if page.url != site_drive.GATE:
        return {'completed': False, 'stage': 'not-existing-gate'}
    before = read_context(page)
    if before['member'] in (None, 'null', '{}') or before['status'].get('isLoginExpired') is not False or before['status'].get('isSessionExpired') is not False or (not validate_model(before['model'], saved)):
        return {'completed': False, 'stage': 'stored-order-mismatch'}
    age = time.time() - datetime.fromisoformat(saved['orderResponseReceivedAt']).timestamp()
    if age < 0:
        return {'completed': False, 'stage': 'invalid-receipt-time'}
    ordered_at = time.monotonic() - age
    guard = StoredOrderWatch(page.context,page=page,saved=saved,member=before['member'])
    guard.start()
    try:
        verdict = guard.inspect_gate(reference=saved['pnr'], ordered_at=ordered_at, expected_url=site_drive.GATE, timeout_ms=12000)
        log('같은 주문 게이트 재검증: ' + verdict['stage'])
        after = read_context(page)
        if not verdict['matched'] or after['member'] != before['member'] or after['status'].get('isLoginExpired') is not False or (after['status'].get('isSessionExpired') is not False) or (not validate_model(after['model'], saved)):
            return {'completed': False, 'stage': 'gate-revalidation-failed'}
        return site_drive.payment_pass(page, date=saved['target']['date'], flight=saved['target']['flight'], mileage=f"{int(Decimal(saved['mileage'])):,}", reference=saved['pnr'], ordered_at=ordered_at, origin=saved['target']['origin'], destination=saved['target']['destination'], amount=Decimal(saved['totalAmount']), navigate=False, existing_watch=guard, log=log,mileage_verifier=ensure_mileage)
    finally:
        guard.stop()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-id', required=True)
    ap.add_argument('--port', type=int, default=9232)
    a = ap.parse_args()
    saved = order_evidence.load_received(ROOT / 'dev-shots/state', a.run_id)
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp(f'http://127.0.0.1:{a.port}')
        pages = [x for x in b.contexts[0].pages if x.url == site_drive.GATE]
        if len(pages) != 1:
            print('결제 게이트가 정확히 하나가 아님')
            return 2
        try:
            result = run(pages[0], saved)
        except Exception:
            result={'completed':False,'stage':'resume-exception'}
        safe = {'runId': a.run_id, 'completed': result.get('completed') is True, 'stage': result.get('stage'),
                'npayReferenceMatched':result.get('npayReferenceMatched'),
                'paymentWindow':result.get('paymentWindow')}
        permit.durable_json(ROOT / 'dev-shots/state/order-evidence' / a.run_id / 'gate-resume-result.json', safe)
        print(json.dumps(safe, ensure_ascii=False))
        if not safe['completed']:
            try:input('결과 보존 중. 종료하려면 Enter: ')
            except EOFError:pass
        return 0 if safe['completed'] else 2
if __name__ == '__main__':
    raise SystemExit(main())
