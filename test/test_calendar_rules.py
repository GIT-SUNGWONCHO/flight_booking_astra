"""실행일 주말과 출발일 일요일을 구분하고 준비·리허설에 함께 적용한다."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
from test_calendar import load_calendar, plan, rehearsal_plan, render, KST
import prepare_day


class CalendarRulesTests(unittest.TestCase):
    def test_departure_sundays_both_directions(self):
        for day, departure in [('2026-09-10', '2027-09-05'),
                               ('2026-09-17', '2027-09-12')]:
            p = plan(day)
            self.assertFalse(p['enabled'])
            self.assertEqual(p['departureDate'], departure)
            self.assertIn('출발일 일요일', p['skipReason'])

    def test_departure_exclusion_override(self):
        """규칙은 남기고 특정 실행일만 예외로 연다(2026-09-23 사용자 결정: 9/24 런던).

        예외 목록을 비우면 다시 막혀야 한다. 그래야 예외가 규칙을 지운 게 아니라는 것이 검사된다.
        """
        p = plan('2026-09-24')
        self.assertTrue(p['enabled'])
        self.assertIsNone(p['skipReason'])
        self.assertEqual(p['departureDate'], '2027-09-19')
        self.assertEqual((p['origin'], p['destination'], p['flight']), ('LHR', 'ICN', '908'))

        cfg = load_calendar()
        cfg['departureExclusionOverrides'] = []
        with patch('test_calendar.load_calendar', return_value=cfg):
            off = plan('2026-09-24')
            self.assertFalse(off['enabled'])
            self.assertIn('출발일 일요일', off['skipReason'])

    def test_sunday_route_is_london(self):
        """2026-09-23 실측: 일요일 CDG→ICN 은 KE 운항편이 없고 공동운항 AF5902 만 있다."""
        self.assertEqual(plan('2026-09-24')['flight'], '908')
        self.assertEqual(plan('2026-09-25')['flight'], '932')

    def test_weekends_follow_the_confirmed_setting(self):
        """주말 휴무는 정책이지 상수가 아니다. 설정을 뒤집어 양쪽을 다 본다.

        2026-09-12 에 사용자가 주말 테스트를 켰다. 기댓값만 True 로 바꾸면
        weekends=False 일 때 건너뛰는 기능 자체를 아무도 검사하지 않게 된다.
        """
        self.assertTrue(plan('2026-09-12')['enabled'])
        self.assertTrue(plan('2026-09-13')['enabled'])
        self.assertTrue(plan('2026-09-11')['enabled'])

        cfg = load_calendar()
        cfg['weekends'] = False
        with patch('test_calendar.load_calendar', return_value=cfg):
            off = plan('2026-09-12')
            self.assertFalse(off['enabled'])
            self.assertIn('주말', off['skipReason'])
            self.assertTrue(plan('2026-09-11')['enabled'])

    def test_important_targets(self):
        for day in ['2026-09-14', '2026-09-25']:
            self.assertTrue(plan(day)['enabled'])
            self.assertTrue(plan(day)['important'])

    def test_friday_morning_rehearsal_avoids_sunday(self):
        for run_day, now, expected in [
            ('2026-09-11', datetime(2026, 9, 11, 8, 20, tzinfo=KST), '2027-09-04'),
            ('2026-09-18', datetime(2026, 9, 18, 8, 20, tzinfo=KST), '2027-09-11'),
            ('2026-09-25', datetime(2026, 9, 25, 8, 20, tzinfo=KST), '2027-09-18')]:
            self.assertEqual(rehearsal_plan(run_day, now)['rehearsalDepartureDate'], expected)
        with self.assertRaises(ValueError):
            rehearsal_plan('2026-09-17')

    def test_skipped_day_never_starts_preparation(self):
        with patch.object(prepare_day.subprocess, 'Popen') as launch:
            self.assertEqual(prepare_day.main(SimpleNamespace(day='2026-09-17')), 0)
            launch.assert_not_called()

    def test_generated_calendar_matches_config(self):
        self.assertEqual((ROOT / 'docs' / 'calendar.md').read_text(encoding='utf-8'), render())


if __name__ == '__main__':
    unittest.main()
