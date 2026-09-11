"""Regression tests for real operational failures, without a live account."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dev"))
import runtime
import ke_setup
import daily
from network_trace import order_id_observed
from hybrid import request_matches


class RuntimeTests(unittest.TestCase):
    def test_readiness_failure_only_stops_its_own_worker(self):
        for failed_name in ('watch', 'macro'):
            with self.subTest(failed=failed_name), tempfile.TemporaryDirectory() as tmp:
                folder = Path(tmp)
                class Worker:
                    def __init__(self, pid): self.pid, self.calls, self.stopped = pid, 0, False
                    def poll(self):
                        self.calls += 1
                        return 1 if self.stopped else (0 if self.calls > 12 else None)
                    def wait(self, timeout=None): return 1 if self.stopped else 0
                workers = {'watch': Worker(10001), 'macro': Worker(10002)}
                for file in ('watch_seats.json', 'autorun_report.json'):
                    (folder/file).write_text(json.dumps({'runId':'test', 'ok':True}))
                def stop(cmd, **kwargs):
                    for worker in workers.values():
                        if str(worker.pid) == cmd[-1]: worker.stopped = True
                    return subprocess.CompletedProcess(cmd, 0)
                def check(processes, *args):
                    return [failed_name + ': READY 없음'] if failed_name in processes else []
                with patch.dict(os.environ, {'KE_RUN_ID':'test'}), \
                     patch.object(sys, 'argv', ['daily','--at','+30s','--date','09-01']), \
                     patch.object(daily, 'output_dir', return_value=folder), \
                     patch.object(daily, 'measure_clock', return_value={'ok':True}), \
                     patch.object(daily, 'checkpoint', side_effect=check), \
                     patch.object(daily.subprocess, 'Popen', side_effect=list(workers.values())), \
                     patch.object(daily.subprocess, 'run', side_effect=stop), \
                     patch.object(daily.time, 'sleep'):
                    self.assertEqual(daily.main(), 1)
                other = 'macro' if failed_name == 'watch' else 'watch'
                self.assertTrue(workers[failed_name].stopped)
                self.assertFalse(workers[other].stopped)
                report = json.loads((folder/'daily_report.json').read_text())
                self.assertTrue(report['workers'][other]['ok'])
                self.assertFalse(report['workers'][failed_name]['ok'])

    def test_observer_launch_failure_does_not_prevent_booking_launch(self):
        class Finished:
            pid = 10003
            def poll(self): return 0
            def wait(self, timeout=None): return 0
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder/'autorun_report.json').write_text(json.dumps({'runId':'test','ok':True}))
            with patch.dict(os.environ, {'KE_RUN_ID':'test'}), \
                 patch.object(sys, 'argv', ['daily','--at','+30s','--date','09-01']), \
                 patch.object(daily, 'output_dir', return_value=folder), \
                 patch.object(daily, 'measure_clock', return_value={'ok':True}), \
                 patch.object(daily.subprocess, 'Popen', side_effect=[OSError('fixture'), Finished()]) as launch:
                self.assertEqual(daily.main(), 1)
                self.assertEqual(launch.call_count, 2)
            report = json.loads((folder/'daily_report.json').read_text())
            self.assertTrue(report['workers']['macro']['ok'])
            self.assertFalse(report['workers']['watch']['ok'])

    def test_http_success_or_echo_does_not_prove_an_order(self):
        self.assertFalse(order_id_observed({}))
        self.assertFalse(order_id_observed({'error': {'request': {'orderId': 'old'}}}))
        self.assertFalse(order_id_observed({'orderId': 'old', 'errorCode': 'failed'}))
        self.assertTrue(order_id_observed({'orderId': 'fixture-order'}))

    def test_worker_failure_overrides_success_json(self):
        class Failed:
            returncode = 1
            pid = 98765
            def poll(self): return 1
            def wait(self, timeout=None): return 1
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'autorun_report.json').write_text(json.dumps({'runId': 'test', 'ok': True}))
            with patch.dict(os.environ, {'KE_RUN_ID': 'test'}), \
                 patch.object(sys, 'argv', ['daily', '--at', '+60s', '--no-watch', '--date', '09-01']), \
                 patch.object(daily, 'output_dir', return_value=folder), \
                 patch.object(daily, 'measure_clock', return_value={'ok': True}), \
                 patch.object(daily.subprocess, 'Popen', return_value=Failed()):
                self.assertEqual(daily.main(), 1)
            self.assertFalse(json.loads((folder / 'daily_report.json').read_text())['ok'])

    def test_past_fire_is_not_silently_tomorrow(self):
        now = datetime(2026, 9, 8, 9, 1, tzinfo=runtime.KST)
        with self.assertRaises(ValueError):
            runtime.resolve_time("09:00", now)
        self.assertEqual(runtime.resolve_time("2026-09-09T09:00:00+09:00", now).day, 9)

    def test_expired_setup_does_not_start_even_min_tries(self):
        with patch.object(ke_setup.subprocess, "run") as run:
            result = ke_setup.run_setup(["ignored"], datetime.now(runtime.KST)-timedelta(seconds=1), min_tries=2)
            self.assertFalse(result["ok"])
            run.assert_not_called()

    def test_setup_timeout_uses_remaining_budget(self):
        with patch.object(ke_setup.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, '{"ok":true}', 'warning')) as run:
            result = ke_setup.run_setup(["ignored"], datetime.now(runtime.KST)+timedelta(seconds=2))
            self.assertTrue(result["ok"])
            self.assertLessEqual(run.call_args.kwargs["timeout"], 2)

    def test_setup_timeout_preserves_partial_output(self):
        with tempfile.TemporaryDirectory() as tmp, \
             patch.object(ke_setup, 'LOGDIR', Path(tmp)), \
             patch.object(ke_setup.subprocess, 'run', side_effect=subprocess.TimeoutExpired(
                 ['fixture'], 0.05, output=b'fixture preparation reached calendar', stderr=b'fixture timeout')):
            result = ke_setup.run_setup(['fixture'], datetime.now(runtime.KST)+timedelta(seconds=0.05), log=lambda _:None)
            self.assertFalse(result['ok'])
            saved = (Path(tmp)/'setup_failures.log').read_text(encoding='utf-8')
            self.assertIn('fixture preparation reached calendar', saved)
            self.assertIn('fixture timeout', saved)

    def test_empty_stale_partial_and_slow_are_failures(self):
        watch = {"runId":"test", "ok":True, "samples":2}
        good = {"runId":"test", "ok":True, "dry":True, "total":6, "idx":6,
                "problem":False, "dryReady":True, "seconds":15}
        self.assertEqual(runtime.validate_rehearsal(watch, good, "test", 30, mode='dry'), [])
        self.assertTrue(runtime.validate_rehearsal(watch, good, 'test'))
        for bad in ({}, {**good,"runId":"yesterday"}, {**good,"idx":2},
                    {**good,"problem":True}, {**good,"seconds":76}, {**good,"dryReady":False}):
            self.assertTrue(runtime.validate_rehearsal(watch, bad, "test", 30, mode='dry'))
        self.assertTrue(runtime.validate_rehearsal({**watch,"ok":False}, good, "test", 30, mode='dry'))

    def test_rehearsal_requires_real_payment_window(self):
        good = {'runId':'test', 'ok':True, 'problem':False, 'seconds':45,
                'dry':False, 'paymentWindowTest':True, 'total':17, 'idx':17,
                'payWindowReady':True, 'paymentApprovalClicked':False}
        self.assertEqual(runtime.validate_rehearsal({}, good, 'test', require_watch=False), [])
        for change in ({'payWindowReady':False}, {'idx':6}, {'paymentApprovalClicked':True},
                       {'paymentWindowTest':False}, {'seconds':120}, {'runId':'old'}):
            self.assertTrue(runtime.validate_rehearsal({}, {**good, **change}, 'test', require_watch=False))

    def test_ready_url_cannot_hide_dead_worker(self):
        class Dead:
            returncode = 2
            def poll(self): return 2
        self.assertTrue(daily.checkpoint({"macro":Dead()}, "test", Path("unused"), "09-04", "time"))

    def test_ready_accepts_windows_child_pid_but_rejects_foreign_worker(self):
        class Live:
            pid = 1
            worker_token = 'worker-a'
            def poll(self): return None
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            state = {'runId':'test', 'pid':2, 'workerToken':'worker-a', 'state':'ready',
                     'at':datetime.now(runtime.KST).isoformat(), 'date':'09-04', 'fireAt':'time'}
            runtime.atomic_json(folder/'macro_status.json', state)
            self.assertEqual(daily.checkpoint({'macro':Live()}, 'test', folder, '09-04', 'time'), [])
            state['workerToken'] = 'foreign-worker'
            runtime.atomic_json(folder/'macro_status.json', state)
            self.assertTrue(daily.checkpoint({'macro':Live()}, 'test', folder, '09-04', 'time'))

    def test_atomic_result_is_valid_json(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"result.json"
            runtime.atomic_json(path, {"runId":"a", "ok":False})
            runtime.atomic_json(path, {"runId":"b", "ok":True})
            self.assertEqual(json.loads(path.read_text())["runId"], "b")
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_atomic_save_retries_temporary_windows_file_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'status.json'
            runtime.atomic_json(path, {'state':'old'})
            replace = os.replace
            attempts = []
            def intermittent(source, destination):
                attempts.append(source)
                if len(attempts) < 3: raise PermissionError('fixture sharing violation')
                replace(source, destination)
            with patch.object(runtime.os, 'replace', side_effect=intermittent):
                runtime.atomic_json(path, {'state':'ready'})
            self.assertEqual(json.loads(path.read_text())['state'], 'ready')
            self.assertEqual(len(attempts), 3)
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_hybrid_never_matches_changed_ticket_or_date(self):
        source = {"url":"https://example.test/calendar", "req":{"method":"POST", "body":'{"date":"20270904"}', "headers":{"X-Ticket":"a"}}}
        self.assertTrue(request_matches(source, source["url"], "POST", source["req"]["body"], {"x-ticket":"a"}))
        self.assertFalse(request_matches(source, source["url"], "POST", source["req"]["body"], {"x-ticket":"b"}))
        self.assertFalse(request_matches(source, source["url"], "POST", '{"date":"20270903"}', {"x-ticket":"a"}))
        self.assertFalse(request_matches(source, source["url"]+"/order", "POST", source["req"]["body"], {"x-ticket":"a"}))

if __name__ == "__main__":
    unittest.main()
