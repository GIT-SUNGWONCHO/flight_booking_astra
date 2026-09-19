# API 예매 명세 (주력)

기준: 2026-09-19. 코드: `api_booking/`. 실행 절차는 [운영](../operations.md), 시험은 [시험](../testing.md),
실측 근거는 [FACTS](../../FACTS.md)와 [results](../results/)를 따른다. 규칙·권한은 [AGENTS](../../AGENTS.md).

## 1. 목표와 현재 도달 범위

정시(09:00 KST)에 새로 열리는 마일리지 좌석을 **API 조회→운임→주문 1건**으로 잡고, 같은 주문을 브라우저 화면에
넘겨 동의·마일리지까지 진행한 뒤 **결제는 사용자가** 한다.

| 범위 | 상태 |
|---|---|
| 달력 준비→API 조회·운임·주문→같은 주문 인계→동의·마일리지 | **실사이트 통과**(9/14 ICN→CDG Npay, 9/19 CDG→ICN 결제수단 직전 정지) |
| 무인 체인(작업 스케줄러 08:20 → 09:00 발사) | 9/19 콜드 스타트·실전에서 동작 확인 |
| 09:00 신규 개방 프레스티지 주문 확보 | **미달성**. 9/19 주문 송신 +2.92초 → business-error(매진 추정) |
| 실제 좌석 확보 시각 | 미확인(주문 송신~응답 6.4초 사이 어딘가) |

## 2. 전체 흐름

```
작업 스케줄러 08:20 ─ api_day.py (체인, 콘솔 창)
  ├─ observer_chain.py  (별도 프로세스, 계측 9233; 실패해도 예매 계속)
  ├─ Chrome 9232 기동(실전은 재기동) ─ dev/astra_browsers.ps1
  ├─ 로그인·캡처 날짜 달력 ─ dev/setup.py  (.env 와이프 스카이패스 계정)
  ├─ (08:38까지 대기)
  └─ live_order.py
       ├─ 시계 측정(NTP, 캐시 없이)
       ├─ 캡처 통과: 달력→날짜→검색→KRW→운임(목표 편)→다음→승객·연락처  [주문 요청 네트워크 차단]
       ├─ 달력 복귀 → 무장 점검(토큰·달력 주소·저장 상태·열린 날짜 조회 KRW/편명)
       ├─ 대기 … 세션 점검(08:50) … 시계 재측정(T-60초) … 인계 결속(T-15초)
       ├─ 발사(09:00 - 선발사): 조회 ─(미개방이면 재조회)→ 운임 → 필수 검증 → 전송권 → 주문 1회
       └─ 인계: 저장 모델 적용 → 게이트 이동 → 같은 주문 확인 → 동의 2개 → 마일리지
              → ICN 출발: Npay 결제창까지 / ICN 도착: 결제수단 직전 정지 + 알림음
```

## 3. 모듈 지도

| 파일 | 역할 |
|---|---|
| `api_day.py` | 무인 체인. 모드 `live`(기본 상태 폴더, 9232 재기동) / `rehearsal`(리허설 상태 폴더). 종료 코드 3이면 마감 전 재준비 |
| `schedule_api_day.ps1` | 작업 스케줄러 1회 등록(로그온 세션·콘솔 창·배터리 조건 해제·깨우기·중복 금지). `-Date`, `-Remove` |
| `live_order.py` | 준비·대기·발사·주문·인계 실행기. 옵션은 §5 |
| `pipeline.py` | 한 발사의 판정 문맥(A2 조회·A3 운임·A4a 필수 검증·A4b 주문 준비·A4c 응답) |
| `availability.py` / `fare.py` | 조회 요청 구성·판정(`selected`/`not-open`/`sold-out`/…) / 운임 요청 구성·판정 |
| `eligibility.py` / `travellers.py` | 회원·잔액·승객 적격 / 주문 요청·응답 판정(금액·여정·HK) |
| `transport.py` | 캡처(현재 문서의 fetch 후킹)와 같은 문서에서의 재전송 |
| `site_drive.py` | 실제 클릭 경로: 캡처 통과, 달력 복귀, 게이트·동의·마일리지·결제수단 |
| `state_bridge.py` / `connected_bridge.py` | 검증 응답으로 sessionStorage 저장 모델 구성 / 결속·사전 점검·적용·게이트 확인 |
| `bridge_guard.py` / `handoff.py` / `resume_gate.py` | 인계 중 재주문 차단 감시 / 게이트 주문 참조 판정 / 기존 게이트 같은 주문 재개 |
| `permit.py` | 배타 전송권·발사 시각 검사·내구 저장 |
| `order_evidence.py` / `evidence.py` | 허용 목록 주문 증거·오류 코드·금액 진단 |
| `order_flow.py` / `recovery.py` / `explicit_retry.py` | 상태 전이 / 실패 응답 메모리 조사 / 사용자 승인 재시험 |
| `payment.py` | 결제 단계 판정 모델 |
| 연구 도구 | `collect.py`·`contracts.py`(수집), `analyze.py`, `deadline.py`(잘못된 마감 가정 포함, 판정에 쓰지 않음), `open_lab.*`(9242 연구 Chrome) |

