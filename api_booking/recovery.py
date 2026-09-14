"""실패 응답 보존과 명시적인 같은 응답 인계 콜백. 자체 전송·상태 해제 없음."""
from dataclasses import dataclass, field
import json
import math

from evidence import order_amount_diagnostic


@dataclass(repr=False)
class FailureEvidence:
    response: dict = field(repr=False)
    quote: object = field(repr=False)

    def summary(self):
        # 허용된 숫자와 존재 여부만 출력한다. 원문과 예약번호는 출력하지 않는다.
        return order_amount_diagnostic(self.response.get('body'), self.quote)

    def clear(self):
        self.response = {}
        self.quote = None


def inspect_failure(response, quote, *, emit=print, read=None, resume=None, diagnose=None):
    """summary/exit 및 전달된 콜백이 있을 때 resume 1회를 지원한다.

    참조 원문은 이 실행 메모리에만 존재한다. 이 모드는 같은 주문의 사이트 재개를
    직접 구현하지 않으며 프로세스 종료 후 복원을 보장하지 않는다.
    """
    evidence = FailureEvidence(response, quote)
    reader = input if read is None else read
    resume_used = False
    try:
        emit('응답 메모리 보존 중. summary=금액 진단, exit=종료. 재주문·최종 결제·잠금 해제 없음.')
        if resume is not None:
            emit('resume=같은 응답 재검증·화면 인계 1회. 새 주문 전송·최종 결제 없음.')
        if diagnose is not None:
            emit('diagnose=보존 문맥과 현재 화면 읽기 점검. 상태 변경 없음.')
        while True:
            try:
                command = reader('조사> ').strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 'input-closed'
            if command == 'exit':
                return 'user-exit'
            if command == 'diagnose' and diagnose is not None:
                try:
                    report=diagnose()
                    # 콜백 실수로 원문이 섞여도 고정 bool/유한 숫자 외에는 출력하지 않는다.
                    safe={k:v for k,v in report.items() if k in
                        ('bindingPresent','bindingUsed','withinResumeWindow','documentMatches',
                         'sessionMatches','storageMatches','readFailed') and type(v) is bool}
                    age=report.get('ageSeconds')
                    if type(age) in (int,float) and math.isfinite(age):safe['ageSeconds']=round(age,3)
                    emit(json.dumps(safe,ensure_ascii=False))
                except Exception:
                    emit('읽기 진단 실패. 응답 보존 유지, 상태 변경 없음.')
                continue
            if command == 'resume' and resume is not None:
                if resume_used:
                    emit('이미 인계를 시도했다. 같은 조사에서 반복하지 않는다.')
                    continue
                resume_used = True
                try:
                    completed = resume()
                except Exception:
                    # 콜백 예외에도 원문·예약번호를 출력하거나 재실행하지 않는다.
                    emit('인계 예외. 응답 보존 유지, 재주문 없음.')
                    continue
                if completed is True:
                    return 'resumed'
                emit('같은 응답 인계 미완료. 응답 보존 유지, 재주문 없음.')
                continue
            if command == 'summary':
                emit(json.dumps(evidence.summary(), ensure_ascii=False))
            else:
                emit('지원 명령: summary, exit')
    finally:
        evidence.clear()
