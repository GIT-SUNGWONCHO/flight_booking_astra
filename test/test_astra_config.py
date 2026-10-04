"""실행 설정(dev/astra_config.py) 시험. 파일·브라우저·사이트 없음.

지키려는 것은 둘이다. 설정 파일이 없을 때 로그인 규칙이 2026-09 와 한 글자도 다르지 않을 것,
그리고 설정에 고칠 곳이 있으면 어디를 어떻게 고칠지 한 번에 다 알려 줄 것.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
import astra_config as ac  # noqa: E402

GOOD = {
    'trip': {'origin': 'FCO', 'destination': 'ICN', 'flight': '932', 'date': '2027-09-20', 'captureDate': 'auto'},
    'accounts': [
        {'name': 'wife', 'login': 'skypass', 'mileage': 100000, 'first': 'prestige', 'fallback': 'economy'},
        {'name': 'me', 'login': 'naver', 'mileage': 65000, 'first': 'prestige', 'fallback': 'economy'},
    ],
    'observer': 'me',
}


def changed(**paths):
    """GOOD 의 사본에서 'trip.origin' 같은 경로의 값만 바꾼다."""
    cfg = json.loads(json.dumps(GOOD))
    for path, value in paths.items():
        node, keys = cfg, path.replace('__', '.').split('.')
        for key in keys[:-1]:
            node = node[int(key)] if key.isdigit() else node[key]
        last = keys[-1]
        if value is ...:
            del node[last]
        else:
            node[int(last) if last.isdigit() else last] = value
    return cfg


class LegacyRuleTests(unittest.TestCase):
    """설정 파일이 없을 때(cfg=None). setup.py 의 예전 login_mode·SKYPASS_PORTS 와 같아야 한다."""

    def test_skypass_only_on_the_two_ports_and_only_when_the_env_says_so(self):
        for port in ac.SLOTS:
            with self.subTest(port):
                self.assertEqual(ac.login_for(port, {'KE_LOGIN_MODE': 'skypass'}, None),
                                 'skypass' if port in (9232, 9243) else 'naver')
                self.assertEqual(ac.login_for(port, {}, None), 'naver')
                self.assertEqual(ac.login_for(port, {'KE_LOGIN_MODE': ' SkyPass '}, None),
                                 'skypass' if port in (9232, 9243) else 'naver')

    def test_marker_ports_and_env_names_are_the_old_ones(self):
        self.assertEqual(ac.marker_ports({}, None), frozenset({9232, 9243}))
        self.assertEqual(ac.marker_ports({'KE_LOGIN_MODE': 'skypass'}, None), frozenset({9232, 9243}))
        for port in ac.SLOTS:
            self.assertEqual(ac.credential_keys(port, None), ('KE_SKYPASS_ID', 'KE_SKYPASS_PW', 'KE_LOGIN_TAB'))

    def test_profiles_are_the_folders_already_on_disk(self):
        self.assertEqual({p: ac.profile_for(p) for p in ac.SLOTS},
                         {9232: '.debug-profile', 9233: '.debug-profile2', 9242: '.api-profile',
                          9243: '.debug-profile3', 9244: '.api-profile2'})
        self.assertIsNone(ac.profile_for(9222))
        self.assertEqual(set(ac.BOOKING_PORTS), {9232, 9242, 9243, 9244})

    def test_missing_file_means_legacy(self):
        self.assertIsNone(ac.load(Path(tempfile.gettempdir()) / 'no-such-astra-run.json'))


class ConfiguredAccountTests(unittest.TestCase):
    def setUp(self):
        self.cfg = ac.validate(GOOD)

    def test_each_seat_follows_its_account(self):
        want = {9232: 'skypass', 9243: 'skypass', 9242: 'naver', 9244: 'naver', 9233: 'naver'}
        self.assertEqual({p: ac.login_for(p, {}, self.cfg) for p in ac.SLOTS}, want)
        # 설정이 있으면 .env 의 KE_LOGIN_MODE 는 보지 않는다.
        self.assertEqual(ac.login_for(9242, {'KE_LOGIN_MODE': 'skypass'}, self.cfg), 'naver')

    def test_second_account_uses_its_own_env_names(self):
        cfg = ac.validate(changed(accounts__1__login='skypass', observer='wife'))
        self.assertEqual(ac.credential_keys(9232, cfg)[:2], ('KE_SKYPASS_ID', 'KE_SKYPASS_PW'))
        self.assertEqual(ac.credential_keys(9243, cfg)[:2], ('KE_SKYPASS_ID', 'KE_SKYPASS_PW'))
        self.assertEqual(ac.credential_keys(9242, cfg)[:2], ('KE_SKYPASS_ID_2', 'KE_SKYPASS_PW_2'))
        self.assertEqual(ac.credential_keys(9244, cfg)[:2], ('KE_SKYPASS_ID_2', 'KE_SKYPASS_PW_2'))
        self.assertEqual(ac.credential_keys(9233, cfg)[:2], ('KE_SKYPASS_ID', 'KE_SKYPASS_PW'))   # 계측 = wife
        self.assertEqual(ac.marker_ports({}, cfg), frozenset(ac.SLOTS))

    def test_one_account_leaves_the_second_seats_unassigned(self):
        cfg = ac.validate(changed(accounts=[GOOD['accounts'][1]], observer=None))
        self.assertEqual(ac.account_for(9232, cfg)['name'], 'me')
        self.assertIsNone(ac.account_for(9242, cfg))
        self.assertIsNone(ac.account_for(9233, cfg))
        self.assertEqual(ac.login_for(9232, {}, cfg), 'naver')
        self.assertEqual(ac.marker_ports({}, cfg), frozenset())

    def test_defaults_are_the_values_that_worked_on_0925(self):
        self.assertEqual(self.cfg['times'], {'open': '09:00:00', 'start': '08:20', 'capture': '08:38:00',
                                             'health': '08:50:00', 'lastPrepare': '08:52:00'})
        self.assertEqual(self.cfg['tuning'], {'preFireMs': 800, 'fallbackDelayMs': 3500, 'fallbackWaitSeconds': 5.0})

    def test_keys_starting_with_underscore_are_notes(self):
        noted = changed()
        noted['_설명'] = '아무 말'
        noted['trip']['_note'] = 'x'
        noted['accounts'][0]['_왜'] = 'y'
        self.assertEqual(ac.validate(noted), self.cfg)


class ValidationTests(unittest.TestCase):
    def errors(self, cfg):
        with self.assertRaises(ac.ConfigError) as caught:
            ac.validate(cfg)
        return str(caught.exception)

    def test_every_problem_is_listed_at_once(self):
        text = self.errors(changed(trip__origin='fco', trip__flight='KE932', accounts__0__mileage='많음',
                                   accounts__1__login='kakao', observer='nobody'))
        self.assertIn('5군데', text)
        for hint in ('trip.origin', 'trip.flight', 'accounts[0].mileage', 'accounts[1].login', 'observer'):
            self.assertIn(hint, text)

    def test_single_bad_values(self):
        cases = {
            'trip.date': changed(trip__date='2027/09/20'),
            'trip.captureDate': changed(trip__captureDate='2027-09-20'),      # 목표와 같은 날
            'trip.origin 과': changed(trip__destination='FCO'),
            'accounts: 계정을': changed(accounts=[]),
            'accounts[0].first': changed(accounts__0__first='first'),
            'fallback 이 first 와 같다': changed(accounts__0__fallback='prestige'),
            'name 이 겹친다': changed(accounts__1__name='wife', observer='wife'),
            'accounts[0].name': changed(accounts__0__name='와이프', observer='me'),
            'times.open': changed(times={'open': '9:00'}),
            'times: start <': changed(times={'capture': '08:55:00'}),
            'tuning.preFireMs': changed(tuning={'preFireMs': 5000}),
            'tuning.fallbackWaitSeconds': changed(tuning={'fallbackWaitSeconds': 0}),
        }
        for hint, cfg in cases.items():
            with self.subTest(hint):
                self.assertIn(hint, self.errors(cfg))

    def test_three_accounts_are_refused(self):
        third = dict(GOOD['accounts'][0], name='third')
        self.assertIn('1~2개', self.errors(changed(accounts=GOOD['accounts'] + [third])))

    def test_fallback_is_optional(self):
        cfg = ac.validate(changed(accounts__0__fallback=None, accounts__1__fallback=...))
        self.assertEqual([a['fallback'] for a in cfg['accounts']], [None, None])

    def test_unreadable_file_says_so(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'run.json'
            path.write_text('{ "trip": ', encoding='utf-8')
            with self.assertRaises(ac.ConfigError) as caught:
                ac.load(path)
            self.assertIn('JSON 형식 오류', str(caught.exception))
            path.write_text('﻿' + json.dumps(GOOD), encoding='utf-8')      # 메모장이 붙이는 BOM
            self.assertEqual(ac.load(path)['trip']['flight'], '932')


class ExampleFileTests(unittest.TestCase):
    def test_the_example_in_the_repo_is_valid(self):
        if not ac.EXAMPLE.exists():
            self.skipTest('config/run.example.json 은 다음 단계에서 넣는다')
        cfg = ac.load(ac.EXAMPLE)
        self.assertGreaterEqual(len(cfg['accounts']), 1)


if __name__ == '__main__':
    unittest.main(verbosity=0)