공용 모듈은 `dev/`에 있다: `setup.py`(로그인·달력), `runtime.py`(`measure_clock`·실행 ID), `astra_browsers.ps1`,
`session_health.py`(토큰 만료), `prepare_currency.py`, `payment_window.py`(결제 제공자 판정), `observer_chain.py`.

## 4. 단계별 규칙

### 4.1 캡처 통과 (주문 없는 준비)
- 이미 열린 **캡처 날짜**(목표와 다른 날, 보통 일반석)로 사이트를 끝까지 통과시켜 조회·운임·주문 요청의 헤더·본문을
  메모리에 잡는다. 사이트의 주문 요청(`inputTravellers`)은 네트워크에서 막는다. 막지 못하면 중단·기록한다.
- **KRW**: 운임 화면 도착 직후 `#currencyBtn`이 KRW가 아니면 KRW를 적용한다. 적용하면 사이트가 달력으로 되돌아가므로
  같은 날짜로 재검색한 뒤 통화만 재확인한다(9/19 실측). 홈에서 새로 검색하면 USD로 돌아간다.
- **운임칸**: `항공편명 KE<편> <등급> N 마일` 라벨 중 **목표 편명**만 고르고, 화면 가운데로 스크롤한 뒤 좌표 클릭한다
  (CDG→ICN은 공동운항 KE5902가 먼저 그려진다).
- 캡처 유효시간 기본 3600초. 무장 뒤 대기 중 캡처가 만료되면 재준비.

### 4.2 무장·세션 점검 (`readiness_check`)
캡처 직후(arm)와 `--health-at`(health)에 실행. 실패하면 **종료 코드 3**(주문 전, 재준비 가능).

| 점검 | 실패 조건 |
|---|---|
| 토큰 | `T` 쿠키 JWT 만료가 발사+300초 이전 |
| 달력 주소 | 상태 인계 모드에서 정확한 `calendar-fare-bonus` URL이 아님 |
| 저장 상태 | 4개 sessionStorage 모델이 인계 조건과 다름(검색조건 날짜는 목표 또는 캡처 날짜만 허용) |
| 열린 날짜 조회 | 캡처 날짜 조회가 세션 오류·통화 KRW 아님 → 3. 목표 편을 못 찾음(`no-target` 등) → **2(설정 오류)** |

### 4.3 시계와 선발사
- `FireClock.measure`: `runtime.measure_clock`(time.windows.com·time.google.com 최소 RTT 표본, 불확실성=RTT/2)을
  **30분 캐시를 비우고** 시작 때와 T-60초에 잰다. 대기·발사 판정은 보정 시각(로컬+오프셋)으로 한다.
- `--pre-fire-ms`: 기본 0, 상한 3000. 불확실성을 모르거나 100ms 초과면 **0으로 강제**. 9/19 실전 100ms 사용.
- 로컬 시계 오프셋은 시간에 따라 수백 ms 움직였다(+60 → −423ms). 발사 직전 재측정이 필수다.

### 4.4 조회 재시도 (시각 기준, 사용자 확정 2026-09-19)
- **신뢰 경계 = 개방 시각 + 시계 불확실성.** 그 전에 **보낸** 조회의 `selected` 아닌 응답은 전부 재시도한다
  (not-open·no-target·business-error·sold-out·세션 오류 포함). 정각 전 부정 응답은 판정 근거가 아니다.
- 경계 뒤에 보낸 조회는 기존 판정을 신뢰한다. 예외: `not-open`(`ERT.10032`)과 `--not-open-shape`로 지정한 관측 형태(매진 제외)는 상한 안에서 재조회.
- 상한: `--open-retry-max` 25, `--open-retry-gap-ms` 150(응답 뒤 간격, 동시 1건), `--open-retry-until-ms` 8000.
  측정 실패 시 경계 여유는 `--unmeasured-clock-margin-ms` 3000.
- 미개방 awardAvailability 응답: **HTTP 200 + `ERT.10032`**, 키 `code,message,status,subMessages,trace,userDefineMap,uuid`, 약 1.24초(9/19 관측).

### 4.5 주문과 전송권
- 주문은 **실행당 1회**. 전송 직전 `order-permit.json`을 O_EXCL로 만들고 의도(`order-intent-<day>.json`)를 `sending`으로 기록한다.
- 응답 판정이 `order-recorded`가 아니면 `unknown`으로 남기고 **재전송하지 않는다**. 전송권·`unknown`이 있으면 이후 모든 실행이 거부된다.
- 예약번호가 없는 `unknown`은 사용자 확인 뒤 `--release-no-reference --release-reason "<사유>"`로 **보관 폴더로 옮긴다**(삭제 아님, 서버 해제 확인 아님). 예약번호가 있으면 거부한다.
- 금액 표기 예외(사용자 승인 2026-09-19): 운임 `amount=0`, 주문 `amount=totalAmount`, 총액·마일리지 일치, KRW이면 이 실행의 목표에서 허용(`observed-zero-to-total`).

