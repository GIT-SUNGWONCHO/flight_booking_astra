"""아침 준비의 오판·시간 초과·예매/계측 독립성 시험. 실사이트 주문 없음."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
import prepare_day
from runtime import atomic_json
from test_calendar import plan, KST
from worker_health import inspect_worker, wait_worker, process_alive
from check_day import latest_preparation
from stage_process import StageProcess


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.identity = '20260911-082000-1234abcd'
        self.folder = self.root / 'dev-shots/runs' / self.identity
        self.folder.mkdir(parents=True)
        self.expected = plan('2026-09-11')
        self.now = datetime.now(KST)
        self.status = dict(runId=self.identity, pid=os.getpid(), at=self.now.isoformat(),
                           target=self.expected['departureDate'], openAt=self.expected['openAt'], state='ready-unarmed')
        self.report = dict(runId=self.identity, plan=self.expected, target=self.expected['departureDate'],
                           openAt=self.expected['openAt'], prepared=True, rehearsal=False, routeVerified=True,
                           session={'known': True, 'coversOpen': True},
                           configuration={'cabin':'프레스티지', 'leadMs':2500, 'startAt':'calendar'})

    def write(self):
        atomic_json(self.folder / 'manual-booking_status.json', self.status)
        atomic_json(self.folder / 'manual_booking.json', self.report)

    def health(self):
        self.write()
        return inspect_worker(self.folder, 'manual-booking', self.expected, now=self.now)

    def test_ready_and_user_armed(self):
        self.assertTrue(self.health()['ready'])
        self.status['state'] = 'armed-by-user'
        self.assertTrue(self.health()['ready'])

    def test_stale_dead_wrong_run_target_preview_and_unknown_session(self):
        original_status, original_report = copy.deepcopy(self.status), copy.deepcopy(self.report)
        cases = [
            ('status', 'at', (self.now-timedelta(seconds=8)).isoformat()),
            ('status', 'pid', -1), ('status', 'runId', 'old'),
            ('status', 'target', '2027-09-04'), ('report', 'preview', True),
            ('report', 'session', {'known':False}),
            ('report', 'session', {'known':True, 'coversOpen':False}),
            ('report', 'endedAt', self.now.isoformat()),
            ('status', 'state', 'running'),
        ]
        for location, key, value in cases:
            with self.subTest(location=location, key=key, value=value):
                self.status, self.report = copy.deepcopy(original_status), copy.deepcopy(original_report)
                getattr(self, location)[key] = value
                self.assertFalse(self.health()['ready'])

    def test_actual_child_heartbeat_and_exit(self):
        log = self.root / 'child.log'
        stop = self.root / 'stop'
        code = '''import json, os, sys, time
from datetime import datetime, timezone, timedelta
from pathlib import Path
folder=Path(sys.argv[1]);stop=Path(sys.argv[2]);report=json.loads(sys.argv[3]);status=json.loads(sys.argv[4])
status.update(pid=os.getpid(),at=datetime.now(timezone(timedelta(hours=9))).isoformat())
(folder/'manual_booking.json').write_text(json.dumps(report),encoding='utf-8')
(folder/'manual-booking_status.json').write_text(json.dumps(status),encoding='utf-8')
print(report['runId']+' 준비:',flush=True)
while not stop.exists():time.sleep(.05)
'''
        with log.open('w', encoding='utf-8') as output:
            proc = subprocess.Popen([sys.executable, '-u', '-c', code, str(self.folder), str(stop),
                                     json.dumps(self.report), json.dumps(self.status)], stdout=output, stderr=output,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            meta = {'log':str(log)}
            self.assertTrue(wait_worker(meta, proc, 'manual-booking', '준비:', self.expected, self.root, 5), meta)
            self.assertTrue(process_alive(meta['workerPid']))
            stop.touch();proc.wait(timeout=5)
            self.assertFalse(inspect_worker(self.folder, 'manual-booking', self.expected)['ready'])
            self.assertFalse(wait_worker(meta, proc, 'manual-booking', '준비:', self.expected, self.root, 1))
        finally:
            stop.touch()
            proc.wait(timeout=5)

    def test_timeout_keeps_failed_stage_and_caps_deadline(self):
        report = {'plan':self.expected,'stages':[]}
        started = time.monotonic()
        with patch.object(prepare_day, 'heartbeat'):
            with self.assertRaises(subprocess.TimeoutExpired):
                prepare_day.run_stage(report, self.folder, time.monotonic()+.2,
                                      '시간초과시험', [sys.executable,'-c','import time;time.sleep(30)'], 30)
        stored = json.loads((self.folder/'prepare_day.json').read_text(encoding='utf-8'))
        self.assertEqual(stored['stages'][0]['status'], 'failed')
        self.assertEqual(stored['stages'][0]['error'], 'TimeoutExpired')
        self.assertLessEqual(stored['stages'][0]['timeoutSeconds'], .2)
        self.assertLess(time.monotonic()-started, 5)

    def test_preparation_selects_normal_latest_not_preview(self):
        for identity, preview in [('20260911-082000-1234abcd',False), ('20260911-083000-1234abcd',True)]:
            atomic_json(self.root/'dev-shots/runs'/identity/'prepare_day.json', {'plan':self.expected,'preview':preview})
        path, report = latest_preparation('2026-09-11', self.root)
        self.assertEqual(path.parent.name, self.identity)
        self.assertFalse(report['preview'])

    @unittest.skipUnless(os.name=='nt', 'Windows venv/하위 프로세스 정리 시험')
    def test_timeout_stops_only_stage_process_tree(self):
        marker=self.root/'grandchild.pid'
        script='import os,time;from pathlib import Path;Path('+repr(str(marker))+').write_text(str(os.getpid()));time.sleep(30)'
        launcher='import subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(script)+']);time.sleep(30)'
        report={'plan':self.expected,'stages':[]}
        # 별개 대기 프로그램을 함께 두고 정리에 휘말리지 않는지 확인한다.
        unrelated=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            with patch.object(prepare_day,'heartbeat'):
                with self.assertRaises(subprocess.TimeoutExpired):
                    prepare_day.run_stage(report,self.folder,time.monotonic()+1.5,'자식정리시험',[sys.executable,'-c',launcher],30)
            self.assertTrue(marker.exists(),'시간 초과 전에 실제 하위 작업자가 시작돼야 함')
            self.assertFalse(process_alive(int(marker.read_text())))
            self.assertIsNone(unrelated.poll(),'별개 프로세스는 살아 있어야 함')
        finally:
            if unrelated.poll() is None:
                subprocess.run(['taskkill','/PID',str(unrelated.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                unrelated.wait(timeout=5)

    @unittest.skipUnless(os.name=='nt', 'Windows Job 정상 종료 후 자식 유지 시험')
    def test_success_keeps_started_child_alive(self):
        marker=self.root/'retained.pid'
        stop=self.root/'retained.stop'
        child=('import os,time;from pathlib import Path;'
               f'Path({str(marker)!r}).write_text(str(os.getpid()));'
               f'\nwhile not Path({str(stop)!r}).exists():time.sleep(.05)')
        launcher='import subprocess,sys;subprocess.Popen([sys.executable,"-c",'+repr(child)+'])'
        try:
            with StageProcess([sys.executable,'-c',launcher],creationflags=subprocess.CREATE_NO_WINDOW) as stage:
                stage.process.wait(timeout=5)
            deadline=time.monotonic()+5
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.05)
            self.assertTrue(marker.exists())
            self.assertTrue(process_alive(int(marker.read_text())), '정상 Chrome 실행 단계의 자식은 유지해야 함')
        finally:
            stop.touch()

    def exercise_main(self, fail_role):
        class Morning(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026,9,11,8,20,tzinfo=KST)
        page = MagicMock()
        page.url = 'https://www.koreanair.com/select-award-flight/departure'
        page.locator.return_value.inner_text.return_value = 'KRW'
        pw = MagicMock()
        pw.chromium.connect_over_cdp.return_value.contexts = [SimpleNamespace(pages=[page])]
        stages = []
        def stage(report, out, deadline, name, args, timeout):
            stages.append(name);report['activeStage']=name
            if name == ('계측_크롬' if fail_role=='observer' else '예약_크롬'):
                raise RuntimeError('의도한 시험 실패')
            return ''
        with patch.object(prepare_day, 'datetime', Morning), \
             patch.object(prepare_day, 'new_run', return_value=self.identity), \
             patch.object(prepare_day, 'output_dir', return_value=self.folder), \
             patch.object(prepare_day, 'run_stage', side_effect=stage), \
             patch.object(prepare_day, 'prepare_krw', return_value={'verified':True}), \
             patch.object(prepare_day, 'wait_worker', return_value=True), \
             patch.object(prepare_day.subprocess, 'Popen') as popen, \
             patch('playwright.sync_api.sync_playwright') as sp, \
             patch('payment_window.inspect_payment_window', return_value={'ready':False}):
            sp.return_value.__enter__.return_value = pw
            popen.return_value.pid = os.getpid()
            if os.name == 'nt':
                with patch('winsound.MessageBeep'):
                    code = prepare_day.main(SimpleNamespace(day='2026-09-11', preview=False, cold=False))
            else:
                code = prepare_day.main(SimpleNamespace(day='2026-09-11', preview=False, cold=False))
        result = json.loads((self.folder/'prepare_day.json').read_text(encoding='utf-8'))
        popen.return_value.terminate.assert_not_called()
        popen.return_value.kill.assert_not_called()
        return code, result, stages

    def test_observer_failure_does_not_fail_or_stop_booking(self):
        code, result, stages = self.exercise_main('observer')
        self.assertEqual(code, 0)
        self.assertTrue(result['booking']['ready'])
        self.assertFalse(result['observer']['ready'])
        self.assertEqual(result['observer']['failedStage'], '계측_크롬')

    def test_booking_failure_still_starts_observer(self):
        code, result, stages = self.exercise_main('booking')
        self.assertEqual(code, 2)
        self.assertFalse(result['booking']['ready'])
        self.assertTrue(result['observer']['ready'])
        self.assertIn('계측_달력', stages)


if __name__ == '__main__':
    unittest.main()
