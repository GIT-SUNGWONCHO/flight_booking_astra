"""D3 발사 판정 연결: 캡처 본문 → A2 조회 → A3 운임 → A4a 필수 검증 → A4b 주문 준비 → A4c 응답.

전송은 하지 않는다. 호출자(live_order)가 이 모듈이 만든 본문을 보내고 결과를 넣는다.
각 단계는 기존 모델(availability·fare·eligibility·travellers)의 판정을 그대로 쓴다.

검증 증거의 출처와 한계
  회원·승객·연락처: 이번 실행의 준비 통과가 사이트의 승객·연락처 확인을 지나 주문 요청까지
    나갔다는 관측(site validateMember 통과로 추정). 캡처 여정 기준이며 목표 여정에 대한
    회원 검증이 아니다.
  세션: 같은 발사의 조회·운임 응답이 HTTP 200 과 기대 구조로 판정됐다는 사실.
  잔액: 서버에서 읽는 계약이 이 PC 자료에 없다. 사용자가 확인해 넣은 본인 가용 마일리지만
    쓰며, 없으면 `mileage-unverified` 로 주문하지 않는다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import time
from uuid import uuid4

import availability
import eligibility
import fare
import travellers
from availability import Target

# 로컬 신선도 정책(초). 조회 요청 생성→응답 판정, 선택→운임 판정·주문 준비에 쓴다.
# 서버 토큰 유효기간이 아니다. 9/13 실측 조회 2.1초·운임 1.4초보다 넉넉히 둔다.
FRESH_MAX_AGE = 30.0
SESSION_EXPIRED = (401, 403)


@dataclass(frozen=True)
class MemberEvidence:
    """이번 실행 준비 통과에서 얻은 회원·승객·연락처 확인 관측.

    어느 승객(travellerId 지문)·노선·날짜·등급에서 확인됐는지를 함께 담는다.
    회원 검증이 여정에 따라 달라지는지는 미확인이라, 날짜·등급이 목표와 다르면
    reuse_itinerary 가 명시적으로 켜졌을 때만 쓴다. 승객·노선이 다르면 쓰지 않는다.
    """
    observed: float
    expires: float
    reached_order_request: bool
    traveller_digest: str = field(default='', repr=False)
    origin: str = ''
    destination: str = ''
    date: str = ''
    family: str | None = None
    reuse_itinerary: bool = False


def traveller_digest(order_body):
    """주문 본문의 travellerId 목록 지문. 원문은 담지 않는다. 읽을 수 없으면 None."""
    try:
        data = json.loads(order_body or '')
        ids = [t.get('travellerId') for t in data['travellerInfoList']]
    except (ValueError, TypeError, KeyError, AttributeError):
        return None
    if not ids or not all(isinstance(i, str) and i.strip() for i in ids):
        return None
    return hashlib.sha256(json.dumps(sorted(ids)).encode('utf-8')).hexdigest()


def member_evidence_from_capture(award_body, order_body, *, observed, expires,
                                 reached_order_request, family, reuse_itinerary=False):
    """캡처 조회·주문 본문에서 확인 문맥을 읽어 회원 증거를 만든다. 읽지 못하면 None."""
    digest = traveller_digest(order_body)
    try:
        seg = json.loads(award_body or '')['segmentList'][0]
        origin, destination, raw = seg['departureAirport'], seg['arrivalAirport'], seg['departureDate']
    except (ValueError, TypeError, KeyError, IndexError):
        return None
    if (digest is None or not all(isinstance(v, str) for v in (origin, destination, raw))
            or len(raw) < 8 or not raw[:8].isdigit()):
        return None
    date = f'{raw[:4]}-{raw[4:6]}-{raw[6:8]}'
    return MemberEvidence(observed, expires, bool(reached_order_request), digest,
                          origin, destination, date, family, bool(reuse_itinerary))


@dataclass(frozen=True)
class BalanceEvidence:
    """사용자가 확인한 본인 가용 마일리지. 서버 검증이 아니다."""
    observed: float
    expires: float
    miles: object = field(default=None, repr=False)
    source: str = 'user-confirmed'


def _finite(*values):
    return all(type(v) in (int, float) and math.isfinite(v) for v in values)


def http_state(result):
    """전송 결과를 판정 전에 분류한다. None 이면 판정기로 넘긴다."""
    if type(result) is not dict or result.get('ok') is not True:
        return 'fetch-failed'
    status = result.get('status')
    if status in SESSION_EXPIRED:
        return 'session-expired'
    return None


class Pipeline:
    """한 번의 발사를 위한 판정 문맥. 상태는 순서대로만 진행한다."""

    def __init__(self, target, *, member, balance, clock=time.monotonic,
                 max_age=FRESH_MAX_AGE):
        if type(target) is not Target:
            raise ValueError('invalid-target')
        self.target = target
        self.member = member
        self.balance = balance
        self.clock = clock
        self.max_age = max_age
        # 서버 세션이 아니라 이 실행 안의 문맥 표지다.
        self.session = uuid4().hex
        self.subject = uuid4().hex
        self.award = None
        self.selection = None
        self.fare_request = None
        self.quote = None
        self.order_request = None
        self.state = 'new'

    # --- A2 조회 ---
    def award_body(self, captured_body, headers):
        """캡처 조회 본문의 날짜·공항만 목표로 바꾼 본문. (본문, 이유)"""
        try:
            template = json.loads(captured_body or '')
        except (ValueError, TypeError):
            return None, 'captured-award-not-json'
        try:
            self.award = availability.build_request(self.target, template, dict(headers or {}),
                                                    self.session, self.clock())
        except ValueError as exc:
            return None, str(exc)
        self.state = 'award-built'
        return json.dumps(self.award.body, ensure_ascii=False), None

    def judge_award(self, result):
        if self.award is None:
            return self._stop('award-not-built')
        early = http_state(result)
        if early:
            return self._stop(early)
        verdict = availability.judge(self.award, result.get('status'), result.get('body'),
                                     self.award.generation, self.session, self.clock(),
                                     max_age=self.max_age)
        if verdict.state != 'selected':
            return self._stop(verdict.state)
        self.selection = verdict.selection
        self.state = 'selected'
        return self.state

    # --- A3 운임 ---
    def fare_body(self, captured_body, headers):
        if self.selection is None:
            return None, 'no-selection'
        try:
            template = json.loads(captured_body or '')
        except (ValueError, TypeError):
            return None, 'captured-fare-not-json'
        try:
            # cabinClass 실값은 기록되지 않았다. 등급은 fareFamily 로 판정한다(D3).
            self.fare_request = fare.build_request(
                self.selection, self.award.generation, self.session, self.target, None,
                template, dict(headers or {}), self.clock(), max_age=self.max_age)
        except ValueError as exc:
            return None, str(exc)
        self.state = 'fare-built'
        return json.dumps(self.fare_request.body, ensure_ascii=False), None

    def judge_fare(self, result):
        if self.fare_request is None:
            return self._stop('fare-not-built')
        early = http_state(result)
        if early:
            return self._stop(early)
        verdict = fare.judge(self.fare_request, result.get('status'), result.get('body'),
                             self.fare_request.generation, self.award.generation,
                             self.session, self.target, self.clock())
        if verdict.state != 'validated':
            return self._stop(verdict.state)
        self.quote = verdict.quote
        self.state = 'validated'
        return self.state

    # --- A4a·A4b 주문 준비 ---
    def prepare_order(self, captured_body):
        """필수 검증과 주문 본문 구조를 판정한다. 통과하면 'ready'. 본문은 캡처 원문을 보낸다."""
        if self.quote is None:
            return self._stop('no-quote')
        now = self.clock()
        member, balance = self.member, self.balance
        try:
            template = json.loads(captured_body or '')
        except (ValueError, TypeError):
            # 파서 메시지는 본문 위치를 담을 수 있어 쓰지 않는다.
            return self._stop('captured-order-not-json')
        if type(member) is not MemberEvidence or not member.reached_order_request:
            return self._stop('member-unverified')
        digest = traveller_digest(captured_body)
        if not member.traveller_digest or digest != member.traveller_digest:
            return self._stop('member-evidence-other-passenger')
        if (member.origin, member.destination) != (self.target.origin, self.target.destination):
            return self._stop('member-evidence-other-route')
        if ((member.date, member.family) != (self.target.date, self.target.family)
                and not member.reuse_itinerary):
            return self._stop('member-evidence-other-itinerary')
        if type(balance) is not BalanceEvidence or balance.miles is None:
            return self._stop('mileage-unverified')
        if not _finite(member.observed, member.expires, balance.observed, balance.expires, now):
            return self._stop('invalid-time')
        if member.observed > now or balance.observed > now:
            return self._stop('invalid-time')
        # 증거 묶음은 판정 시점에 만든다. 원천 관측의 만료 중 이른 것을 쓴다.
        evidence = eligibility.Evidence(
            self.quote, self.subject, observed=now, expires=min(member.expires, balance.expires),
            member_eligible=True, session_valid=True, available_mileage=balance.miles)
        try:
            draft = travellers.prepare_draft(self.subject, self.session, template,
                                             identity_checked=True, contact_checked=True)
        except ValueError as exc:
            return self._stop(str(exc))
        try:
            self.order_request = travellers.build_request(
                self.quote, evidence, draft, target=self.target, session=self.session,
                subject=self.subject, fare_generation=self.quote.generation,
                availability_generation=self.quote.availability_generation,
                now=now, max_age=self.max_age)
        except ValueError as exc:
            return self._stop(str(exc))
        self.state = 'ready'
        return self.state

    # --- A4c 주문 응답 ---
    def judge_order(self, result):
        """주문 응답 판정. 전송 뒤이므로 어떤 결과도 주문 미생성의 증거가 아니다."""
        if self.order_request is None:
            return travellers.Outcome('not-ready')
        if type(result) is not dict or result.get('ok') is not True:
            return travellers.Outcome('order-unknown')
        outcome = travellers.judge(self.order_request, result.get('status'), result.get('body'),
                                   quote=self.quote, target=self.target, session=self.session,
                                   subject=self.subject, now=self.clock())
        # 업무 상태까지 대조한다. 관측된 계약은 구간 status=HK 뿐이다(검토 2331c183 P2).
        if outcome.state == 'order-recorded' and outcome.order.segment_status != 'HK':
            return travellers.Outcome('segment-status-unverified', outcome.order)
        return outcome

    def mileage_label(self):
        """게이트 문구 대조용 필요 마일리지 표기. 운임 판정 전이면 None."""
        if self.quote is None:
            return None
        return f'{int(self.quote.mileage):,}'

    def _stop(self, state):
        self.state = state
        return state
