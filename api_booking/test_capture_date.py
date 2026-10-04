"""캡처 날짜 자동 선택 시험. 브라우저·사이트 없음.

달력 칸 문구는 실사이트(2026-10-04, FCO→ICN, 2027년 9월)에서 읽은 그대로다.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import capture_date as cd  # noqa: E402


def cell(text, disabled=False):
    return {'text': text, 'disabled': disabled}


SEPTEMBER = [
    cell('', True),
    cell('8월 30 08월 30일 (월) 일반석'),
    cell('31 08월 31일 (화) 운항편 없음', True),
    cell('9월 01 09월 01일 (수) 일반석'),
    cell('06 09월 06일 (월) 일반석'),
    cell('08 09월 08일 (수) 일반석'),
    cell('10 09월 10일 (금) , 성수기 운항편 없음', True),
    cell('11 09월 11일 (토) , 성수기일반석'),
    cell('13 09월 13일 (월) , 성수기일반석'),
    cell('14 09월 14일 (화) , 성수기 운항편 없음', True),
    cell('15 09월 15일 (수) , 성수기일반석, 선택됨'),
    cell('16 09월 16일 (목) , 성수기 운항편 없음', True),
    cell('18 09월 18일 (토) , 성수기 좌석 없음', True),
    cell('20 09월 20일 (월) 좌석 없음', True),
    cell('22 09월 22일 (수) 좌석 없음', True),
    cell('27 09월 27일 (월) 일반석'),
    cell('29 09월 29일 (수) 일반석'),
    cell('', True),
]


class ParseTests(unittest.TestCase):
    def test_states_are_read_from_the_cell_text(self):
        days = cd.parse_cells(SEPTEMBER, '2027-09-20')
        self.assertEqual(days['2027-09-15'], 'seat:일반석')
        self.assertEqual(days['2027-09-18'], 'sold-out')
        self.assertEqual(days['2027-09-14'], 'no-flight')
        self.assertEqual(days['2027-08-30'], 'seat:일반석')
        self.assertEqual(len(days), 16)                      # 빈 칸 둘은 뺀다

    def test_year_comes_from_the_target(self):
        january = [cell('12월 30 12월 30일 (목) 일반석'), cell('1월 02 01월 02일 (일) 일반석')]
        self.assertEqual(sorted(cd.parse_cells(january, '2028-01-05')), ['2027-12-30', '2028-01-02'])
        self.assertEqual(sorted(cd.parse_cells(january, '2027-12-31')), ['2027-12-30', '2028-01-02'])

    def test_enabled_cell_without_a_known_cabin_is_unknown(self):
        self.assertEqual(cd.parse_cells([cell('15 09월 15일 (수) 52,500')], '2027-09-20'),
                         {'2027-09-15': 'unknown'})
        self.assertEqual(cd.parse_cells([cell('15 09월 15일 (수) 일반석', True)], '2027-09-20'),
                         {'2027-09-15': 'unknown'})

    def test_garbage_is_ignored(self):
        self.assertEqual(cd.parse_cells([None, {}, {'text': None}, cell('공지'), cell('13월 45일')], '2027-09-20'), {})
        self.assertEqual(cd.parse_cells(None, '2027-09-20'), {})


class ChooseTests(unittest.TestCase):
    def test_nearest_earlier_day_with_economy(self):
        # 9/25 본선에서 사람이 고른 날짜와 같다.
        self.assertEqual(cd.choose(SEPTEMBER, '2027-09-20'), '2027-09-15')

    def test_days_after_the_target_are_never_used(self):
        # 실전 아침에는 목표 뒤 날짜가 아직 안 열렸다. 열려 있어 보여도 쓰지 않는다.
        self.assertEqual(cd.choose(SEPTEMBER, '2027-09-28'), '2027-09-27')
        self.assertEqual(cd.choose(SEPTEMBER, '2027-09-27'), '2027-09-15')

    def test_sold_out_and_no_flight_days_are_skipped(self):
        self.assertEqual(cd.choose(SEPTEMBER, '2027-09-19'), '2027-09-15')
        self.assertEqual(cd.choose(SEPTEMBER, '2027-09-15'), '2027-09-13')

    def test_nothing_usable_gives_none(self):
        self.assertIsNone(cd.choose(SEPTEMBER, '2027-08-30'))
        self.assertIsNone(cd.choose([], '2027-09-20'))
        self.assertIsNone(cd.choose(SEPTEMBER, '2027-09-20', cabin='프레스티지'))

    def test_tentative_is_a_week_before(self):
        self.assertEqual(cd.tentative('2027-09-20'), '2027-09-13')
        self.assertEqual(cd.tentative('2027-01-03'), '2026-12-27')


if __name__ == '__main__':
    unittest.main(verbosity=0)
