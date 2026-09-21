"""본문 축소 조사 도구(api_booking/request_shape_probe.py)의 변형 생성·집계 시험. 실사이트 요청 없음."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'api_booking'))
sys.path.insert(0, str(ROOT / 'dev'))
import request_shape_probe as rsp  # noqa: E402

BODY = {'currency': 'KRW', 'travelers': [{'ptc': 'ADT', 'count': 1}], 'nearbyDate': True,
        'cabinClass': 'ECONOMY', 'commercialFareFamilies': ['KEBONUSEY', 'KEBONUSPR'],
        'segmentList': [{'departureDate': '20261217', 'departureAirport': 'SEL',
                         'arrivalAirport': 'CDG', 'connection': True}]}


class VariantTests(unittest.TestCase):
    def test_baseline_is_first_and_unchanged(self):
        name, body = rsp.build_variants(BODY)[0]
        self.assertEqual(name, 'baseline')
        self.assertEqual(body, BODY)

    def test_city_code_is_narrowed_to_airport(self):
        made = dict(rsp.build_variants(BODY, origin_exact='ICN'))
        self.assertEqual(made['origin=ICN']['segmentList'][0]['departureAirport'], 'ICN')
        self.assertEqual(BODY['segmentList'][0]['departureAirport'], 'SEL')   # 원본 보존

    def test_no_narrowing_variant_when_already_exact(self):
        body = {**BODY, 'segmentList': [{**BODY['segmentList'][0], 'departureAirport': 'ICN'}]}
        self.assertNotIn('origin=ICN', dict(rsp.build_variants(body, origin_exact='ICN')))

    def test_required_fields_are_never_dropped(self):
        names = [n for n, _ in rsp.build_variants(BODY, limit=99)]
        for key in ('segmentList', 'travelers'):
            self.assertNotIn(f'drop:{key}', names)
        for key in ('departureDate', 'departureAirport', 'arrivalAirport'):
            self.assertNotIn(f'drop:seg.{key}', names)

    def test_optional_fields_are_dropped_and_booleans_flipped(self):
        made = dict(rsp.build_variants(BODY, limit=99))
        self.assertNotIn('currency', made['drop:currency'])
        self.assertNotIn('connection', made['drop:seg.connection']['segmentList'][0])
        self.assertIs(made['false:nearbyDate']['nearbyDate'], False)
        self.assertIs(made['false:seg.connection']['segmentList'][0]['connection'], False)

    def test_fare_family_list_is_narrowed_to_the_target(self):
        made = dict(rsp.build_variants(BODY, family='KEBONUSPR', limit=99))
        self.assertEqual(made['families=[KEBONUSPR]']['commercialFareFamilies'], ['KEBONUSPR'])
        self.assertEqual(BODY['commercialFareFamilies'], ['KEBONUSEY', 'KEBONUSPR'])

    def test_no_family_variant_when_list_is_already_one_or_absent(self):
        single = {**BODY, 'commercialFareFamilies': ['KEBONUSEY']}
        self.assertNotIn('families=[KEBONUSEY]', dict(rsp.build_variants(single, family='KEBONUSEY')))
        missing = {k: v for k, v in BODY.items() if k != 'commercialFareFamilies'}
        self.assertEqual([n for n, _ in rsp.build_variants(missing, family='KEBONUSEY')][0], 'baseline')

    def test_limit_caps_the_list_but_keeps_baseline(self):
        made = rsp.build_variants(BODY, limit=3)
        self.assertEqual(len(made), 3)
        self.assertEqual(made[0][0], 'baseline')


class SummaryTests(unittest.TestCase):
    def rows(self):
        return [{'variant': 'baseline', 'state': 'ok', 'seat': '5', 'elapsedMs': 1600},
                {'variant': 'baseline', 'state': 'ok', 'seat': '5', 'elapsedMs': 1400},
                {'variant': 'baseline', 'state': 'ok', 'seat': '5', 'elapsedMs': 1500},
                {'variant': 'origin=ICN', 'state': 'ok', 'seat': '5', 'elapsedMs': 1200},
                {'variant': 'origin=ICN', 'state': 'ok', 'seat': '5', 'elapsedMs': 1100},
                {'variant': 'origin=ICN', 'state': 'error:ERT.3002', 'seat': None, 'elapsedMs': 90}]

    def test_median_and_delta_against_baseline(self):
        out = rsp.summarize(self.rows())
        self.assertEqual(out['baseline']['medianMs'], 1500)
        self.assertEqual(out['origin=ICN']['medianMs'], 1150)
        self.assertEqual(out['origin=ICN']['deltaMs'], -350)

    def test_failed_samples_are_counted_but_not_timed(self):
        out = rsp.summarize(self.rows())['origin=ICN']
        self.assertEqual((out['n'], out['targetSeen']), (3, 2))
        self.assertEqual(out['errors'], ['error:ERT.3002'])

    def test_variant_without_any_good_sample_has_no_median(self):
        out = rsp.summarize([{'variant': 'drop:currency', 'state': 'target-absent',
                              'seat': None, 'elapsedMs': 800}])
        self.assertNotIn('medianMs', out['drop:currency'])
        self.assertEqual(out['drop:currency']['targetSeen'], 0)


if __name__ == '__main__':
    unittest.main()