### 4.6 인계와 결제 단계 (`--state-bridge --continue-payment --inspect-failure`)
- 결속(bind)은 대기 중 **T-15초**에 정확한 달력 탭에서 잡는다(쿠키·회원 저장값·4개 저장 모델의 기준). 발사→주문→connect는 결속 후 120초 안이어야 한다.
- 주문 응답 신선도 상한 90초(`state_bridge.RESPONSE_MAX_AGE`).
- connect: 검증 응답으로 `fareInformation`·`inputTravellers` 저장 모델 적용 → 게이트 이동 → 같은 주문 참조·날짜·마일리지·KRW 표시 확인.
- `payment_pass`: 동의 2개(각 모달 상태 확인) → 마일리지(`ensure_mileage`) → 방향별:
  - **ICN 출발**: Npay 선택 → 결제하기 → 새 Npay 창·금액·같은 주문 참조 확인 → `npay-checkout`.
  - **ICN 도착**: 게이트 기본값이 한국발행 신용/체크카드. 주문 재판정 뒤 **결제수단 직전 정지**(`user-payment-method`), 알림음. 현대카드·결제하기는 사용자.
- 인계 실패는 재주문 없이 조사 모드(메모리 보존, `summary`/`diagnose`/`resume`/`exit`)로 간다.

## 5. `live_order.py` 주요 옵션

| 옵션 | 뜻 |
|---|---|
| `--date` `--origin` `--destination` `--flight` `--family` | 목표. `KEBONUSPR` 프레스티지 / `KEBONUSEY` 일반석 |
| `--capture-date '09월 13일'` `--capture-iso 2027-09-13` `--capture-cabin 일반석` | 캡처 날짜(라벨·ISO 일치 필수) |
| `--own-mileage N` `--reuse-member-check` | 사용자 확인 잔액(없으면 주문 안 함) / 캡처 회원 검증 재사용 |
| `--at HH:MM:SS` `--health-at` `--late-limit 3` | 발사 시각 / 세션 점검 / 늦은 기상 허용 |
| `--pre-fire-ms` `--open-retry-*` `--not-open-shape` `--unmeasured-clock-margin-ms` | §4.3·§4.4 |
| `--state-bridge --continue-payment --inspect-failure` | 인계·결제 단계·실패 조사 |
| `--state-dir` | 리허설 전용 상태 폴더(기본 `dev-shots/state` 금지) |
| `--observe-date` `--observe-count` | 리허설 부수 관측: 미개방 날짜 조회 형태 기록 |
| `--dry` | 운임·필수 검증까지, **주문 직전 중단**(캡처는 수행) |
| `--status` / `--release-no-reference` | 상태 읽기 / 예약번호 없는 unknown 보관 |

종료 코드: 0 완료(또는 사용자 차례), 2 거부·실패(주문이 나갔을 수 있음 → 재실행 금지), **3 주문 전 준비 무효(재준비 가능)**.

## 6. 기록·증거 (Git 제외 `dev-shots/`)

| 경로 | 내용 |
|---|---|
| `state/order-intent-<day>.json` | preparing / prep-no-unblocked-order / sending / ordered / unknown, runId, 진단(오류 코드 포함) |
| `state/order-permit.json` | 전송권(날짜 무관). 자동 삭제 안 함 |
| `state/order-evidence/<runId>/received.json` | 허용 항목: pnr·orderId·목표·HK·통화·총액·마일리지·요청/수신 시각·승객 지문·진단·**오류 코드·메시지(정제)** |
| `state/fire-timing-<day>-<id>.json` | 시계 측정들·선발사·신뢰 경계·조회 시도별 보정 송수신 시각·판정·응답 형태 |
| `state/released/<day>-<runId>-<ts>/` | 보관된 unknown·전송권과 사유 |
| `state-rehearsal/<runId>/` | 리허설 상태 폴더(같은 구조) |
| `api-day/<mode>-<runId>.json`, `*-console.log`, `observer-<runId>.log` | 체인 보고서·콘솔 사본·계측 체인 로그 |

저장하지 않는 것: 응답 원문·쿠키·토큰·pageTicket·승객 원문·trace/uuid. 식별자는 일반 문서·터미널에 옮기지 않는다.

## 7. 알려진 한계·다음 과제

1. 09:00 경쟁: 주문 송신 +2.8~3.1초(조회 1.4~1.7초 + 운임 1.4초 직렬). 9/19 프레스티지는 +2.7~5.05초에 사라졌다 → 조회를 앞당기는 선발사 값, 조회·운임 단축 연구.
2. 좌석 확보 시각을 좁히는 **좌석 수 계측기**(9233에서 awardAvailability `seatCount` 0.3초 폴링) — [계측 명세 §5](observer.md#next).
3. 9/19 business-error 원인 미확정(오류 코드 수집은 이후 추가).
4. ICN 도착 현대카드 자동 선택은 미구현(의도적 정지).
5. 연구 도구(`collect`·`analyze`·`deadline`)는 운영 체인과 분리 유지. 폴더 재배치는 9/25 이후([README §5](../../README.md#layout)).
