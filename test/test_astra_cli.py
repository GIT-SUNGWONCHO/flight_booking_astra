"""astra 명령의 계획·판정 시험. 브라우저·사이트·스케줄러 없음.

본보기 설정(config/run.example.json)은 2026-09-25 에 두 계정이 좌석을 잡은 구성이다.
그 설정이 그날 실제로 쓴 네 실행과 같은 인자로 풀리는지, 그 인자를 체인(api_day)이 받아
주문 프로그램(live_order)에 그대로 넘기는지를 끝에서 끝까지 본다.
"""
import json
import shutil
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'dev'))
sys.path.insert(0, str(ROOT / 'api_booking'))
import astra  # noqa: E402
import astra_config as ac  # noqa: E402
import api_day  # noqa: E402

CFG = ac.load(ac.EXAMPLE)
_REAL_ARGS = ac.api_day_args
RUN_DAY = '2026-09-25'


def value(args, flag):
    return args[args.index(flag) + 1] if flag in args else None


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.runs = {r['name']: r for r in ac.plan_runs(CFG, RUN_DAY)}

    def test_example_is_the_four_runs_of_0925(self):
        got = {name: (r['port'], r['family'], r['at'], r['preFireMs'], r['mileage'], r['observer'])
               for name, r in self.runs.items()}
        self.assertEqual(got, {
            'wife-first': (9232, 'KEBONUSPR', '09:00:00', 800, 100000, True),      # 9/25 A
            'me-first': (9242, 'KEBONUSPR', '09:00:00', 800, 65000, False),        # 9/25 B
            'wife-fallback': (9243, 'KEBONUSEY', '09:00:04', 500, 100000, False),  # 9/25 C
            'me-fallback': (9244, 'KEBONUSEY', '09:00:04', 500, 65000, False),     # 9/25 D
        })
        self.assertEqual(ac.open_day(CFG['trip']['date']), RUN_DAY)

    def test_fallback_waits_for_its_own_accounts_first_run_only(self):
        for who in ('wife', 'me'):
            first, fallback = self.runs[f'{who}-first'], self.runs[f'{who}-fallback']
            self.assertTrue(first['writes'].endswith(f'{RUN_DAY}-{who}.json'))
            self.assertEqual((fallback['waitsFor'], fallback['writes'], first['waitsFor']), (first['writes'], '', ''))
        self.assertNotEqual(self.runs['wife-first']['writes'], self.runs['me-first']['writes'])

    def test_every_run_has_its_own_state_folder(self):
        folders = [r['stateDir'] for r in self.runs.values()]
        self.assertEqual(len(set(folders)), 4)
        self.assertNotIn('dev-shots/state', folders)          # 예전 기본 폴더와 섞지 않는다

    def test_account_without_fallback_writes_no_signal(self):
        cfg = json.loads(json.dumps(CFG))
        cfg['accounts'] = [dict(cfg['accounts'][0], fallback=None)]
        cfg['observer'] = None
        (run,) = ac.plan_runs(cfg, RUN_DAY)
        self.assertEqual((run['name'], run['writes'], run['observer']), ('wife-first', '', False))
        self.assertNotIn('--order-outcome-file', ac.api_day_args(run, cfg))
        self.assertNotIn('--observer', ac.api_day_args(run, cfg))

    def test_fire_time_splits_into_whole_seconds_and_a_lead(self):
        self.assertEqual(ac.fire_time('09:00:00', 3500), ('09:00:04', 500))
        self.assertEqual(ac.fire_time('09:00:00', 4000), ('09:00:04', 0))
        self.assertEqual(ac.fire_time('09:00:00', 1), ('09:00:01', 999))
        self.assertEqual(ac.fire_time('09:00:00', 0, 800), ('09:00:00', 800))
        self.assertEqual(ac.fire_time('08:59:59', 1500), ('09:00:01', 500))


