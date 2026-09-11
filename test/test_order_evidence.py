"""주문 식별자 미관측을 실패로, HTTP200을 수락으로 오인하지 않는다."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'dev'))
from network_trace import NetworkTrace,order_response_evidence


class OrderEvidenceTests(unittest.TestCase):
    def test_unknown_schema_and_identifier_are_unverified(self):
        for value in ({},{'orderId':'private-order'},{'result':{'pnr':'private-pnr'}},{'errorCode':'failure'}):
            evidence=order_response_evidence(value)
            self.assertEqual(evidence['acceptance'],'unverified')
            self.assertFalse(evidence['seatHoldVerified'])

    def test_body_and_personal_field_names_not_saved(self):
        private={'orderId':'private-order','message':'private message','travellers':[{'name':'private name','skypassNumber':'private number'}],
                 'private dynamic key':'private value','result':{'pnr':'private-pnr'}}
        evidence=order_response_evidence(private)
        saved=json.dumps(evidence)
        self.assertNotIn('private',saved)
        self.assertNotIn('skypassNumber',saved)
        self.assertTrue(evidence['orderIdObserved'])

    def test_http_error_is_not_legacy_success_and_parse_failure_is_saved(self):
        with tempfile.TemporaryDirectory() as name:
            folder=Path(name);trace=NetworkTrace(MagicMock(),folder,'09-04')
            response=SimpleNamespace(status=503,ok=False,json=lambda:{'orderId':'private-order'})
            request=SimpleNamespace(url='https://www.koreanair.com/api/ap/booking/traveller/inputTravellers',
                                    method='POST',timing={},response=lambda:response)
            trace.finished(request)
            result=json.loads((folder/'network.json').read_text(encoding='utf-8'))
            self.assertFalse(result['rows'][-1]['orderCreated'])
            self.assertEqual(result['rows'][-1]['orderEvidence']['acceptance'],'unverified')
            def invalid_json():raise ValueError('private data')
            response.json=invalid_json
            trace.finished(request)
            result=json.loads((folder/'network.json').read_text(encoding='utf-8'))
            self.assertEqual(result['rows'][-1]['traceError'],'ValueError')
            self.assertNotIn('private',json.dumps(result))


if __name__=='__main__':unittest.main()
