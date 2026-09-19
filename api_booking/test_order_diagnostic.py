import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from evidence import order_amount_diagnostic


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.quote = SimpleNamespace(target=SimpleNamespace(currency='KRW'),
            amount=Decimal('0'), total_amount=Decimal('325500'), mileage=Decimal('35000'))

    def test_reports_changed_field_without_sensitive_values(self):
        body = json.dumps({'pnr': 'PRIVATE-REFERENCE', 'token': 'PRIVATE-TOKEN',
            'pnrFareInfo': {'currency':'KRW', 'amount':100, 'totalAmount':325500, 'mileage':35000}})
        out = order_amount_diagnostic(body, self.quote)
        self.assertTrue(out['referencePresent'])
        self.assertFalse(out['values']['amount']['matches'])
        self.assertTrue(out['values']['totalAmount']['matches'])
        self.assertTrue(out['values']['mileage']['matches'])
        self.assertNotIn('PRIVATE', json.dumps(out))

    def test_invalid_values_are_not_echoed(self):
        out = order_amount_diagnostic(json.dumps({'pnrFareInfo':{'amount':'PRIVATE'}}), self.quote)
        self.assertIsNone(out['values']['amount']['order'])
        self.assertNotIn('PRIVATE', json.dumps(out))

    def test_non_object(self):
        for body in ('oops', '[]', None):
            self.assertEqual(order_amount_diagnostic(body, self.quote), {'responseObject':False})

    def test_business_error_code_and_message_are_kept_without_identifiers(self):
        # 2026-09-19 승인: 주문 오류 코드·메시지 저장. trace·uuid·예약번호·긴 숫자는 남기지 않는다.
        body = json.dumps({'code': 'ERT.20001', 'status': 400,
            'message': '좌석이 없습니다 ABC123 예약 1234567 test@example.com',
            'subMessages': ['잔여 좌석 부족', {'code': 'SUB.1', 'message': '재고 0', 'x': 'PRIVATE'}],
            'trace': 'PRIVATE-TRACE', 'uuid': 'PRIVATE-UUID', 'userDefineMap': {'k': 'PRIVATE'}})
        out = order_amount_diagnostic(body, self.quote)
        self.assertFalse(out['referencePresent'])
        err = out['error']
        self.assertEqual((err['code'], err['status']), ('ERT.20001', 400))
        self.assertEqual(err['message'], '좌석이 없습니다 <id> 예약 <n> <email>')
        self.assertEqual(err['subMessages'], [{'message': '잔여 좌석 부족'},
                                              {'code': 'SUB.1', 'message': '재고 0'}])
        self.assertNotIn('PRIVATE', json.dumps(out, ensure_ascii=False))

    def test_no_error_block_when_reference_present(self):
        body = json.dumps({'pnr': 'PRIVATE-REFERENCE', 'code': 'ERT.1', 'message': 'x'})
        self.assertNotIn('error', order_amount_diagnostic(body, self.quote))

    def test_free_text_code_is_dropped(self):
        body = json.dumps({'code': '고객 홍길동 오류', 'message': '실패'})
        self.assertEqual(order_amount_diagnostic(body, self.quote)['error'], {'message': '실패'})


if __name__ == '__main__':
    unittest.main()
