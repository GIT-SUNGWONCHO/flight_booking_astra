"""실사이트 연결 없이 격리 브라우저의 응답 헤더·본문·오류 이벤트 연결 시험."""
import json
import tempfile
import unittest
from pathlib import Path

from collect import Collector
from playwright.sync_api import sync_playwright


class BrowserTest(unittest.TestCase):
    def test_event_lifecycle_and_non_json(self):
        with tempfile.TemporaryDirectory() as directory, sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(service_workers='block')
                def reply(route):
                    path = route.request.url.split('koreanair.com',1)[-1]
                    if path == '/':
                        route.fulfill(status=200,content_type='text/html',body='<title>local fixture</title>')
                    elif path.endswith('/fareInformation'):
                        route.fulfill(status=200,content_type='application/json',body='{"pnrFareInfo":{"currency":"KRW"}}')
                    elif path.endswith('/inputTravellers'):
                        route.fulfill(status=200,content_type='text/html',body='private-fixture-malformed')
                    else:
                        route.abort()
                context.route('**/*',reply)
                c=Collector(Path(directory))
                context.on('request',c.started)
                context.on('response',c.responded)
                context.on('requestfinished',c.finished)
                context.on('requestfailed',c.failed)
                page=context.new_page()
                page.goto('https://www.koreanair.com/')
                page.evaluate('''async () => {
                    for (const path of ['/api/ap/booking/avail/fareInformation',
                        '/api/ap/booking/traveller/inputTravellers','/api/private-member']) {
                        try { await (await fetch(path,{method:'POST',body:'{}'})).text(); } catch {}
                    }
                }''')
                page.wait_for_timeout(100)
                c.save('ended')
                result=json.loads((Path(directory)/'contracts.json').read_text(encoding='utf-8'))
                self.assertEqual(c.summary()['started'],2)
                self.assertEqual(c.summary()['responsesRecorded'],1)
                self.assertEqual(c.summary()['incompleteRequestIds'],[2])
                self.assertIn('responseHeadersCallback',c.ledger[1])
                self.assertIn('parseCompleted',c.ledger[1])
                self.assertFalse(c.summary()['allTrackedRequestsRecorded'])
                self.assertEqual(result['coverage']['outsideAllowlist'][0]['state'],'network-failed')
                self.assertNotIn('private-',json.dumps(result))
            finally:
                browser.close()


if __name__=='__main__':
    unittest.main()
