"""D4 전송권·발사 시각 시험. 사이트·브라우저 없음.

프로세스 경합 시험은 Windows 기본과 같은 spawn 방식으로 여러 프로세스를 동시에 출발시켜
전송권을 하나만 얻는지 반복 확인한다. 로컬 디스크(Mac APFS)에서만 실행했다.
"""
from datetime import datetime, timedelta, timezone
import json
import multiprocessing
import os
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import permit  # noqa: E402

KST = timezone(timedelta(hours=9))
DAY = '2099-01-01'


def _contend(state_dir, day, go_file, ready_file, result_file, index):
    Path(ready_file).write_text('ready', encoding='utf-8')
    while not os.path.exists(go_file):
        time.sleep(0.001)
    got = permit.acquire(state_dir, day=day, run_id=f'run-{index}', target={'i': index},
                         now_iso='2099-01-01T09:00:00+09:00')
    Path(result_file).write_text('1' if got else '0', encoding='utf-8')


class PermitTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_only_the_first_acquire_succeeds_and_nothing_releases_it(self):
        first = permit.acquire(self.dir, day=DAY, run_id='a', target={'flight': '901'}, now_iso='t')
        self.assertIsNotNone(first)
        self.assertIsNone(permit.acquire(self.dir, day=DAY, run_id='b', target={}, now_iso='t'))
        held = permit.existing_permit(self.dir)
        self.assertEqual((held['runId'], held['day'], held['target']), ('a', DAY, {'flight': '901'}))
        self.assertIsNone(permit.acquire(self.dir, day=DAY, run_id='c', target={}, now_iso='t'))

    def test_another_run_day_is_blocked_by_the_same_permit(self):
        # 검토 9aaff071 P1: 자정을 넘겨 실행일이 바뀌어도 남은 전송권이 막는다.
        permit.acquire(self.dir, day=DAY, run_id='a', target={}, now_iso='t')
        self.assertIsNone(permit.acquire(self.dir, day='2099-01-02', run_id='b', target={},
                                         now_iso='t'))

    def test_unreadable_permit_still_blocks(self):
        permit.permit_path(self.dir).write_text('{broken', encoding='utf-8')
        self.assertEqual(permit.existing_permit(self.dir), {'state': 'unreadable'})
        self.assertIsNone(permit.acquire(self.dir, day=DAY, run_id='a', target={}, now_iso='t'))

    def test_permit_holds_no_order_reference_or_account(self):
        permit.acquire(self.dir, day=DAY, run_id='a', target={'date': '2027-09-09'}, now_iso='t')
        body = json.loads(permit.permit_path(self.dir).read_text(encoding='utf-8'))
        self.assertEqual(set(body), {'day', 'runId', 'pid', 'at', 'target', 'meaning'})

    def test_permit_and_durable_writes_sync_the_directory(self):
        # 검토 9aaff071 P2: 파일뿐 아니라 부모 디렉터리 항목도 동기화한다(POSIX).
        calls = []
        real = os.fsync
        with unittest.mock.patch.object(permit.os, 'fsync', lambda fd: (calls.append(fd), real(fd))):
            permit.acquire(self.dir, day=DAY, run_id='a', target={}, now_iso='t')
            self.assertEqual(len(calls), 1 if os.name == 'nt' else 2)
            calls.clear()
            permit.durable_json(self.dir / 'intent.json', {'state': 'sending'})
            self.assertEqual(len(calls), 1 if os.name == 'nt' else 2)
        self.assertEqual(json.loads((self.dir / 'intent.json').read_text(encoding='utf-8')),
                         {'state': 'sending'})
        permit.durable_json(self.dir / 'intent.json', {'state': 'ordered'})
        self.assertEqual(json.loads((self.dir / 'intent.json').read_text(encoding='utf-8'))['state'],
                         'ordered')
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.endswith('.tmp')], [])

    def test_concurrent_processes_get_exactly_one_permit(self):
        ctx = multiprocessing.get_context('spawn')
        for round_no in range(4):
            state = self.dir / f'state-{round_no}'     # 회차마다 새 상태 폴더(전송권은 폴더에 하나)
            state.mkdir()
            go = self.dir / f'go-{round_no}'
            procs, results = [], []
            for i in range(6):
                ready = self.dir / f'ready-{round_no}-{i}'
                result = self.dir / f'result-{round_no}-{i}'
                results.append(result)
                # 실행일도 섞는다. 날짜가 달라도 하나만 얻어야 한다.
                day = f'2099-02-{i % 2 + 1:02d}'
                proc = ctx.Process(target=_contend, args=(str(state), day, str(go), str(ready),
                                                          str(result), i))
                proc.start()
                procs.append((proc, ready))
            deadline = time.monotonic() + 60
            while not all(r.exists() for _, r in procs):
                self.assertLess(time.monotonic(), deadline, 'processes did not start')
                time.sleep(0.01)
            go.write_text('go', encoding='utf-8')
            for proc, _ in procs:
                proc.join(30)
                self.assertEqual(proc.exitcode, 0)
            wins = [r.read_text(encoding='utf-8') for r in results].count('1')
            self.assertEqual(wins, 1, f'round {round_no}')


class FireTimeTests(unittest.TestCase):
    def test_parse_at_uses_the_run_day_not_today(self):
        at = permit.parse_at('2099-01-02', '09:00:00', KST)
        self.assertEqual(at, datetime(2099, 1, 2, 9, 0, 0, tzinfo=KST))
        for day, value in (('2099-1-2', '09:00:00'), ('2099-01-02', '9:00'), ('2099-01-02', ''),
                           ('2099-02-30', '09:00:00'), ('2099-01-02', '24:00:00')):
            with self.assertRaises(ValueError):
                permit.parse_at(day, value, KST)

    def test_start_check(self):
        fire = datetime(2099, 1, 1, 9, tzinfo=KST)
        now = datetime(2099, 1, 1, 8, 30, tzinfo=KST)
        self.assertIsNone(permit.start_check(day=DAY, now=now, fire_at=fire))
        self.assertIsNone(permit.start_check(day=DAY, now=now))
        self.assertEqual(permit.start_check(day=DAY, now=fire, fire_at=fire), 'fire-time-passed')
        # 전날 밤에 다음 날 --day 로 시작하는 것도 막는다(실행일은 오늘이어야 한다).
        self.assertEqual(permit.start_check(day='2099-01-02', now=now), 'day-is-not-today')
        self.assertEqual(permit.start_check(day=DAY, now=datetime(2099, 1, 1, 8)), 'naive-now')

    def test_fire_check(self):
        fire = datetime(2099, 1, 1, 9, tzinfo=KST)
        self.assertEqual(permit.fire_check(day=DAY, now=fire - timedelta(milliseconds=1),
                                           fire_at=fire), 'before-fire-time')
        self.assertIsNone(permit.fire_check(day=DAY, now=fire + timedelta(seconds=0.2), fire_at=fire))
        self.assertIsNone(permit.fire_check(day=DAY, now=fire + timedelta(seconds=3), fire_at=fire))
        self.assertEqual(permit.fire_check(day=DAY, now=fire + timedelta(seconds=3.1), fire_at=fire),
                         'too-late')
        self.assertEqual(permit.fire_check(day=DAY, now=fire + timedelta(days=1), fire_at=fire),
                         'day-is-not-today')
        self.assertIsNone(permit.fire_check(day=DAY, now=fire))


if __name__ == '__main__':
    unittest.main()
