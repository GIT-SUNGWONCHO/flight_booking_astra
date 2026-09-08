"""Real local HTTP + browser: fresh prefetch feeds the UI, old/changed requests do not."""
import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
from hybrid import CalendarPrefetch, CALENDAR_PATH
from playwright.sync_api import sync_playwright


class Handler(BaseHTTPRequestHandler):
    opened = 0
    calls = []
    def log_message(self, *args): pass
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<!doctype html><body><div id='seats'>not loaded</div></body>")
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.calls.append((self.path, body.decode()))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"seats":1 if time.time() >= self.opened else 0}).encode())


class HybridTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.origin = f"http://127.0.0.1:{cls.server.server_port}"
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown(); cls.server.server_close()
    def setUp(self):
        Handler.calls = []
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.page.goto(self.origin)
        self.page.evaluate("""(url) => {window.KE_PROBE={hits:()=>[{url,
          req:{method:'POST',body:'{"date":"20270904"}',headers:{'content-type':'application/json'}}}]};}""", self.origin+CALENDAR_PATH)
    def tearDown(self): self.context.close()
    def ui_fetch(self, body='{"date":"20270904"}'):
        return self.page.evaluate("""async ({path, body})=>{
          const r=await fetch(path,{method:'POST',headers:{'content-type':'application/json'},body});
          const data=await r.json(); document.querySelector('#seats').textContent=String(data.seats); return data;
        }""", {"path":CALENDAR_PATH,"body":body})
    def test_opens_after_schedule_and_ui_receives_fresh_response(self):
        Handler.opened=time.time()+0.4
        bridge=CalendarPrefetch(self.page, Handler.opened*1000)
        self.assertTrue(bridge.prepare())
        self.assertEqual(Handler.calls, [])
        bridge.helper.wait_for_function("window.__astraResult !== null")
        self.assertEqual(self.ui_fetch()["seats"], 1)
        self.assertEqual(self.page.locator('#seats').inner_text(), '1')
        self.assertTrue(bridge.report["used"])
        self.assertEqual(len(Handler.calls), 1)
        # One-shot: the next UI request must use the server again.
        self.ui_fetch()
        self.assertEqual(len(Handler.calls), 2)
        bridge.close()
    def test_changed_date_uses_normal_request(self):
        Handler.opened=time.time()-1
        bridge=CalendarPrefetch(self.page, time.time()*1000)
        bridge.prepare(); bridge.helper.wait_for_function("window.__astraResult !== null")
        self.ui_fetch('{"date":"20270905"}')
        self.assertFalse(bridge.report["used"])
        self.assertEqual(len(Handler.calls), 2)
        bridge.close()
    def test_stale_prefetch_is_not_used(self):
        Handler.opened=time.time()-1
        bridge=CalendarPrefetch(self.page, time.time()*1000, ttl_ms=1)
        bridge.prepare(); bridge.helper.wait_for_function("window.__astraResult !== null")
        self.page.wait_for_timeout(10)
        self.ui_fetch()
        self.assertFalse(bridge.report["used"])
        self.assertEqual(len(Handler.calls), 2)
        bridge.close()

if __name__ == '__main__': unittest.main()
