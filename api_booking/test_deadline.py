"""로컬 시계 오차·부족한 표본을 빠른 마감 판정으로 잘못 바꾸지 않는지 시험."""
import json
import tempfile
import unittest
from pathlib import Path
from deadline import evaluate, server_offset, depletion, forecast

OPEN_MS = 1789171200000          # 2026-09-12T09:00:00+09:00
OPEN_AT = '2026-09-12T09:00:00+09:00'


def http_date(ms):
    import email.utils
    return email.utils.formatdate(ms / 1000, usegmt=True)


def response(rid, sent, read, present, served=None, state='valid'):
    event = {'kind': 'response', 'id': rid, 'sentAt': sent, 'readAt': read, 'state': state, 'p': present}
    if served is not None:
        event['cache'] = {'date': http_date(served)}
    return event


class ServerAxisTests(unittest.TestCase):
    def test_date_headers_bracket_the_offset(self):
        # 로컬 시계가 서버보다 2초 늦은 실행. 응답은 로컬 축으로 기록된다.
        events = [response(i, OPEN_MS - 2000 + i * 1000, OPEN_MS - 2000 + i * 1000 + 900,
                           True, OPEN_MS + i * 1000 + 500) for i in range(4)]
        bounds = server_offset(events)
        self.assertTrue(bounds['usable'])
        self.assertLessEqual(bounds['lowMs'], 2000)
        self.assertGreaterEqual(bounds['highMs'], 2000)
        self.assertEqual(bounds['samples'], 4)

    def test_single_sample_is_not_an_axis(self):
        self.assertFalse(server_offset([response(1, OPEN_MS, OPEN_MS + 900, True, OPEN_MS + 500)])['usable'])

    def test_missing_date_headers_are_skipped(self):
        self.assertFalse(server_offset([response(1, OPEN_MS, OPEN_MS + 900, True),
                                        response(2, OPEN_MS, OPEN_MS + 900, False)])['usable'])


class DepletionTests(unittest.TestCase):
    def report(self, events):
        return {'runId': 'fixture', 'openAt': OPEN_AT, 'events': events}

    def test_deadline_uses_proven_present_time(self):
        events = [response(1, OPEN_MS + 1000, OPEN_MS + 3000, True, OPEN_MS + 2000),
                  response(2, OPEN_MS + 2000, OPEN_MS + 4200, False, OPEN_MS + 4000)]
        result = depletion(self.report(events), 0)
        self.assertTrue(result['consistent'])
        # 보낸 시각 2.0초가 아니라 서버가 있음으로 답한 2.0초 이후가 마감이다.
        self.assertEqual(result['designDeadlineSeconds'], 2.0)
        self.assertEqual(result['depletionWindowSinceServerOpen'], [2.0, 4.2])

    def test_overlapping_samples_are_not_a_window(self):
        events = [response(1, OPEN_MS + 4000, OPEN_MS + 6000, True, OPEN_MS + 5000),
                  response(2, OPEN_MS + 1000, OPEN_MS + 3000, False, OPEN_MS + 2000)]
        self.assertFalse(depletion(self.report(events), 0)['consistent'])

    def test_errors_are_not_absence(self):
        events = [response(1, OPEN_MS, OPEN_MS + 900, True, OPEN_MS + 500),
                  response(2, OPEN_MS + 1000, OPEN_MS + 2000, None, OPEN_MS + 1500, state='application-error')]
        self.assertFalse(depletion(self.report(events), 0)['observed'])


class ForecastTests(unittest.TestCase):
    def test_unmeasured_step_has_no_arrival_time(self):
        result = forecast([('awardAvailability', 2000.0), ('fareInformation', None)], 6.0)
        self.assertFalse(result['usable'])

    def test_lead_and_gap_move_the_candidate_request(self):
        chain = [('awardAvailability', 2000.0), ('fareInformation', 2000.0), ('inputTravellers', 2000.0)]
        self.assertEqual(forecast(chain, 6.0)['candidateSentSinceServerOpen'], 4.0)
        self.assertEqual(forecast(chain, 6.0, lead=1.0)['candidateSentSinceServerOpen'], 3.0)
        late = forecast(chain, 6.0, gap=1.5)
        self.assertEqual(late['candidateSentSinceServerOpen'], 7.0)
        self.assertEqual(late['verdict'], 'misses')

    def test_no_deadline_gives_no_verdict(self):
        self.assertEqual(forecast([('awardAvailability', 2000.0)], None)['verdict'], 'unknown')


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.run_dir = Path(self.tmp.name) / 'run'; self.run_dir.mkdir()
        self.obs_dir = Path(self.tmp.name) / 'obs'; self.obs_dir.mkdir()
        self.skew = 2000                                   # 로컬 시계가 서버보다 2초 늦다
        self.run = {'runId': 'fixture', 'openAt': OPEN_AT, 'clock': {'offset': 0.1},
                    'fire': {'localAt': OPEN_MS - 2500, 'targetAt': OPEN_MS}}
        self.network = {'runId': 'fixture', 'rows': [
            {'path': '/avail/awardAvailability', 'status': 200,
             'timing': {'startTime': OPEN_MS - self.skew + 1000, 'responseEnd': 2000.0}}]}
        self.observer = {'runId': 'obs', 'openAt': OPEN_AT, 'events': [
            response(1, OPEN_MS - self.skew + 1000, OPEN_MS - self.skew + 3000, True, OPEN_MS + 2000),
            response(2, OPEN_MS - self.skew + 2000, OPEN_MS - self.skew + 4200, False, OPEN_MS + 4000)]}

    def write(self):
        (self.run_dir / 'manual_booking.json').write_text(json.dumps(self.run), encoding='utf-8')
        (self.run_dir / 'network.json').write_text(json.dumps(self.network), encoding='utf-8')
        (self.obs_dir / 'calendar_observer.json').write_text(json.dumps(self.observer), encoding='utf-8')
        return evaluate(self.run_dir, self.obs_dir)

    def test_server_axis_survives_a_wrong_local_clock(self):
        result = self.write()
        # 하한이므로 실제 2.0초 어긋남을 1.7초까지만 입증한다. 남는 차이는 더 늦은 쪽이다.
        self.assertAlmostEqual(result['clock']['ourAxisLateBySeconds'], 1.7, places=1)
        self.assertEqual(result['depletion']['designDeadlineSeconds'], 2.0)
        # 로컬 기준 -2.5초 선발사가 서버 기준으로는 -0.7초까지 줄어든다(실제는 -0.5초).
        self.assertAlmostEqual(result['fireSinceServerOpen'], -0.7, places=1)
        self.assertTrue(any('로컬 시계' in line for line in result['limits']))

    def test_missing_observer_leaves_no_deadline(self):
        (self.run_dir / 'manual_booking.json').write_text(json.dumps(self.run), encoding='utf-8')
        (self.run_dir / 'network.json').write_text(json.dumps(self.network), encoding='utf-8')
        result = evaluate(self.run_dir)
        self.assertFalse(result['depletion']['observed'])
        self.assertIsNone(result['forecast']['deadlineSeconds'])
        self.assertEqual(result['forecast']['verdict'], 'unknown')

    def test_proxy_round_trips_are_declared(self):
        result = self.write()
        self.assertEqual(result['chainRoundTripSource']['fareInformation'], 'proxy-slowest-measured')
        self.assertTrue(any('실측이 아니다' in line for line in result['limits']))

    def test_different_runs_rejected(self):
        self.network['runId'] = 'other'
        with self.assertRaises(ValueError):
            self.write()


if __name__ == '__main__':
    unittest.main()