class EndToEndArgumentTests(unittest.TestCase):
    """설정 → api_day → live_order. 실제 프로세스·브라우저는 띄우지 않는다."""

    def order_args(self, name, again=False):
        run = next(r for r in ac.plan_runs(CFG, RUN_DAY) if r['name'] == name)
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        seen = {}

        def fake_call(args, **kw):
            seen['order'] = list(args)
            return 0

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 9, 25, 8, 20, 0, tzinfo=tz)
        with mock.patch.object(sys, 'argv', ['api_day.py', *ac.api_day_args(run, CFG, again=again)]), \
                mock.patch.object(api_day, 'OUT', tmp), \
                mock.patch.object(api_day, 'datetime', Clock), \
                mock.patch.object(api_day, 'keep_awake'), \
                mock.patch.object(api_day, 'lingering_orders', return_value=[]), \
                mock.patch.object(api_day, 'browser', return_value=True), \
                mock.patch.object(api_day, 'prepare', return_value=True), \
                mock.patch.object(api_day.capture_date, 'read_cells',
                                  return_value=[{'text': '15 09월 15일 (수) 일반석', 'disabled': False}]), \
                mock.patch.object(api_day, 'log', lambda m: None), \
                mock.patch.object(api_day.subprocess, 'Popen') as popen, \
                mock.patch.object(api_day.subprocess, 'call', fake_call), \
                mock.patch.object(api_day.time, 'sleep'):
            popen.return_value.pid = 1
            code = api_day.main()
        seen['observer'] = popen.call_count
        return code, seen

    def test_first_choice_run_reaches_the_order_program_like_0925(self):
        code, seen = self.order_args('wife-first')
        args = seen['order']
        self.assertEqual(code, 0)
        self.assertEqual((value(args, '--port'), value(args, '--date'), value(args, '--flight'), value(args, '--family')),
                         ('9232', '2027-09-20', '932', 'KEBONUSPR'))
        self.assertEqual((value(args, '--at'), value(args, '--pre-fire-ms'), value(args, '--health-at')),
                         ('09:00:00', '800', '08:50:00'))
        self.assertEqual((value(args, '--capture-iso'), value(args, '--capture-date')), ('2027-09-15', '09월 15일'))
        self.assertEqual(value(args, '--own-mileage'), '100000')
        self.assertTrue(value(args, '--order-outcome-file').endswith('2026-09-25-wife.json'))
        self.assertTrue(Path(value(args, '--state-dir')).as_posix().endswith('dev-shots/state-9232'))
        self.assertNotIn('--order-gate-file', args)
        self.assertEqual(seen['observer'], 1)                 # 계측 체인은 첫 실행만 띄운다

    def test_fallback_run_carries_the_gate_and_the_later_time(self):
        code, seen = self.order_args('me-fallback')
        args = seen['order']
        self.assertEqual(code, 0)
        self.assertEqual((value(args, '--port'), value(args, '--family'), value(args, '--at'), value(args, '--pre-fire-ms')),
                         ('9244', 'KEBONUSEY', '09:00:04', '500'))
        self.assertTrue(value(args, '--order-gate-file').endswith('2026-09-25-me.json'))
        self.assertEqual(float(value(args, '--order-gate-timeout')), 5.0)
        self.assertNotIn('--order-outcome-file', args)
        self.assertEqual(seen['observer'], 0)

    def test_all_four_runs_are_accepted_by_the_chain(self):
        for name in ('wife-first', 'wife-fallback', 'me-first', 'me-fallback'):
            with self.subTest(name):
                code, seen = self.order_args(name)
                self.assertEqual(code, 0)
                self.assertNotIn('--again', seen['order'])

    def test_again_is_passed_down_only_when_asked(self):
        _, seen = self.order_args('wife-first', again=True)
        self.assertIn('--again', seen['order'])

    def test_manual_payment_travels_from_the_config_to_the_order_program(self):
        cfg = dict(CFG, payment='manual')
        for run in ac.plan_runs(cfg, RUN_DAY):
            self.assertEqual(value(ac.api_day_args(run, cfg), '--payment'), 'manual')
        self.assertEqual(value(ac.rehearsal_args(cfg, cfg['accounts'][0], '2027-09-13'), '--payment'), 'manual')
        with mock.patch.object(ac, 'api_day_args', lambda run, _cfg, **kw: _REAL_ARGS(run, cfg, **kw)):
            _, seen = self.order_args('wife-first')
        self.assertEqual(value(seen['order'], '--payment'), 'manual')
        # 기본(auto)은 인자를 붙이지 않는다 - 9/25 의 명령과 같게.
        self.assertNotIn('--payment', ac.api_day_args(ac.plan_runs(CFG, RUN_DAY)[0], CFG))
        _, seen = self.order_args('wife-first')
        self.assertNotIn('--payment', seen['order'])

    def test_rehearsal_arguments_are_economy_on_the_first_seat(self):
        args = ac.rehearsal_args(CFG, CFG['accounts'][1], '2027-09-13')
        self.assertEqual((value(args, '--mode'), value(args, '--port'), value(args, '--family'),
                          value(args, '--target-date'), value(args, '--capture-iso'), value(args, '--own-mileage')),
                         ('rehearsal', '9242', 'KEBONUSEY', '2027-09-13', 'auto', '65000'))
        self.assertNotIn('--at', args)
        self.assertNotIn('--live-state-dir', args)


