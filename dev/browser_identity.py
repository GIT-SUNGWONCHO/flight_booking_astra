"""Visible, persistent names for Astra-owned Chrome profiles."""
import argparse
import json
import os
import hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright, expect, Error


ROLES = {9232: '예매', 9233: '계측', 9242: '예매2', 9243: '예매3'}
# 9242 = 본인 계정 두 번째 예매(2026-09-22), 9243 = 와이프 계정 두 번째 예매(2026-09-23)


def mark_context(context, port):
    if port not in ROLES:
        raise ValueError('Only Astra ports are permitted')
    name = f'ASTRA · {port}'
    role = ROLES[port]
    script = """(() => {
      if (window.top !== window || !/^https?:$/.test(location.protocol)) return;
      if (window.__astraIdentityObserver) window.__astraIdentityObserver.disconnect();
      window.__astraIdentityInstalled = true;
      const name = NAME;
      const role = ROLE;
      const apply = () => {
        if (!document.body) return;
        if (!document.getElementById('astra-browser-identity')) {
          const badge = document.createElement('div');
          badge.id = 'astra-browser-identity';
          badge.style.cssText = 'position:fixed;bottom:4px;left:4px;z-index:2147483647;pointer-events:none;background:#312e81;color:white;padding:3px 8px;border-radius:5px;font:12px sans-serif;';
          badge.setAttribute('aria-hidden', 'true');
          document.body.appendChild(badge);
        }
        const badge = document.getElementById('astra-browser-identity');
        badge.textContent = name + ' · ' + role;
        badge.style.background = role === '계측' ? '#065f46' : '#312e81';
        const prefix = '[' + name + '] ' + role + ' | ';
        if (!document.title.startsWith(prefix))
          document.title = prefix + document.title.replace(/\\[ASTRA · \\d+(?: · (?:예매|계측))?\\]\\s*/g,'').replace(/^(?:예매|계측) \\| /,'');
      };
      const install = () => {
        apply();
        if (document.head) {
          window.__astraIdentityObserver = new MutationObserver(apply);
          window.__astraIdentityObserver.observe(document.head, {childList:true, subtree:true, characterData:true});
        }
      };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once:true});
      else install();
    })();""".replace('NAME', json.dumps(name)).replace('ROLE', json.dumps(role))
    context.add_init_script(script)
    for page in context.pages:
        try:
            page.evaluate(script)
        except Error:
            # 이동 중인 탭은 다음 문서의 init script가 표시한다.
            # 이름표 표시 실패가 예약/계측 준비를 중단하면 안 된다.
            continue
    return name + ' · ' + role


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True, choices=sorted(ROLES))
    parser.add_argument('--keep', action='store_true', help='Chrome이 열려 있는 동안 새로고침·탭 이동에도 이름표 유지')
    args = parser.parse_args()
    keeper_handle=None
    if args.keep and os.name=='nt':
        import ctypes
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        key=hashlib.sha256(str(Path(__file__).resolve().parent.parent).encode()).hexdigest()[:12]
        keeper_handle=kernel.CreateMutexW(None,False,f'Local\\AstraIdentity_{key}_{args.port}')
        if not keeper_handle:raise RuntimeError('Identity mutex failed')
        if ctypes.get_last_error()==183:
            kernel.CloseHandle(keeper_handle);return
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{args.port}')
        context = browser.contexts[0]
        name = mark_context(context, args.port)
        page = context.new_page()
        try:
            page.goto('chrome://settings/manageProfile')
            field = page.locator('input[type=text]')
            field.fill(name)
            field.press('Tab')
            page.reload()
            expect(field).to_have_value(name, timeout=10000)
            print(f'Profile name saved: {name}')
        finally:
            page.close()
        if args.keep:
            print(f'Identity keeper ready: {args.port}',flush=True)
            try:
                while browser.is_connected() and context.pages:
                    context.pages[0].wait_for_timeout(1000)
            except Error:pass
            finally:
                if keeper_handle:kernel.CloseHandle(keeper_handle)


if __name__ == '__main__':
    main()
