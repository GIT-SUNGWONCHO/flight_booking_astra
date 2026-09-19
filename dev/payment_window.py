"""Read-only verification of the newly opened payment window. Never click in it."""
from urllib.parse import urlsplit


def payment_provider(origin, destination):
    """사용자가 확정한 방향만 선택한다. 새 노선의 제공자는 추측하지 않는다."""
    if origin == 'ICN' and destination != 'ICN':
        return 'npay'
    if destination == 'ICN' and origin != 'ICN':
        return 'hyundai'
    raise ValueError('결제 제공자 미확정 노선')


def inspect_payment_window(page, expected='npay'):
    if expected not in ('npay', 'hyundai'):
        return {'ready': False, 'reason': 'payment-policy-undefined'}
    url = urlsplit(page.url)
    if url.scheme != 'https' or not url.hostname:
        return {'ready': False, 'reason': 'payment-provider-not-loaded'}
    try:
        # 9/8 관측 /xacs3/, 9/20 리허설 관측 /web/WEB100.do. 화면 판정(앱카드/PIN 선택 보임)이 본 조건이다.
        if url.hostname == 'ansimclick.hyundaicard.com' and url.path.startswith(('/xacs3/', '/web/')):
            # Observed live issuer entry screen: authentication choices precede
            # merchant/amount display. Do not click either authentication method.
            observed = (page.evaluate("document.readyState === 'complete'")
                     and page.get_by_text('앱카드 결제', exact=True).is_visible()
                     and page.get_by_text('PIN번호 결제', exact=True).is_visible())
            blocked = page.evaluate(r"""() => /오류가 발생|유효하지 않은|결제를 진행할 수 없|로그인/.test(document.body?.innerText || '')""")
            ready = bool(observed and not blocked and expected == 'hyundai')
            return {'ready': ready, 'providerWindowObserved': observed, 'provider': url.hostname,
                    'stage': 'card-authentication-method-selection', 'amountVerified': False,
                    'errorPage': blocked, 'expectedProvider': expected,
                    'reason': 'target-payment-window-reached' if ready else 'target-payment-window-not-reached'}
        if url.hostname not in ('pay.naver.com', 'm.pay.naver.com'):
            return {'ready': False, 'reason': 'unrecognized-payment-provider'}
        result = page.evaluate(r"""() => {
          const text = document.body ? document.body.innerText : '';
          const login = /네이버 로그인|아이디 찾기|비밀번호 찾기/.test(text);
          const error = /오류가 발생|유효하지 않은|결제를 진행할 수 없/.test(text);
          const merchant = /대한항공|KOREAN\s*AIR/i.test(text);
          const amount = /[0-9][0-9,]*\s*원|KRW\s*[0-9]/.test(text);
          const controls = [...document.querySelectorAll('button,input,[role=button]')]
            .some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
              && /결제하기|결제\s*승인|동의하고\s*결제/.test(e.innerText || e.value || e.getAttribute('aria-label') || ''));
          return {ready: document.readyState === 'complete' && !login && !error
              && merchant && amount && /결제/.test(text) && controls,
            merchantVisible: merchant, amountVisible: amount, paymentControlVisible: controls,
            loginRequired: login, errorPage: error};
        }""")
        stage = ('npay-checkout' if result['ready'] else 'login-required' if result['loginRequired']
                 else 'payment-error' if result['errorPage'] else 'npay-incomplete')
        return {**result, 'ready': result['ready'] and expected == 'npay',
                'provider': url.hostname, 'stage': stage, 'expectedProvider': expected}
    except Exception:
        return {'ready': False, 'reason': 'payment-window-loading'}


def inspect_new_payment_windows(pages, original_pages, expected='npay'):
    """이번 실행의 새 창만 읽는다. 뒤의 무관한 팝업이 앞선 진단을 덮지 않는다."""
    best = {'ready': False, 'reason': 'no-new-payment-window'}
    def rank(value):
        if value.get('ready'):return 100
        if value.get('loginRequired'):return 80
        if value.get('errorPage'):return 70
        if value.get('providerWindowObserved'):return 60
        if value.get('provider'):return 40
        return 0
    for page in pages:
        if page in original_pages or page.is_closed():continue
        value = inspect_payment_window(page, expected)
        if rank(value)>rank(best):best=value
    return best
