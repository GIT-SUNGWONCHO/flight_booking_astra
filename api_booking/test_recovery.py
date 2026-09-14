"""합성 응답만 사용한다. 사이트·파일·브라우저 접근 없음."""
import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

import recovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.quote = SimpleNamespace(target=SimpleNamespace(currency='KRW'),
            amount=Decimal(0), total_amount=Decimal(100), mileage=Decimal(35000))
        self.response = {'body': json.dumps({'pnr':'SECRET-REFERENCE',
            'token':'SECRET-TOKEN', 'pnrFareInfo':{'currency':'KRW','amount':10,
                'totalAmount':100,'mileage':35000}})}

    def test_retains_response_and_only_reports_allowlisted_fields(self):
        messages = []
        commands = iter(['retry', 'summary', 'exit'])
        with patch.object(recovery.FailureEvidence, 'clear', autospec=True) as clear:
            result = recovery.inspect_failure(self.response, self.quote,
                emit=messages.append, read=lambda _:next(commands))
            evidence = clear.call_args.args[0]
            self.assertIs(evidence.response, self.response)
            self.assertEqual(result, 'user-exit')
            self.assertNotIn('SECRET', '\n'.join(messages))
            self.assertIn('지원 명령', messages[1])
            summary = json.loads(messages[2])
            self.assertFalse(summary['values']['amount']['matches'])
            self.assertTrue(summary['referencePresent'])
            clear.assert_called_once()

    def test_closed_input_and_interrupt_finish_without_releasing_order(self):
        for error in (EOFError, KeyboardInterrupt):
            def read(_): raise error()
            self.assertEqual(recovery.inspect_failure(self.response, self.quote,
                emit=lambda _:None, read=read), 'input-closed')

    def test_clear_drops_references_without_mutating_callers_response(self):
        evidence = recovery.FailureEvidence(self.response, self.quote)
        evidence.clear()
        self.assertEqual(evidence.response, {})
        self.assertIsNone(evidence.quote)
        self.assertIn('body', self.response)

    def test_repr_does_not_expose_payload(self):
        self.assertNotIn('SECRET', repr(recovery.FailureEvidence(self.response, self.quote)))

    def test_resume_is_explicit_once_and_failed_response_stays_available(self):
        commands=iter(['summary','resume','resume','summary','exit']);calls=[];messages=[]
        def resume():calls.append(1);return False
        result=recovery.inspect_failure(self.response,self.quote,resume=resume,
            emit=messages.append,read=lambda _:next(commands))
        self.assertEqual(calls,[1]);self.assertEqual(result,'user-exit')
        self.assertEqual(sum('responseObject' in x for x in messages),2)
        self.assertNotIn('SECRET','\n'.join(messages))

    def test_resume_success_returns_without_second_call(self):
        self.assertEqual(recovery.inspect_failure(self.response,self.quote,resume=lambda:True,
            emit=lambda _:None,read=lambda _:'resume'),'resumed')

    def test_resume_exception_not_exposed_or_retried(self):
        calls=[];messages=[];commands=iter(['resume','resume','exit'])
        def resume():calls.append(1);raise ValueError('SECRET-TOKEN')
        recovery.inspect_failure(self.response,self.quote,resume=resume,
            emit=messages.append,read=lambda _:next(commands))
        self.assertEqual(calls,[1]);self.assertNotIn('SECRET','\n'.join(messages))

    def test_diagnose_does_not_consume_resume_or_expose_payload(self):
        commands=iter(['diagnose','diagnose','resume']);messages=[];calls=[]
        def diagnose():return {'documentMatches':True,'sessionMatches':'SECRET',
            'token':'SECRET','ageSeconds':float('inf'),'bindingUsed':False}
        def resume():calls.append(1);return True
        result=recovery.inspect_failure(self.response,self.quote,diagnose=diagnose,resume=resume,
            emit=messages.append,read=lambda _:next(commands))
        self.assertEqual(result,'resumed');self.assertEqual(calls,[1])
        self.assertNotIn('SECRET','\n'.join(messages));self.assertNotIn('Infinity','\n'.join(messages))
        self.assertEqual(sum('documentMatches' in m for m in messages),2)


if __name__ == '__main__':
    unittest.main()
