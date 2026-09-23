"""소유권을 확인한 전용 Chrome을 정상 종료하여 프로필과 소켓을 정리한다."""
import argparse
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--port', type=int, required=True, choices=[9232, 9233, 9242])
args = parser.parse_args()
with sync_playwright() as pw:
    browser = pw.chromium.connect_over_cdp(f'http://127.0.0.1:{args.port}', timeout=5000)
    browser.new_browser_cdp_session().send('Browser.close')
