"""Read-only verification of the newly opened payment window. Never click in it."""
from urllib.parse import urlsplit


def inspect_payment_window(page):
    url = urlsplit(page.url)
    if url.scheme != 'https' or not url.hostname:
        return {'ready': False, 'reason': 'payment-provider-not-loaded'}
    try:
        if url.hostname == 'ansimclick.hyundaicard.com' and url.path.startswith('/xacs3/'):
            # Observed live issuer entry screen: authentication choices precede
            # merchant/amount display. Do not click either authentication method.
            ready = (page.evaluate("document.readyState === 'complete'")
                     and page.get_by_text('앱카드 결제', exact=True).is_visible()
                     and page.get_by_text('PIN번호 결제', exact=True).is_visible())
            return {'ready': ready, 'provider': url.hostname,
                    'stage': 'card-authentication-method-selection', 'amountVerified': False}
        if url.hostname != 'pay.naver.com':
            return {'ready': False, 'reason': 'unrecognized-payment-provider'}
        result = page.evaluate(r"""() => {
          const text = document.body ? document.body.innerText : '';
          const login = /네이버 로그인|아이디 찾기|비밀번호 찾기/.test(text);
          const error = /오류가 발생|유효하지 않은|결제를 진행할 수 없/.test(text);
          const merchant = /대한항공|KOREAN\s*AIR/i.test(text);
          const amount = /[0-9][0-9,]*\s*원|KRW\s*[0-9]/.test(text);
          const controls = [...document.querySelectorAll('button,input,[role=button]')]
            .some(e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
          return {ready: document.readyState === 'complete' && !login && !error
              && merchant && amount && /결제/.test(text) && controls,
            merchantVisible: merchant, amountVisible: amount,
            loginRequired: login, errorPage: error};
        }""")
        return {**result, 'provider': url.hostname}
    except Exception:
        return {'ready': False, 'reason': 'payment-window-loading'}
