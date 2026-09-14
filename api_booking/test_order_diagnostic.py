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


if __name__ == '__main__':
    unittest.main()
