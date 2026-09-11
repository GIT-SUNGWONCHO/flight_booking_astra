"""로그 누락·재시도·실행 혼합을 빠른 API 성능으로 잘못 계산하지 않는지 시험."""
import json
import tempfile
import unittest
from pathlib import Path
from analyze import analyze


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.run={'runId':'fixture','clock':{'offset':.05},
                  'fire':{'localAt':97500,'targetAt':100050},'secondsFromFire':25}
        self.network={'runId':'fixture','rows':[
            {'path':'/awardAvailability','status':200,'timing':{'startTime':100000,'responseEnd':1500}},
            {'path':'/fareInformation','status':200,'timing':{'startTime':102000,'responseEnd':1400}},
            {'path':'/inputTravellers','status':200,'orderCreated':False,'timing':{'startTime':109000,'responseEnd':5800}},
        ]}

    def run_analysis(self):
        for name,value in [('manual_booking.json',self.run),('network.json',self.network)]:
            (self.folder/name).write_text(json.dumps(value),encoding='utf-8')
        return analyze(self.folder)

    def test_offsets_and_conditional_model(self):
        r=self.run_analysis()
        self.assertEqual(r['paymentWindowSecondsFromOpen'],22.5)
        self.assertEqual(r['conditionalZeroGapModel']['orderRequestSentSeconds'],2.9)
        self.assertEqual(r['conditionalZeroGapModel']['inputTravellersResponseSeconds'],8.7)
        self.assertEqual(r['betweenCoreCalls'][1]['seconds'],5.6)
        self.assertFalse(r['orderBusinessAcceptanceVerified'])

    def test_missing_timing_is_not_zero_latency(self):
        self.network['rows'][1]['timing']['responseEnd']=-1
        r=self.run_analysis()
        self.assertIsNone(r['conditionalZeroGapModel'])
        self.assertEqual(len(r['missingTiming']),1)

    def test_retries_and_http_errors_have_no_simple_model(self):
        self.network['rows'].append(self.network['rows'][0].copy())
        self.assertIsNone(self.run_analysis()['conditionalZeroGapModel'])
        self.network['rows'].pop();self.network['rows'][1]['status']=503
        self.assertIsNone(self.run_analysis()['conditionalZeroGapModel'])

    def test_different_runs_rejected(self):
        self.network['runId']='old'
        with self.assertRaises(ValueError):self.run_analysis()


if __name__=='__main__':unittest.main()
