"""Visible, persistent names for Astra-owned Chrome profiles."""
import argparse
import json
from playwright.sync_api import sync_playwright, expect


def mark_context(context, port):
    if port not in (9232, 9233):
        raise ValueError('Only Astra ports are permitted')
    name = f'ASTRA · {port}'
    script = """(() => {
      if (window.top !== window || !/^https?:$/.test(location.protocol)) return;
      if (window.__astraIdentityInstalled) return;
      window.__astraIdentityInstalled = true;
      const name = NAME;
      const apply = () => {
        if (!document.body) return;
        if (!document.getElementById('astra-browser-identity')) {
          const badge = document.createElement('div');
          badge.id = 'astra-browser-identity';
          badge.textContent = name;
          badge.style.cssText = 'position:fixed;bottom:4px;left:4px;z-index:2147483647;pointer-events:none;background:#312e81;color:white;padding:3px 8px;border-radius:5px;font:12px sans-serif;';
          badge.setAttribute('aria-hidden', 'true');
          document.body.appendChild(badge);
        }
        if (!document.title.startsWith('[' + name + '] '))
          document.title = '[' + name + '] ' + document.title;
      };
      const install = () => {
        apply();
        if (document.head) new MutationObserver(apply).observe(document.head, {childList:true, subtree:true, characterData:true});
      };
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, {once:true});
      else install();
    })();""".replace('NAME', json.dumps(name))
    context.add_init_script(script)
    for page in context.pages:
        page.evaluate(script)
    return name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, required=True, choices=[9232, 9233])
    args = parser.parse_args()
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


if __name__ == '__main__':
    main()
