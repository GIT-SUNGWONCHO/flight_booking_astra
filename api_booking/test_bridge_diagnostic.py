import unittest
import json
import connected_bridge

class DiagnosticTests(unittest.TestCase):
    def test_known_reason_allowed(self):
        self.assertEqual(connected_bridge.error_summary(ValueError('session-changed'))['reason'],
                         'session-changed')
    def test_external_message_never_recorded(self):
        for exc in (ValueError('SECRET_TOKEN'),RuntimeError('SECRET_REFERENCE')):
            out=connected_bridge.error_summary(exc)
            self.assertEqual(out['reason'],'unclassified')
            self.assertNotIn('SECRET',json.dumps(out))

if __name__=='__main__':unittest.main()
