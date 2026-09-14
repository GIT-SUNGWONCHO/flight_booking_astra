"""사용자가 별도로 요청한 일반석 재시험 1회. 실패 후 자동 호출하지 않는다.

이전 unknown/permit 원문을 감사 기록에 보존하며 서버 해제 확인으로 바꾸지 않는다.
기존 전송권을 계속 점유하다 새 전송 직전에 교체한다. 같은 이전 runId는 한 번만 승인.
"""
from dataclasses import dataclass,field
import json
import os
from pathlib import Path
import re
import hashlib
import permit


@dataclass(repr=False)
class Claim:
    root: Path=field(repr=False)
    previous: dict=field(repr=False)
    previous_id: str
    used: bool=False


def claim(root, *, previous_id, day, target, pending, at, resume_preparation=False,
          after_handoff_failure=False, after_checkout=False):
    root=Path(root)
    if not re.fullmatch('[a-f0-9]{12}',previous_id or ''):
        raise ValueError('invalid-previous-run')
    held=permit.existing_permit(root)
    if resume_preparation:
        audit=root/'retry-history'/previous_id
        saved=json.loads((audit/'previous.json').read_text(encoding='utf-8'))
        auth=json.loads((audit/'authorization.json').read_text(encoding='utf-8'))
        current=json.loads((root/f'order-intent-{day}.json').read_text(encoding='utf-8'))
        saved_intent=saved.get('intent',{})
        eligible_saved=(saved_intent.get('state')=='unknown' or
            (auth.get('afterCheckout') is True and saved_intent.get('state')=='ordered'
             and saved_intent.get('handoff')=='npay-checkout'
             and saved_intent.get('paymentWindowReached') is True) or
            (auth.get('afterHandoffFailure') is True and saved_intent.get('state')=='ordered'
             and saved_intent.get('handoff')=='bridge-exception'
             and saved_intent.get('paymentWindowReached') is False))
        clean_current=(current==saved_intent or
            (current.get('day')==day and current.get('state')=='prep-no-unblocked-order'))
        if ((pending and pending!={day:saved_intent}) or not held or held!=saved.get('permit')
                or held.get('runId')!=previous_id or held.get('day')!=day
                or held.get('target')!=target or target.get('family')!='KEBONUSEY'
                or not eligible_saved
                or saved['intent'].get('runId')!=previous_id
                or auth.get('previousRunId')!=previous_id or auth.get('target')!=target
                or auth.get('kind')!='user-requested-single-rehearsal'
                or not clean_current):
            raise ValueError('resume-context-mismatch')
        # 같은 준비 상태로 두 번 재개하지 않는다. 새 준비가 무전송 종료된 경우만 새 기록.
        fingerprint=hashlib.sha256(json.dumps(current,sort_keys=True).encode()).hexdigest()[:24]
        resume=audit/'preparation-resume'/fingerprint
        resume.mkdir(parents=True,exist_ok=False)
        permit.durable_json(resume/'authorization.json',{'at':at,'previousRunId':previous_id,
            'target':target,'preparation':current,'kind':'user-requested-preparation-resume',
            'serverReleaseVerified':False,'previousOutcome':saved_intent.get('state')})
        return Claim(root,held,previous_id)
    previous=pending.get(day,{})
    if after_checkout and after_handoff_failure:
        raise ValueError('conflicting-retry-kind')
    checkout=(after_checkout is True and previous.get('state')=='ordered'
        and previous.get('handoff')=='npay-checkout' and previous.get('paymentWindowReached') is True
        and previous.get('paymentAmountsMatched') is True)
    eligible=(previous.get('state')=='unknown' and not after_handoff_failure and not after_checkout) or checkout or (
        after_handoff_failure is True and previous.get('state')=='ordered'
        and previous.get('handoff')=='bridge-exception'
        and previous.get('paymentWindowReached') is False
        and previous.get('paymentAmountsMatched') is True)
    if (set(pending)!={day} or not eligible
            or pending[day].get('runId')!=previous_id or not held
            or held.get('runId')!=previous_id or held.get('day')!=day
            or held.get('target')!=target or target.get('family')!='KEBONUSEY'):
        raise ValueError('retry-context-mismatch')
    audit=root/'retry-history'/previous_id
    audit.mkdir(parents=True,exist_ok=False) # 같은 요청 중복 실행을 원자적으로 거부한다.
    permit.durable_json(audit/'previous.json',{'intent':pending[day],'permit':held})
    permit.durable_json(audit/'authorization.json',{'at':at,'previousRunId':previous_id,
        'target':target,'kind':'user-requested-single-rehearsal',
        'serverReleaseVerified':False,'previousOutcome':previous.get('state'),
        'afterHandoffFailure':after_handoff_failure,'afterCheckout':after_checkout})
    return Claim(root,held,previous_id)


def transfer(claim, *, run_id, day, target, at):
    if type(claim) is not Claim or claim.used:
        return None
    claim.used=True
    if permit.existing_permit(claim.root)!=claim.previous:
        return None
    if day!=claim.previous['day'] or target!=claim.previous['target']:
        return None
    # 최초 실행과 준비 재개 실행이 같은 이전 permit을 읽어도 한쪽만 전송한다.
    try:
        (claim.root/'retry-history'/claim.previous_id/'transfer-consumed').mkdir(exist_ok=False)
    except FileExistsError:
        return None
    if permit.existing_permit(claim.root)!=claim.previous:
        return None
    # old permit을 unlink하지 않는다. 다른 일반 acquire는 교체 전후 모두 거부된다.
    permit.durable_json(permit.permit_path(claim.root),{'day':day,'runId':run_id,
        'pid':os.getpid(),'at':at,'target':target,'retryOf':claim.previous_id,
        'meaning':'order-send-permit; explicit retry; never auto-removed'})
    return permit.Permit(permit.permit_path(claim.root),run_id)
