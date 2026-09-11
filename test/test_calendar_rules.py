"""실행일 주말과 출발일 일요일을 구분하고 준비·리허설에 함께 적용한다."""
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'dev'))
from test_calendar import plan, rehearsal_plan, render, KST
import prepare_day


class CalendarRulesTests(unittest.TestCase):
    def test_departure_sundays_both_directions(self):
        for day, departure in [('2026-09-10', '2027-09-05'),
                               ('2026-09-17', '2027-09-12'),
                               ('2026-09-24', '2027-09-19')]:
            p = plan(day)
            self.assertFalse(p['enabled'])
            self.assertEqual(p['departureDate'], departure)
            self.assertIn('출발일 일요일', p['skipReason'])

    def test_weekends_and_important_targets(self):
        self.assertFalse(plan('2026-09-12')['enabled'])
        self.assertTrue(plan('2026-09-11')['enabled'])
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
        self.assertEqual((ROOT / 'CALENDAR.md').read_text(encoding='utf-8'), render())


if __name__ == '__main__':
    unittest.main()