class OutcomeTests(unittest.TestCase):
    FIRST = {'role': 'first', 'name': 'wife-first'}
    FALLBACK = {'role': 'fallback', 'name': 'wife-fallback'}

    def say(self, run=None, report=None, intent=None, signal=None):
        return astra.run_outcome(run or self.FIRST, RUN_DAY, report, intent, signal)

    def test_order_record_decides_first(self):
        self.assertEqual(self.say(intent={'state': 'ordered', 'paymentWindowReached': True})[0], '✔')
        mark, text = self.say(intent={'state': 'ordered', 'paymentWindowReached': False,
                                      'handoff': 'user-payment-method'})
        self.assertEqual(mark, '✔')
        self.assertIn('결제수단을 고르고', text)
        mark, text = self.say(intent={'state': 'ordered', 'paymentWindowReached': False})
        self.assertEqual(mark, '✔')
        self.assertIn('예약 조회에서 결제', text)
        mark, text = self.say(intent={'state': 'unknown', 'why': 'business-error'}, report={'result': None})
        self.assertEqual(mark, '✘')
        self.assertIn('거절', text)

    def test_unclear_results_send_the_person_to_check(self):
        for intent in ({'state': 'unknown', 'why': 'order-send-exception'}, {'state': 'sending'},
                       {'state': 'prep-order-possible'}):
            with self.subTest(intent['state']):
                mark, text = self.say(intent=intent)
                self.assertEqual(mark, '?')
                self.assertIn('예약 조회', text)

    def test_fallback_that_did_not_order_is_not_a_failure(self):
        mark, text = self.say(run=self.FALLBACK, report={'result': 'done'}, intent={'state': 'prep-no-unblocked-order'},
                              signal={'state': 'order-recorded'})
        self.assertEqual(mark, '–')
        self.assertIn('1순위가 좌석을 잡아서', text)
        mark, text = self.say(run=self.FALLBACK, report={'result': 'done'}, signal=None)
        self.assertIn('기다리다', text)

    def test_runs_that_never_fired_say_why(self):
        self.assertEqual(self.say()[1], '아직 시작하지 않았다')
        self.assertIn('진행 중', self.say(report={'fireAt': '2026-09-25T09:00:00+09:00'})[1])
        for result in ('reprep-cutoff', 'attempts-exhausted', 'prepare-failed', 'chrome-failed'):
            with self.subTest(result):
                mark, text = self.say(report={'result': result})
                self.assertEqual(mark, '✘')
                self.assertIn('발사하지 못했다', text)
        self.assertIn('live_order-exit-2', self.say(report={'result': 'live_order-exit-2'})[1])


class SmallRuleTests(unittest.TestCase):
    def test_task_names_are_unique_and_dated(self):
        names = [astra.task_name(RUN_DAY, r) for r in ac.plan_runs(CFG, RUN_DAY)]
        self.assertEqual(names, ['0925-wife-first', '0925-wife-fallback', '0925-me-first', '0925-me-fallback'])

    def test_argument_lines_fit_the_scheduler_script(self):
        # schedule_api_day.ps1 은 & | < > ^ " 가 든 줄을 거부한다.
        for run in ac.plan_runs(CFG, RUN_DAY):
            line = ' '.join(ac.api_day_args(run, CFG))
            self.assertFalse(set('&|<>^"') & set(line), line)

    def test_env_reader_returns_names_never_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp) / '.env'
            env.write_text('# 설명\nKE_SKYPASS_ID=someone\nKE_SKYPASS_PW="s3cret"\nKE_LOGIN_TAB=\nKE_SKYPASS_ID_2 = \n',
                           encoding='utf-8')
            with mock.patch.object(astra, 'ENV_FILE', env):
                got = astra.env_filled()
            self.assertEqual(got, {'KE_SKYPASS_ID', 'KE_SKYPASS_PW'})
            self.assertFalse(any('someone' in x or 's3cret' in x for x in got))
            with mock.patch.object(astra, 'ENV_FILE', Path(tmp) / 'none'):
                self.assertEqual(astra.env_filled(), set())

    def test_rehearsal_date_is_an_open_day_on_the_targets_weekday(self):
        target = date.fromisoformat(CFG['trip']['date'])
        for now in (date(2026, 9, 24), date(2026, 10, 5), date(2026, 7, 1)):
            with self.subTest(now), mock.patch.object(astra, 'today', return_value=now):
                picked = date.fromisoformat(astra.rehearsal_date(CFG))
                self.assertEqual(picked.weekday(), target.weekday())
                self.assertLess(picked, target)
                self.assertLessEqual(picked, now + timedelta(days=ac.OPEN_DAYS_AHEAD - 1))

    def test_schedule_refuses_times_that_already_passed(self):
        self.assertIn('지난 날', astra.check_times(CFG, '2020-01-01'))
        self.assertIsNone(astra.check_times(CFG, (astra.today() + timedelta(days=3)).isoformat()))
        self.assertIn('오늘', astra.check_times(CFG, (astra.today() + timedelta(days=3)).isoformat(), now_start=True))

    def test_used_slots_cover_every_browser_the_plan_needs(self):
        slots = astra.used_slots(CFG)
        self.assertEqual([(p, a['name'], use) for p, a, use in slots],
                         [(9232, 'wife', '1순위'), (9243, 'wife', '대체'), (9242, 'me', '1순위'),
                          (9244, 'me', '대체'), (9233, 'me', '계측')])


if __name__ == '__main__':
    unittest.main(verbosity=0)
