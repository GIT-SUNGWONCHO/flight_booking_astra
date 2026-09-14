# 클로드 API 개발 진행 기록

이 파일은 클로드의 단계별 작업 기록이다. 사용자 지시(2026-09-13)에 따라 NOW·README·FACTS·API README에 섞지 않았고, 기존 문서 반영·정리는 아스트라가 맡는다. **실행 지시나 준비 완료 판정이 아니다.** 개발 원본은 [API README §3~§6](README.md#process)이다.

---

## D1 동일 주문 인계 계약 — 2026-09-13 23:31 KST 작성, 09-14 00:07 아스트라 검토 반영, T1 로컬

### 1. 결론

| 항목 | 상태 |
|---|---|
| 새 주문과 이전 주문 구분, 같은 날짜·마일리지라도 다른 참조면 거부 | **T1 로컬 통과.** 합성 판정 시험 + 가짜 브라우저 + 격리 headless Chromium |
| 인계 중 새 주문 요청 차단·실패 처리 | **T1 로컬 통과.** 격리 Chromium에서 fetch·XHR·sendBeacon 주문 요청이 네트워크에 도달하지 않음 |
| 실사이트에서 API 주문 뒤 게이트가 **그 주문**을 띄우는지 | **미관측.** 성공 미입증. 현재 실제 인계는 `reference-unobserved`나 `other-order`로 실패할 수 있다 |
| 인계 경로 확정(연속 반영/정상 재개) | **미확정.** `goto(GATE)`는 여전히 확정 경로가 아니다 |
| 달력 시작·동의·마일리지·실제 Npay | D2·D5 범위. 이번에 손대지 않음 |

이번 작업의 실사이트 요청 0회, 주문 0회, 로그인·결제창 조작 0회.

### 2. 근거 대조

| 확인 대상 | 근거 | 결과 | 한계 |
|---|---|---|---|
| 주문 응답 → 게이트가 쓰는 주문 참조 | [수집 f66cf6b8](../dev-shots/api-capture/20260912-232135-f66cf6b8/contracts.json) rows | 정상 UI 주문 2회 모두, 게이트 문서가 `inputTravellers` 응답을 받고 0.090초·0.085초 뒤 보낸 `baggagePolicies` **GET** 요청(행 20·64)의 쿼리 `orderId`가 응답 `pnr`과 같은 참조(v3→v3, v6→v6). 두 주문의 참조는 서로 다름 | 참조는 실행 내 HMAC([contracts.py:93-94](contracts.py:93))라 같은 참조 = 같은 문자열. 사이트가 스스로 만든 주문에서만 관측 |
| 같은 순간의 다른 결제 요청 | 같은 수집 | `GetAvailablePaymentType` 요청은 `currency`·`mode`만(생략 필드 0) → 주문 참조 없음. GiftCardList·GetPaymentAlert·PlccSearch도 참조 필드 미관측 | 허용목록 밖 필드는 기록하지 않음(searchMemberCreditCard 생략 1) |
| 정상 호출 순서 | 같은 수집 | fareInformation → searchFamilyInfoList → bookingToCabin → getReservationAirport·discountPtc·allowUmnrAge → searchMemberSubscriptions → (사용자 입력 약 38~94초) validateMember → inputTravellers → GetAvailablePaymentType·GiftCardList·baggagePolicies·GetPaymentAlert → PlccSearch·searchMemberCreditCard | 주문 요청은 게이트의 승객·연락처 확인 뒤에 나갔다(2/2). 마운트만으로 나가는지는 모름 |
| pnr → NaverPay `reservationRecLoc` | [보관 API 문서 §2.2](../docs/archive/2026-09-13/before-api-README.md) 9/10 수집 113950 | 문서 기록만 있음 | 원본이 이 Mac에 없다. D1 판정에 쓰지 않았다(D5 후보) |
| 게이트 앱 소스(store·action·라우팅·orderId 출처) | [eafd682f 보존 JS 4개](../dev-shots/api-capture/20260912-223915-eafd682f/) | shell 청크뿐. `inputTravellers`·`pnr`·`orderId`·`payment/gate` 문자열 0건 | **없음.** 9/9~10 소스 위치 기록(`dev-shots/p5-resume-20260910/review.json`)도 이 Mac에 없다 |
| 날짜·마일리지 문구로 주문 구분 가능 여부 | 9/13 08:17 리허설 로그(세션 임시 파일, 저장소 밖. 요약은 [사건 기록 §5](live-attempt-2026-09-13.md)) | 캡처 준비가 09-07 일반석으로 승객·연락처 확인까지 진행(사이트 주문) → API 주문 09-07 KEBONUSEY pnr 발급 → `goto(GATE)` 화면 단서 `09월 07일 (화)`·`35,000 마일`·`325,500 원`. **두 주문이 같은 날짜·마일리지라 문구로 구분 불가** | 재진입 게이트가 어느 주문이었는지, 재진입 중 주문 요청이 나갔는지 기록 없음 |
| 문서 이동 부작용 | 9/13 08:44:08 실행 로그(세션 임시 파일) `무장 전에 캡처가 사라졌다: [awardAvailability, fareInformation, inputTravellers]`, [사건 기록 §5](live-attempt-2026-09-13.md) 08:55 | `add_init_script` 후킹이 새 문서마다 빈 상태로 설치돼 캡처가 사라짐. 게이트 문서에 멈춰 있어야만 발사 가능 | `goto(GATE)`가 게이트 앱 상태를 어떻게 복원하는지 미확인 |

수집 파일 자체의 불일치: `configuration.port`는 9232인데 `scope` 문구는 "9242 정상 UI 수동 관측"으로 남아 있다. 이번 판단에는 요청 구조·참조만 사용했다.

### 3. 이번에 정한 계약과 코드 반영

- **화면 주문 참조:** 인계를 시작한 뒤 **인계 대상 페이지의 주 프레임이 이동한 문서**에서 나온 `baggagePolicies` **GET 요청 쿼리**의 `orderId`(값 1개). 출처 오리진은 `https://www.koreanair.com`. 다른 메서드·본문·중복 파라미터는 읽지 않고 실패로 둔다. 다른 탭·이동 전 문서의 참조는 세기만 하고 판정에 쓰지 않는다. 값만 메모리로 읽으며 원문은 로그·파일에 쓰지 않는다.
- **인계 중 주문 요청:** `context.route`로 `inputTravellers`를 abort한다. 주문·선택 요청은 모든 탭을 본다. 막았어도 인계는 실패다. 막지 못한 요청이 보이면 `extra_order_possible=True`. 결제하기 클릭 뒤에도 다시 판정한다.
- **게이트 `matched`:** 주문 참조 판정 `same-order-reference` **그리고** 날짜·마일리지·KRW 문구 일치. 문구는 필요조건일 뿐이다.
- **`same-order-reference`의 의미:** 참조 일치뿐. `screen_state_verified`·`payment_window_reached`는 계속 False다.
- 기존 A6 `prepare()`·`compare()`는 바꾸지 않았다. `compare()`에 들어갈 `order_reference`의 출처는 게이트 단계에서만 정해졌고, 결제 세션 단계 출처는 미정이다.

판정 상태(우선순위 순). 인계 시작 전 기록은 이전 화면의 것이라 세지 않는다.

| 상태 | 조건 | 결과 |
|---|---|---|
| `missing-reference` / `invalid-time` / `handoff-before-order` / `invalid-observation` | 참조 없음, 시각 오류, 관찰이 주문 응답보다 먼저 시작, 기록 형식 오류 | 실패 |
| `new-order-requested` / `new-order-blocked` | 인계 중 `inputTravellers` 요청(미차단 포함 / 전부 차단) | 실패 |
| `selection-request` | 인계 중 `awardAvailability`·`fareInformation` 요청 | 실패 |
| `reference-unreadable` | 참조 요청 본문을 읽지 못함 | 실패 |
| `other-order` | 참조가 하나라도 이번 pnr과 다름 | 실패 |
| `reference-unobserved` | 대상 페이지 이동 뒤 사이트 오리진의 일치 참조가 없음 | 실패 |
| `same-order-reference` | 위에 해당하지 않고 일치 참조 1개 이상 | 참조 일치만 |

### 4. 변경 파일

| 파일 | 변경 | SHA256 앞 16자리 |
|---|---|---|
| [handoff.py](handoff.py) | `reference_in`(GET 쿼리), `ScreenOrder`, `judge_screen_order`(대상 페이지·이동 문서 한정) 추가. 기존 A6 코드 무변경 | `115e8e3dffcc1fe8` |
| [site_drive.py](site_drive.py) | `HandoffWatch`(요청 관찰·주문 요청 abort·대상 페이지 이동 추적·해제), `open_gate`(예외 시 해제) 추가. `gate_handoff`·`payment_pass`가 주문 참조 판정을 요구. `payment_pass(navigate=False)`(= `--payment-only`)는 누르지 않음. 결제하기 직전·직후 재판정. `agree_with_modal` 결함은 그대로(D5) | `ced3eb8fb334f376` |
| [live_order.py](live_order.py) | 주문 응답 시각·pnr을 메모리로 `gate_handoff`·`payment_pass`에 전달. `--gate-only` 성공 문구를 참조 일치로 축소. `--dry`·`--payment-only` 도움말 정정. 조회·운임·주문 전송·캡처·무장 로직 무변경 | `971f01807e413a76` |
| [test_handoff.py](test_handoff.py) | 화면 주문 판정·참조 읽기 시험 17개 추가 | `098933864876ae13` |
| [test_site_drive.py](test_site_drive.py) | 신규. 가짜 컨텍스트·페이지로 관찰기·게이트 인계·결제 단계 거부 22개 | `5fe35c8d8f5b959a` |
| [test_site_drive_browser.py](test_site_drive_browser.py) | 신규. 격리 headless Chromium, 모든 요청 로컬 응답·DNS 전부 실패 설정. route 차단·해제·GET 참조·다중 탭 7개 | `a34ecb4517226497` |
| [test_live_order.py](test_live_order.py) | `--dry` 주문 가능 경로 확인 2개 추가 | `641134389d8d95b2` |

`transport.py`는 변경하지 않았다(`d02927a7483b4c15`). 모든 파일은 미커밋이다.

### 5. 시험 명령과 결과

Mac Python 3.11.7, Playwright 1.62.0. 모두 exit 0.

```bash
./.venv/bin/python api_booking/test_handoff.py
./.venv/bin/python api_booking/test_site_drive.py
./.venv/bin/python api_booking/test_site_drive_browser.py
./.venv/bin/python api_booking/test_live_order.py
```

```powershell
# Windows 명령. 이번에 실행하지 않았다.
.\.venv\Scripts\python.exe api_booking\test_handoff.py
.\.venv\Scripts\python.exe api_booking\test_site_drive.py
.\.venv\Scripts\python.exe api_booking\test_site_drive_browser.py
.\.venv\Scripts\python.exe api_booking\test_live_order.py
```

| 시험 | 결과 |
|---|---|
| test_handoff | 39개 통과(기존 22 + 신규 17) |
| test_site_drive | 22개 통과(신규) |
| test_site_drive_browser | 7개 통과(신규, 약 13초) |
| test_live_order | 16개 통과(기존 14 + 신규 2) |
| 회귀: analyze 4 · availability 8 · collect 12 · collect_browser 1 · deadline 13 · eligibility 8 · fare 10 · order_flow 7 · payment 24 · travellers 23 | 모두 통과 |

변이 검사(스크래치 복사본, 저장하지 않음): 문구만으로 통과(M1), 인계 전 기록 포함(M2), 주문 요청 abort 제거(M3), 결제 직전 재판정 제거(M4), 다른 참조 거부 제거(M5), 대상 페이지 제한 제거(M6), 결제 클릭 뒤 재판정 제거(M7), 이동 예외 시 해제 제거(M8), POST 본문 참조 인정(M9)을 각각 넣으면 관련 시험이 실패했다.

격리 Chromium 참고 관측: `sendBeacon`으로 보낸 주문 요청도 route에서 막혀 픽스처에 도달하지 않았다(`new-order-blocked`). `request.frame is page.main_frame` 식별이 실제 Playwright에서 동작했다. 실제 9232 CDP 연결·서비스 워커 조건과 다르다.

### 6. 캡처 준비와 `--dry`도 주문을 만들 수 있음 — 확인

아래 줄 번호는 D1 당시 코드 기준이다. 이 동작은 D2에서 바뀌었다(D2 §5).

- [site_drive.py:151-152](site_drive.py:151) `capture_pass`는 게이트에서 `submit-passenger-ADT-0`·`submit-contact`를 누른다. 정상 UI 수집에서 이 확인 뒤 `inputTravellers`와 pnr이 관측됐고, 9/13 아침 캡처 통과 임시 보유 기록이 있다([FACTS §1](../FACTS.md#api-evidence)).
- [live_order.py:228-232](live_order.py:228)의 캡처 준비는 [live_order.py:332](live_order.py:332) `if a.dry:` 중단보다 앞에 있다. 캡처가 부족하고 `--capture-date`가 있으면 `--dry`도 주문을 만들 수 있다.
- [live_order.py:192](live_order.py:192) `if pending and not a.dry:` 때문에 `--dry`는 미해결 주문 검사를 건너뛴다. 미해결 주문이 있어도 캡처 준비로 주문을 더할 수 있다. 캡처 준비의 주문은 intent 파일에 남지 않는다.
- `DryRunIsNotOrderFreeTests` 2개가 이 경로를 가짜 Playwright로 재현한다. **고치지 않았다.** 무주문 준비 설계는 D2, 전송권·의도 기록은 D4 범위다. 고치면 이 시험의 기대값을 바꾼다.

### 아스트라 검토 — `20260914-000053-d2574cfe`

gpt-6-astra, 미커밋 51개 범위 중 D1 지정, `ok=true`·`scopeStable=true`. 읽기·정적 분석만이며 시험·브라우저 실행은 하지 않았다.

| 지적 | 판단 | 조치 |
|---|---|---|
| P1 `baggagePolicies`는 GET인데 POST 본문을 읽음 | **채택.** 수집 행 20·64 `method=GET`, 수집기는 GET 쿼리를 구조로 기록([collect.py:25-27](collect.py:25)). 원래 구현이면 실제 같은 주문도 `reference-unreadable`로 거부됐다 | `reference_in(method, url)`로 GET 쿼리만 읽음. 시험·문서 정정 |
| P1 컨텍스트 전체 요청을 참조로 인정해 다른 탭 참조로 통과 가능 | **채택** | 참조는 대상 페이지 주 프레임·관찰 뒤 이동한 문서만 인정. 주문·선택 요청은 모든 탭 유지. 가짜·격리 Chromium 다중 탭 반례 추가 |
| P1 결제하기 클릭 뒤 주문 요청이 실패로 반영 안 됨 | **채택** | 클릭·대기 뒤 재판정, 실패 시 `matched=False`·`extraOrderPossible` 갱신 |
| P2 게이트 이동 중 예외 시 route·listener 잔존 | **채택** | `open_gate`가 예외 시 해제 후 다시 던짐. 해제 시험 추가 |

재검토는 하지 않았다. 수정분은 위 시험·변이 검사로만 확인했다.

### 7. 미확인·한계

1. **실사이트 게이트 재진입:** API 재전송 주문 뒤 `goto(GATE)`에서 `baggagePolicies`가 나오는지, `orderId`가 새 pnr인지, 재진입 중 주문·운임 요청이 나가는지 관측하지 않았다.
2. 게이트 앱 소스가 없어 `orderId`를 어디서 복원하는지(sessionStorage·store·서버 세션) 모른다.
3. `baggagePolicies.orderId`가 화면의 여정·금액·결제 세션 전체와 같은 주문이라는 것은 정상 UI 2회 관측에 기댄 추론이다. NaverPay 참조 연결은 원본 없는 문서 기록이다. 게이트 SPA가 주 프레임이 아닌 iframe에서 참조 요청을 보내면 `reference-unobserved`로 실패한다(정상 수집은 프레임 정보를 기록하지 않았다).
4. 9232 CDP 연결에서의 `context.route` 동작(서비스 워커 경유 요청, 가로채기 지연)을 검증하지 않았다. route가 켜진 동안 모든 요청이 파이썬 매칭을 거치므로 게이트 로딩이 느려질 수 있으며 측정하지 않았다.
5. 관찰기는 이 프로세스가 연결한 브라우저 컨텍스트만 본다. 다른 프로필·브라우저의 주문은 모른다.
6. Windows 실행 미검증. 표준 라이브러리·pathlib·Playwright만 사용했다.
7. 9/13 이전 시험 주문의 서버 상태는 확인하지 않았다.
8. ke-review(아스트라 검토)는 실행하지 않았다. 커밋 전에 필요하다.

### 8. 다음에 수집할 증거 하나

**API 재전송 주문 직후 게이트 재진입 1회에서 `gate_handoff`의 판정 결과**(`order` 상태, 참조 일치/관측 수, 주문·선택 요청 수. 원문 없음). 주문이 생기므로 사용자 별도 허용과 이전 주문 상태 확인이 먼저다. 현재 준비 방식이면 캡처 준비 주문까지 최대 2건이다. README §5 순서상 D3/D4 뒤 T2b에서 수집하며, 사용자가 앞당겨 허용할 때만 먼저 한다.

### 9. 정확한 다음 작업 하나

**D2 첫 조각: 캡처를 게이트 문서에 두지 않고 달력 문서에서 발사할 수 있는 준비 방식 결정과 로컬 시험.** 9/13 08:44:08 로그의 캡처 소실과 08:55 사건 기록(게이트 문서에 멈춰야만 발사 가능)을 출발점으로, 요청 문맥(헤더·본문)을 문서 전환 너머로 보존할지 명확히 재준비할지 정한다. 주문 없는 준비가 가능한지(특히 `inputTravellers` 본문 확보)도 함께 조사한다.

---

## D2 달력 시작 준비 — 2026-09-14 00:17 KST 작성, 00:26 아스트라 검토 반영, T1 로컬

### 1. 결론

| 항목 | 상태 |
|---|---|
| 게이트 문서 고정 의존성 제거 | **T1 로컬 통과.** 캡처를 프로세스 메모리(`transport.Capture`)로 옮기고, 달력 문서에서 같은 오리진이면 발송. 격리 Chromium에서 새 문서 발송 확인 |
| 문서 전환 뒤 문맥 유지 또는 명확한 재준비 | **T1 로컬 통과.** 발사 캡처는 **이번 실행의 준비 세대**에서 만든 것만 쓴다(`--capture-date` 필수, 세대 시작 시 페이지 캡처 비움·시각 필터). 나이 초과·오리진 변경·달력 경로 이탈·셀 사라짐은 무장 대기·대기 중·정각 60초 안 마지막 확인에서 중단하고, 정각에는 나이·오리진·경로만 가볍게 확인 |
| 준비 중 주문 여부 식별 | **T1 로컬 통과.** 준비 시작 전 intent `preparing` 선기록(프로세스가 죽으면 다음 실행 차단). 캡처 준비·달력 복귀 동안 주문 요청을 route로 차단·계수하고, 예외가 나도 계수를 돌려받아 막지 못했으면 `prep-order-possible` 기록 후 중단. 전부 막았으면 `prep-no-unblocked-order`(로컬 판정, 다음 실행 비차단) |
| 준비/조회/주문 구간 호출 수 구분 | **T1 로컬 통과.** `Ledger`: prep(준비·달력 복귀 중 사이트 요청·차단/미차단·예외), fire(시도·fetch 호출·미발송·예외), handoff(판정 상태·참조·주문·선택 요청 수). 대기 구간의 사이트 요청은 세지 않는다 |
| 실사이트에서 주문 요청 차단 뒤 캡처가 남는지, 달력 문서 발송을 서버가 받는지 | **미관측** |

이번 단계 실사이트 요청 0회, 주문 0회.

### 2. 근거와 설계

- 9/13 08:44:08 로그의 캡처 소실과 08:55 사건 기록(게이트 문서에 멈춰야만 발사 가능)이 출발점이다.
- 9/13 08:17 로그에서 캡처 준비 뒤 게이트 문서에 3구간이 모두 있었다. 달력→항공편→게이트가 한 문서 안에서 진행된 것으로 보이며, 그래서 스냅숏은 준비 통과 직후 게이트 문서에서 한 번 뜬다.
- 페이지 후킹(`transport.HOOK`)은 문자열 본문 요청을 원본 fetch/XHR 호출 **전에** 기록한다. 그래서 네트워크 단계 abort와 캡처가 양립한다. 격리 Chromium에서 fetch 2건·XHR 1건으로 확인했다.
- 발사 캡처는 이번 실행 준비 세대의 것만 쓴다. `begin_generation`이 페이지 캡처를 비우고 페이지 시계 시작 시각을 돌려주며, `snapshot(since=)`이 그 이전 기록을 버린다. 준비 통과가 승객·연락처 확인까지 끝나지 않으면(`capture_complete`) 쓰지 않는다.
- 발송은 `location.origin`이 캡처 오리진과 같을 때만 한다. 원본 fetch(`__KE_TX.origFetch`)로 보내 새 문서의 후킹 캡처를 덮지 않는다.

### 3. 변경 파일

| 파일 | 변경 | SHA256 앞 16자리 |
|---|---|---|
| [transport.py](transport.py) | `Capture`(헤더·본문·URL repr 숨김), `begin_generation`, `snapshot`(완전한 캡처·세대 필터), `send_request`(오리진 대조 후 현재 문서에서 발송) 추가. 기존 `HOOK`·`SEND`·`send_captured` 무변경 | `3c39c0ac76598f20` |
| [site_drive.py](site_drive.py) | `calendar_cells`·`wait_calendar`·`on_calendar`·`return_to_calendar`(차단·계수·예외 반환)·`watch_counts`·`capture_complete` 추가. `capture_pass`가 주문 요청 차단·계수, 예외를 `error`로 반환, 달력 셀 10개 이하면 누르지 않음. 게이트 인계 결과에 요청 수 `counts` | `ca34cb1a5fa65022` |
| [live_order.py](live_order.py) | 미해결 주문 검사를 `--dry`에도 적용. `preparing`·`prep-no-unblocked-order` 상태와 직전 상태 보존. `run`(준비·대기)·`fire`(발사·인계) 분리. 이번 세대 캡처만 사용·달력 복귀·오리진·달력 경로·셀 재확인. `prep_counts_clean`·`send_counted`·`Ledger`. `transport_body` 제거 | `d5d1d71f28732163` |
| [test_live_order.py](test_live_order.py) | D1의 `DryRunIsNotOrderFreeTests`를 `PrepAndFireFlowTests` 15개·`LedgerTests` 2개로 교체 | `5d3ced94e9224f18` |
| [test_site_drive.py](test_site_drive.py) | `CapturePassGuardTests` 6개·`ReturnToCalendarTests` 3개·`OnCalendarTests` 1개 추가 | `42424293ad94100e` |
| [test_transport_browser.py](test_transport_browser.py) | 신규. 격리 Chromium: 차단 중 3구간 캡처·새 달력 문서 발송·세대 비움·세대 이전 기록 무시·다른 오리진 거부 5개 | `3448c0cec7afca7a` |

### 4. 시험 명령과 결과

Mac Python 3.11.7. `api_booking/test_*.py` 15개 파일 모두 exit 0.

```bash
./.venv/bin/python api_booking/test_live_order.py
./.venv/bin/python api_booking/test_site_drive.py
./.venv/bin/python api_booking/test_transport_browser.py
```

```powershell
# Windows 명령. 실행하지 않았다.
.\.venv\Scripts\python.exe api_booking\test_live_order.py
.\.venv\Scripts\python.exe api_booking\test_site_drive.py
.\.venv\Scripts\python.exe api_booking\test_transport_browser.py
```

| 시험 | 결과 |
|---|---|
| test_live_order | 31개 통과 |
| test_site_drive | 32개 통과 |
| test_transport_browser | 5개 통과(약 7초) |
| 회귀(handoff 39·site_drive_browser 7·그 밖 A 계열·수집기) | 모두 통과 |

변이 검사(스크래치): `--dry` 우회 복원, 미차단 준비 주문 무시, 준비 뒤 캡처 병합, 달력 복귀 생략, 오리진 대조 제거, 캡처 준비 차단기 제거, 준비 예외 전파, `preparing` 선기록 제거, 세대 필터 제거, 준비 완료 검사 제거, 달력 재확인 제거, 미발송 계수 오류, 시각 필터 제거를 각각 넣으면 관련 시험이 실패했다.

### 5. D1에서 넘어온 항목 처리

- `--dry`의 미해결 주문 검사 우회: **고침.** 이제 모든 모드에서 거부한다.
- 캡처 준비 주문이 intent에 남지 않음: 차단하지 못한 경우 `prep-order-possible`로 남기고 중단한다. 차단 성공 시에는 주문이 없다고 **로컬에서만** 판단한다.

### 아스트라 검토 — `20260914-001837-165a57e4`

gpt-6-astra, 미커밋 52개 중 D2 지정, `ok=true`·`scopeStable=true`. 정적 검토만.

| 지적 | 판단 | 조치 |
|---|---|---|
| P1 준비 중 미차단 주문 뒤 예외면 `prep-order-possible` 기록 유실 | **채택** | `capture_pass`·`return_to_calendar`가 예외를 `error`로 돌려주고 계수를 보존. 준비 시작 전 `preparing` 선기록으로 프로세스 종료도 차단 |
| P1 SPA 문서에 이전 캡처가 남아 새 조회와 옛 운임·주문이 섞일 수 있음, 준비 성공 여부 미검사 | **채택** | 준비 세대(`begin_generation`·`since`)와 `capture_complete` 검사. 이번 실행 준비 없이 기존 문서 캡처를 쓰는 경로 제거 |
| P2 대기 중 달력 이탈을 발사 전에 재확인 안 함 | **채택** | `still_ready`에 달력 경로·셀 확인. 정각에는 셀 읽기 없이 경로까지만(지연 방지) |
| P2 호출 수가 실제 발송과 어긋남(미발송 계수, 예외 누락, 복귀·인계 요청 미포함) | **채택** | 시도/fetch/미발송/예외 분리, 복귀 구간 관찰, 인계 요청 수 기록. 대기 구간 사이트 요청은 여전히 세지 않음(문서화) |

재검토는 하지 않았다.

### 6. 미확인·한계

1. **실사이트에서 캡처 준비의 주문 요청 차단**(9232 CDP의 route 동작·서비스 워커 경유)을 확인하지 않았다. 사이트가 요청 실패 뒤 다른 경로로 재시도하는지도 모른다.
2. 차단된 주문 요청 뒤 게이트 화면·서버 세션 상태(선택 유지, 오류 표시)가 발사에 영향을 주는지 모른다.
3. 달력 문서에서 보낸 조회·운임·주문을 서버가 게이트 문서에서 보낸 것과 같게 받아 주는지 모른다. 헤더 `x-queueit-ajaxpageurl`은 캡처 문서 URL을 그대로 쓴다(9/13에도 캡처 문서와 발송 문서가 달랐으나 게이트→게이트 조합이었다).
4. `return_to_calendar` 뒤 달력에 검색 조건이 남아 셀이 그려지는지 실사이트에서 확인하지 않았다. 셀이 없으면 중단만 한다.
5. 메모리 캡처의 서버 유효기간은 모른다. 기본 허용 나이 3600초는 9/13 표본 기반 운영값이지 보장이 아니다.
9. 준비 통과는 이제 발사 모드마다 필수다. 실사이트에서 한 번에 약 50초(9/13 로그 08:16:36→08:17:29)가 걸렸고, 무장 전에 끝나야 한다.
10. 정각 발사 직전 셀 확인을 생략하므로, 마지막 확인(60초 안) 뒤 정각 사이의 달력 셀 소실은 경로가 유지되면 잡지 못한다.
6. `replay_probe.py`는 여전히 `send_captured`로 주문을 보낼 수 있는 초기 실험 도구이며 차단기를 쓰지 않는다. 이번에 손대지 않았다.
7. `--at`의 늦은 무장·실행일 결합 결함, 프로세스 간 전송권은 D4 범위로 남아 있다.
8. Windows 실행 미검증.

### 7. 다음 작업

D2 아스트라 검토 → 수정 → D3(실제 계약 대조와 A2~A4 판정 연결).

---

## D3 실제 계약과 검증 연결 — 2026-09-14 00:38 KST 작성, 00:52 아스트라 검토 반영, T1 로컬

### 1. 결론

| 완료 조건(API README §4) | 상태 |
|---|---|
| 잘못된 날짜/노선/편명/등급에서 주문 0회 | **T1 로컬 통과.** 발사가 A2 조회 판정(날짜·출도착·운항사·편명·비공동운항·등급)과 A3 운임 판정(같은 항목+금액·마일리지·pageTicket)을 거쳐야 주문 준비로 간다 |
| 매진/미개방/업무 오류에서 주문 0회 | **T1 로컬 통과.** `sold-out`·`unverified-stock`·`not-open`(ERT.10032)·`business-error`(HTTP 200 오류 필드 포함) |
| 잔액 부족/세션 만료에서 주문 0회 | **T1 로컬 통과.** A4a 필수 검증(`insufficient-mileage`·`mileage-unverified`·`member-unverified`·`expired-evidence`), 401/403→`session-expired`, 로그인 HTML→`invalid-json`, fetch 실패→`fetch-failed` |
| 실제 계약으로 판정이 맞는지 | **부분.** 필드 이름은 9/12 수집 구조·8/27 fixture·9/13 실발사와 대조했지만 실제 값 조합(특히 프레스티지·신규 날짜)은 미관측. 잔액 서버 계약은 없음 |

이번 단계 실사이트 요청 0회, 주문 0회.

### 2. 실측 대조 — 합성 모델과 달랐던 곳

| 모델 | 합성 가정 | 근거 | 조치 |
|---|---|---|---|
| A2 조회 요청 | 템플릿 `currency == 'KRW'` | f66cf6b8 조회 요청 3회 모두 `currency` 값이 KRW/USD가 아닌 문자열(`unrecognizedValue`) | 문자열이면 값 보존, KRW 요구 제거 |
| A2 조회 응답 날짜 | 항공편 `departureDate` 14자리 필수 | 9/12 구조에 항공편 `departureDate`(형식 미기록)·구간 `departureDateTime` 존재. 9/13 실발사 5회는 구간 `departureDateTime` 앞 8자리로 날짜를 맞춤. 8/27 fixture는 항공편 날짜 14자리 | 구간 `departureDateTime` 14자리 필수, 항공편 `departureDate`가 있으면 14자리·같은 날짜 |
| A2 운항사·재고 | `carrierCode`·항공편 `soldOut=false` 필수 | 두 필드는 9/12 허용목록 밖(미기록), 8/27 fixture·`probe.js`에만 있음. `operationCarrierCode`·`codeShare`·등급 `soldout`·`seatCount`는 9/12 관측 | `carrierCode`·`soldOut`은 있으면 대조, 없으면 통과. 구간 공항도 있으면 대조 |
| A3 운임 요청 | 템플릿 `currency == 'KRW'` | f66cf6b8 운임 요청 4회 모두 `recommendList`만(생략 필드 0) | currency가 있을 때만 대조 |
| A3 등급 | 호출자 `cabinClass` 필수 | 응답 `cabinClass` 값 미기록 | `cabin=None`이면 대조하지 않고 `fareFamily`로 등급 판정 |
| A3 업무 오류 | 합성 | 요청 39(옛 식별자 운임 요청)가 HTTP 200 + `code`·`status`·`message` | 기존 오류 필드 판정 유지, 시험 추가 |
| A4b 주문 본문 | `contactList` 1항목 | f66cf6b8 주문 요청 2회 모두 2항목 | 1~2항목 허용, 빈 항목·3항목 거부 |
| A4c 주문 응답 | 금액 없음 가정 | f66cf6b8 주문 응답에 `pnrFareInfo` 있음 | 기존대로 있으면 운임과 대조 |

사용하지 않은 가정: `totalAmount ≥ amount`는 근거가 없지만 모순 관측도 없어 A3 그대로 뒀다.

### 3. 필수 검증 증거의 출처

| 증거 | 출처 | 한계 |
|---|---|---|
| 회원·승객·연락처 확인 | 이번 실행 준비 통과가 승객·연락처 확인을 지나 사이트 주문 요청까지 나감(`capture_complete`·`orderRequests.seen≥1`). 캡처 본문의 travellerId 지문·노선·날짜·등급과 묶음(`member_evidence_from_capture`) | 보낼 주문 본문의 승객·목표 노선이 다르면 거부. 날짜·등급이 다르면 `--reuse-member-check`를 사용자가 켤 때만 사용(회원 검증의 여정 의존성 미확인). 만료는 `--capture-max-age` |
| 세션 | 같은 발사의 조회·운임 응답이 200·기대 구조로 판정 | 주문 시점 세션 보장 아님 |
| 본인 가용 마일리지 | `--own-mileage`(사용자 확인값). 값은 로그에 쓰지 않음 | **서버 검증 아님, 계정과 결속되지 않음.** 잔액을 읽는 사이트 계약이 이 PC 자료에 없다(9/10 `api-source` 검토 원본 없음). 없으면 `mileage-unverified`로 주문하지 않는다 |
| 필요 마일리지 | 이번 발사의 운임 응답 `pnrFareInfo.mileage` | 캡처 때 값이 아니라 새 값과 비교 |

### 4. 변경 파일

| 파일 | 변경 | SHA256 앞 16자리 |
|---|---|---|
| [pipeline.py](pipeline.py) | 신규. A2→A3→A4a·A4b→A4c 연결, `MemberEvidence`(승객 지문·노선·날짜·등급 결속)·`BalanceEvidence`·`http_state`, 주문 응답 구간 HK 요구, 신선도 30초 | `0a81e3f727130fd9` |
| [availability.py](availability.py) | 위 표의 A2 조치 | `16a5cebe55fa290b` |
| [fare.py](fare.py) | 위 표의 A3 조치 | `bf10c1fc819f37b2` |
| [travellers.py](travellers.py) | `contactList` 1~2항목 | `2ed9b5856d237db6` |
| [live_order.py](live_order.py) | `fire`가 pipeline 판정을 거침. `--origin`·`--destination`·`--carrier`·`--own-mileage`·`--own-mileage-valid-hours`·`--reuse-member-check`. 주문 뒤 `order-recorded`(구간 HK 포함)일 때만 인계, 아니면 intent `unknown`. `--dry`는 준비 판정이 `ready`일 때만 exit 0. `--at` 대기 변수(`fire_at`)를 예매 목표와 분리. 옛 간이 판정 제거 | `a808924e11a970b9` |
| [test_pipeline.py](test_pipeline.py) | 신규 15개 | `204d74b9f53b6b02` |
| [test_availability.py](test_availability.py) | 실측 대조 시험 4개 추가, fixture에 관측 필드 보강 | `91161429af29de5a` |
| [test_fare.py](test_fare.py) | 실측 대조 시험 2개 추가 | `db0051e9c6b1517a` |
| [test_travellers.py](test_travellers.py) | 연락처 2항목 허용·3항목 거부 | `1b39993cab08de5a` |
| [test_live_order.py](test_live_order.py) | 옛 간이 판정 시험 제거, `FlowHarness`·`FakeClock` 분리, `FireJudgementTests` 9개 | `df04e0ead91ad98b` |

### 5. 시험 명령과 결과

Mac Python 3.11.7. `api_booking/test_*.py` 16개 파일 모두 exit 0.

```bash
./.venv/bin/python api_booking/test_pipeline.py
./.venv/bin/python api_booking/test_availability.py
./.venv/bin/python api_booking/test_fare.py
./.venv/bin/python api_booking/test_travellers.py
./.venv/bin/python api_booking/test_live_order.py
```

Windows는 같은 파일을 `.\.venv\Scripts\python.exe api_booking\test_<이름>.py`로 실행한다(미실행).

| 시험 | 결과 |
|---|---|
| test_pipeline | 15개 통과 |
| test_availability / fare / travellers / eligibility | 12 / 12 / 24 / 8개 통과 |
| test_live_order | 26개 통과 |
| 회귀(handoff·payment·site_drive·브라우저 시험·수집기 등) | 모두 통과 |

변이 검사(스크래치): 편명 대조 제거, 운임 등급 대조 제거, 필수 검증 무시 주문, 잔액 부족 판정 제거, 등급 soldout 무시, 연락처 개수 제한 제거, 세션 만료 분류 제거, 항공편 날짜 불일치 허용, 발사 시각이 목표를 덮음, 승객 결속 제거, 여정 재사용 명시 제거, HK 검사 제거를 각각 넣으면 관련 시험이 실패했다.

### 아스트라 검토 — `20260914-003938-2331c183`

gpt-6-astra, 미커밋 54개 중 D3 지정, `ok=true`·`scopeStable=true`. 정적 검토만.

| 지적 | 판단 | 조치 |
|---|---|---|
| P1 `--at` 대기에서 발사 시각이 예매 `Target` 변수를 덮어 발사 때 `invalid-target` 예외 | **채택.** 코드 확인: 대기 루프가 `target`에 datetime을 넣었다. 예약 발사가 조회 전에 죽는 결함이었다 | `fire_at` 분리. `FakeClock`으로 `--at` 경로 시험 추가 |
| P1 회원·잔액 증거가 승객·목표 문맥과 결속되지 않음 | **회원: 채택. 잔액 계정 결속: 보류.** 잔액을 서버에서 읽는 계약이 이 PC에 없어 결속할 대상이 없다 | 회원 증거에 travellerId 지문·노선·날짜·등급을 묶고 승객·노선 불일치 거부, 날짜·등급 재사용은 `--reuse-member-check` 명시 때만. 잔액은 사용자 확인값 한계를 명시하고 다음 수집 증거로 둔다 |
| P2 구간 status 누락·미지원 주문도 `order-recorded`로 인계 | **채택** | 관측 계약 HK가 아니면 `segment-status-unverified`로 intent `unknown`, 인계 안 함 |

재검토는 하지 않았다.

### 6. 미확인·한계

1. **실제 값 조합 미관측:** 프레스티지(`KEBONUSPR`)·신규 개방 날짜의 조회·운임·주문 응답을 이 판정기로 받아 본 적이 없다. 8/27 fixture의 `carrierCode`·`soldOut`이 9/12 이후에도 있는지 모른다.
2. **잔액 서버 계약 없음(검토 보류 항목).** `--own-mileage`는 사용자 확인값이고 계정과 결속되지 않는다. 잘못 넣거나 다른 계정 값을 넣으면 부족을 못 막는다. 다음 수집 증거: 정상 준비 통과 중 `searchFamilyInfoList` 응답의 본인 가용 마일리지 필드 이름·형식(값 원문 미저장).
3. 회원 검증 증거는 캡처 여정 기준이다. `--reuse-member-check`를 켜면 목표 날짜·등급에 재사용하므로, 여정별 회원 제한이 있으면 판정이 틀릴 수 있다.
4. 미개방 응답은 달력 API의 ERT.10032만 근거다. 조회 API의 실제 미개방 형태는 모른다(모르는 형태는 주문하지 않음).
5. 신선도 30초는 로컬 정책이다. 09시 부하로 조회 응답이 30초를 넘으면 `stale-context`로 주문하지 않는다.
6. 모델을 실측에 맞춰 느슨하게 한 곳(`carrierCode`·`soldOut` 선택, 요청 currency)은 반대로 실제에 없는 조건을 허용할 수 있다. 등급 `soldout=false`·양수 `seatCount`·`operationCarrierCode`·`codeShare=false`는 여전히 필수다.
7. 발사 경로에 판정이 늘어 T0→주문 전송까지 파이썬 처리 시간이 조금 늘었다(측정 안 함, 로컬 판정은 밀리초 단위로 예상).
8. A1 상태 모델(`order_flow`)은 아직 발사 경로에 연결하지 않았다. D4에서 전송권과 함께 다룬다.
9. Windows 실행 미검증.

### 7. 다음 작업

D3 아스트라 검토 → 수정 → D4(전송권·의도 기록·불명확 복구·늦은 무장 차단).

---

## D4 주문 전송 안전성 — 2026-09-14 00:50 KST 작성, 01:03 아스트라 검토 반영, T1 로컬(Mac)

### 1. 결론

| 완료 조건(API README §4) | 상태 |
|---|---|
| 두 프로세스 동시 시작 중 하나만 전송 | **T1 로컬 통과(Mac).** 주문 요청 직전 **날짜와 무관한 단일** 전송권 파일(`order-permit.json`)을 `O_CREAT\|O_EXCL`로 만든다. spawn 프로세스 6개(실행일 2종 섞음)를 동시에 출발시켜 4회 모두 1개만 획득 |
| 중단/응답 유실/재시작 후 새 주문 차단 | **T1 로컬 통과.** 전송권은 자동으로 풀지 않고, 시작 때 전송권이 있거나 **모든 실행일**의 intent 중 미해결이 있으면 모든 모드 거부(자정 넘긴 재시작 포함). 전송 중 예외·판정 불명은 intent `unknown` + 전송권 유지. 전송권·intent는 파일과 부모 디렉터리까지 `fsync`(POSIX) |
| 늦은 무장 차단 | **T1 로컬 통과.** `--day`+`--at`을 시작 때 고정, 실행일이 오늘이 아니거나 이미 지난 시각이면 시작 거부. 발사 시각까지 무장 안 됨·발사 시각 뒤 무장 확인·허용 지연(기본 3초) 초과 기상은 발사 안 함 |
| 불명확 복구 | **부분.** `--status`가 의도·전송권을 읽기만 하고 할 일을 알린다. 서버 주문 상태 확인과 기록 정리는 사용자 몫이며 프로그램이 대신하지 않는다 |
| **Windows 시험** | **미충족.** 이 Mac에서만 실행했다. 코드는 표준 라이브러리(`os.open` O_EXCL·`O_BINARY`·`fsync`, spawn)만 쓴다 |

이번 단계 실사이트 요청 0회, 주문 0회.

### 2. 설계

- **전송권 파일:** `dev-shots/state/order-permit.json` 하나(날짜 무관). 내용은 실행일·실행 표지·PID·시각·목표(날짜·노선·편명·등급)와 의미 문구. 계정·주문 참조 없음. 쓰고 파일·부모 디렉터리를 `fsync`한다. 읽을 수 없는 파일도 막는다.
- **순서:** 필수 검증 `ready` → 전송권 획득 → A1 `OrderFlow`(READY→ORDER_INTENT→ORDER_IN_FLIGHT) → intent `sending` → 주문 요청 → A4c 판정 → `ordered`/`unknown`. 전송권을 못 얻으면 intent를 건드리지 않고 멈춘다(다른 실행이 소유).
- **실행일:** `--day` 기본값은 오늘(KST)이고, 명시해도 오늘이어야 한다. 전날 밤에 다음 날 `--day`로 미리 띄우는 방식은 거부된다. 9/13 검토 P2(무장한 날의 시각을 새로 구성)를 막기 위한 선택이다.
- **무장 대기:** 무장 파일은 0.5초마다, 준비 재확인은 5초마다 본다.
- **`--dry`:** 전송권을 잡지 않는다. 남은 전송권은 `--dry`도 막는다.

### 3. 변경 파일

| 파일 | 변경 | SHA256 앞 16자리 |
|---|---|---|
| [permit.py](permit.py) | 신규. `acquire`(단일 전송권)·`existing_permit`·`durable_json`·`parse_at`·`start_check`·`fire_check` | `54fb16e00e9f595f` |
| [live_order.py](live_order.py) | 시작 때 전송권·모든 실행일 미해결 intent·실행일·발사 시각 검사, `fire_at` 고정, 무장 대기(발사 시각 경계)·허용 지연 검사, 주문 직전 전송권·A1 상태 전이·예외 시 `unknown`, intent 기록을 `durable_json`으로, `--late-limit`·`--status`, `--day` 기본값을 실행 시점에 계산 | `3ce030233002bdd7` |
| [test_permit.py](test_permit.py) | 신규 9개(프로세스 경합·날짜 무관·디렉터리 동기화 포함) | `2bf65fbfe2b48ac8` |
| [test_live_order.py](test_live_order.py) | 하네스 시계 고정(2099-01-01 KST), `SendSafetyTests` 13개 | `989eaf9e647390ca` |

### 4. 시험 명령과 결과

Mac Python 3.11.7. `api_booking/test_*.py` 17개 파일 모두 exit 0.

```bash
./.venv/bin/python api_booking/test_permit.py
./.venv/bin/python api_booking/test_live_order.py
```

```powershell
# Windows 명령. 실행하지 않았다(완료 조건 미충족).
.\.venv\Scripts\python.exe api_booking\test_permit.py
.\.venv\Scripts\python.exe api_booking\test_live_order.py
```

| 시험 | 결과 |
|---|---|
| test_permit | 9개 통과(경합 4회×6프로세스) |
| test_live_order | 39개 통과 |
| 회귀 | 모두 통과 |

변이 검사(스크래치): O_EXCL 제거, 시작 시 전송권 검사 제거, 무장 확인 뒤 늦음 검사 제거(시험 보강 후 검출), 발사 직전 지연 검사 제거, 전송권 없이 전송, 시작 검사 제거, 전송 예외 시 `unknown` 기록 제거, 날짜별 전송권으로 되돌림, 다른 날짜 미해결 intent 무시, 디렉터리 동기화 제거를 각각 넣으면 관련 시험이 실패했다.

### 아스트라 검토 — `20260914-005112-9aaff071`

gpt-6-astra, 미커밋 56개 중 D4 지정, `ok=true`·`scopeStable=true`. 정적 검토만.

| 지적 | 판단 | 조치 |
|---|---|---|
| P1 실행일별 전송권이라 자정 직전 주문 응답 유실 뒤 자정 이후 재시작하면 새 주문 가능 | **채택.** 자정을 걸친 두 실행이 다른 날짜 파일을 잡는 경합도 가능했다 | 날짜 무관 단일 전송권, 시작 때 모든 실행일 intent 검사. 자정 재시작·이전 날 미해결 반례, 실행일 섞은 경합 시험 |
| P2 파일만 `fsync`, 디렉터리 항목·intent 미동기화로 전원 차단 뒤 기록 유실 가능 | **채택(POSIX).** Windows는 디렉터리 핸들을 `os.open`으로 열 수 없어 건너뛴다 | 전송권 생성 뒤 부모 디렉터리 `fsync`, intent를 `durable_json`(임시 파일 `fsync`→교체→디렉터리 `fsync`)으로. 호출 수 시험 |

재검토는 하지 않았다. 전원 차단 자체를 재현한 시험은 없다.

### 5. 미확인·한계

1. **Windows 미실행.** NTFS에서 O_EXCL 원자성·spawn 경합 시험은 확인하지 않았다.
2. 같은 PC·같은 `dev-shots/state`만 막는다. 다른 PC·다른 작업 폴더·사람의 수동 주문·기존 UI 예매(`manual_booking`)의 주문은 모른다.
3. 전송권은 날짜와 무관한 하나다. 리허설 뒤 실전(같은 날이든 다음 날이든)을 하려면 사용자가 예약 상태를 확인하고 `dev-shots/state/order-permit.json`을 치우고 해당 intent를 `resolved`로 바꿔야 한다. 운영 절차 문서는 아스트라 정리 대상이다.
4. 네트워크 드라이브·동기화 폴더(OneDrive 등)에서 O_EXCL 보장은 확인하지 않았다.
5. Windows에서는 디렉터리 `fsync`를 하지 못한다(NTFS 메타데이터 저널에 의존, 미확인). 전원 차단 재현 시험은 없다.
6. 허용 지연 3초는 로컬 정책이다. 09시 실측 근거로 정한 값이 아니다.
7. A1 `OrderFlow`는 전송 구간 전이만 연결했다. 인계 성공(`CONFIRM_PAYMENT_WINDOW`)은 D5 판정이 필요하다.

### 6. 다음 작업

D4 아스트라 검토 → 수정 → D5(동의→마일리지→Npay 선택→실제 Npay 판정).

---

## D5 브라우저 후반 연결 — 2026-09-14 01:07 KST 작성, 01:19 아스트라 검토 반영, T1 로컬

### 1. 결론

| 완료 조건(API README §4) | 상태 |
|---|---|
| 체크된 동의를 다시 꺼버리지 않음 | **T1 로컬 통과.** 동의마다 기존 매크로 `alreadyOn` 규칙으로 상태를 읽고 켜져 있으면 누르지 않는다. 상태 표지가 없으면(`unknown`) 켜진 것일 수 있어 누르지 않고 멈춘다. 확인 뒤 `on`이 확인돼야만 다음 단계. 격리 Chromium에서 미리 켜진 동의가 꺼지지 않음 확인 |
| 실패 전파 | **T1 로컬 통과.** 동의 실패(모달 없음·스크롤 미완료·확인 누락·모달 안 닫힘·꺼짐)·마일리지 버튼 없음·Npay 미선택·결제하기 없음·결제창 미도착에서 뒤 단계로 가지 않고 `stage`로 돌려준다. `live_order --continue-payment`는 `completed`일 때만 exit 0 |
| 카드 PG·로그인·이전 창·금액 불일치는 완료 아님 | **T1 로컬 통과.** 새 창 판정은 운영 `dev/payment_window.inspect_payment_window`를 그대로 쓰고 이번 실행 전 창·**인계 대상 게이트가 열지 않은 창(opener 불일치)**은 제외. 완료는 Npay ready + 금액 판정 `matched`(원 금액 표기가 하나의 값뿐이고 기대값과 같음) + NaverPay 결제 세션 요청 참조 일치 + 결제하기 뒤 화면 주문 재판정 통과. 마일리지 적용은 차감 API 200·오류 필드 없음이 관측돼야 결제하기로 간다 |
| 실사이트 동의 요소 상태 표지·실제 Npay 화면·결제 세션 참조 위치 | **미관측** |

이번 단계 실사이트 요청 0회, 주문 0회, 결제창 접속 0회.

### 2. 기존 절차 대조(빠진 것 없는지)

| 기존 17단계(보관 README §5.2·`ke_award/steps.json`) | D5 처리 |
|---|---|
| 6~7 승객·연락처 확인 | API 주문(`inputTravellers`)이 대신한다. 인계 뒤에는 누르지 않는다(누르면 주문 요청 → 차단·실패) |
| 8~10 첫 동의 → 끝까지 스크롤 → 확인 | `agree_step('btn-resv-agree-1')`: 상태 읽기 → 꺼져 있을 때만 클릭 → 스크롤 버튼이 사라질 때까지 → 확인 → 모달 닫힘 → 상태 재확인 |
| 11~13 두 번째 동의 → 스크롤 → 확인 | `agree_step('btn-resv-agree-3')` 같은 절차. 끝난 뒤 두 동의가 꺼지지 않았는지 다시 확인 |
| 14 마일리지 적용 | `btnAwardUseMileageApply` 클릭 + 이 페이지의 `bonusBookingDeductMileage` 응답(200·JSON·오류 필드 없음) 관측. 적용액 대조는 응답 계약이 없어 하지 않음 |
| 15~16 방향별 결제수단 | ICN 출발만 Npay(`rad-naverpay`, 체크 확인). ICN 도착(현대카드)은 `unsupported-provider`로 거부. 다른 수단으로 바꾸지 않음 |
| 17 결제하기 → 새 제공자 창 판정 | `btn-payment` 한 번 클릭 → 이번 실행 새 창을 최대 25초 판정. 게이트에 약관 체크 경고가 뜨고 새 창이 없으면 즉시 멈춤. 제공자 창 안에서는 누르지 않음 |
| 보관 README §5.3 판정 | `inspect_payment_window`의 로딩·대한항공·금액 표기·결제 버튼·로그인/오류 배제 그대로. API 흐름 조건(동일 주문·최종 금액)은 NaverPay 참조·금액 표기로 추가 |

### 3. 근거와 한계

- 동의 상태 규칙은 `ke_award/util.js alreadyOn`(aria-pressed/checked/selected → 상위 2단계 class → 내부 체크박스)과 같다. 규칙에 걸리는 표지가 없으면 `unknown`으로 두고, 확인 뒤에도 `unknown`이면 실패로 막지는 않되 `verified=False`로 남긴다. 실사이트 `btn-resv-agree-*`에 어떤 표지가 있는지는 이 PC 자료로 확인하지 못했다.
- 9/13 08:33 실측(이미 동의한 게이트에서 동의를 다시 눌러 약관 체크 경고)이 "켜진 동의는 누르지 않음"과 "경고 문구 감지"의 근거다.
- NaverPay 결제 세션 요청의 `reservationRecLoc` = 주문 pnr은 9/10 수집 113950의 문서 기록이다. 원본이 이 PC에 없고 본문 안 위치를 몰라 JSON 전체에서 같은 이름을 찾는다(모두 같아야 읽힘). 이 연결이 실제로 없으면 완료가 아니라 `npay-reference-unobserved`로 끝난다.
- 기대 금액은 이번 운임 응답 `pnrFareInfo.totalAmount`다. Npay 창에 같은 숫자 표기가 뜬다는 것은 미확인이다. 다르면 `provider-amount-unmatched`로 끝난다.

### 4. 변경 파일

| 파일 | 변경 | SHA256 앞 16자리 |
|---|---|---|
| [site_drive.py](site_drive.py) | `agree_with_modal`(모달 없으면 동의로 간주하고 무조건 클릭하던 결함) 제거 → `AGREE_STATE_JS`·`agree_state`·`agree_step`. `apply_mileage`(차감 응답 확인), `krw_amounts`·`amount_verdict`. `payment_pass` 후반 재작성(방향 확인·동의 재확인·Npay 체크·결제하기 전후 재판정·`wait_provider_window`(opener 확인)·완료 조건). `_wait_id`가 제한 0에서도 한 번 확인. 인계 관찰기가 NaverPay POST 본문 참조를 메모리로 읽음 | `07106819da999218` |
| [handoff.py](handoff.py) | 참조 출처에 NaverPay POST JSON `reservationRecLoc` 추가, `reference_in(method, url, body)`, `ScreenOrder.matched_sources` | `57aa6b283830e91d` |
| [live_order.py](live_order.py) | `--continue-payment`: 방향·기대 금액 전달, `completed`일 때만 exit 0, intent에 `handoff`·`paymentWindowReached`, A1 `BEGIN_HANDOFF`→`CONFIRM_PAYMENT_WINDOW`/`HANDOFF_FAILED`. 도움말 갱신 | `ff2b6fc84bfce2eb` |
| [test_site_drive.py](test_site_drive.py) | 가짜 게이트에 동의·모달·Npay·차감 응답 모델, `PaymentPassTests` 15개, `AmountVerdictTests` | `1a82e87b94482bcd` |
| [test_payment_browser.py](test_payment_browser.py) | 신규. 격리 Chromium 게이트·Npay 픽스처로 후반 전체 10개(다른 탭 창·금액 부분 문자열·표지 없는 동의·차감 오류 포함) | `98a36a32a5dcf987` |
| [test_live_order.py](test_live_order.py) | `ContinuePaymentTests` 2개 | `247e1973447ad1d2` |

### 5. 시험 명령과 결과

Mac Python 3.11.7. `api_booking/test_*.py` 18개 파일 모두 exit 0.

```bash
./.venv/bin/python api_booking/test_site_drive.py
./.venv/bin/python api_booking/test_payment_browser.py
./.venv/bin/python api_booking/test_live_order.py
./.venv/bin/python api_booking/test_handoff.py
```

Windows는 같은 파일을 `.\.venv\Scripts\python.exe api_booking\test_<이름>.py`로 실행한다(미실행).

| 시험 | 결과 |
|---|---|
| test_site_drive | 44개 통과 |
| test_payment_browser | 10개 통과(약 29초) |
| test_live_order | 41개 통과 |
| test_handoff | 39개 통과 |
| 회귀 | 모두 통과 |

변이 검사(스크래치): 켜진 동의도 누름, 모달 없으면 동의로 간주, 결제 전 동의 재확인 제거, 금액 대조 제거, 결제 세션 참조 요구 제거, 방향 제한 제거, 미완료도 exit 0, opener 확인 제거, 금액 부분 문자열 비교, `unknown` 동의 진행, 마일리지 클릭만으로 확인을 각각 넣으면 관련 시험이 실패했다.

시험 중 발견해 고친 것: `_wait_id`가 제한 0이면 한 번도 확인하지 않고 실패했다(실운영 기본값에서는 드러나지 않음). 격리 Chromium 픽스처의 전역 `open` 함수가 `window.open`을 덮어 새 창이 안 열렸다(시험 픽스처 결함).

### 아스트라 검토 — `20260914-010903-2ee60c2b`

gpt-6-astra, 미커밋 57개 중 D5 지정, `ok=true`·`scopeStable=true`. 정적 검토만.

| 지적 | 판단 | 조치 |
|---|---|---|
| P1 같은 컨텍스트 다른 탭이 연 Npay 창도 완료로 인정 | **채택** | 후보 창의 `opener()`가 인계 대상 게이트일 때만 판정. 다른 탭 창은 `otherTabWindows`로 셈. 격리 Chromium 반례 |
| P1 금액을 본문 부분 문자열로 비교(`1,325,500원`도 통과) | **채택(최종 결제액 필드 식별은 불가)** | '원' 금액 토큰을 정수로 뽑아 하나의 값뿐이고 기대값과 같을 때만 `matched`, 서로 다른 금액이 섞이면 `ambiguous`로 미완료. 실제 Npay 최종 결제액 필드 계약은 없다 |
| P1 동의 상태 `unknown`이면 결제까지 진행 | **채택** | 확인 뒤 `on`이 아니면 실패. 누르기 전 `unknown`도 켜진 것일 수 있어 누르지 않고 멈춤 |
| P2 마일리지 적용을 좌표 클릭만으로 통과 | **부분 채택** | 공개 호출부 경로 `bonusBookingDeductMileage`의 이 페이지 응답(200·JSON·오류 필드 없음)이 관측돼야 진행. 적용액 대조는 응답·화면 계약이 없어 하지 못함(보류) |

재검토는 하지 않았다.

### 6. 미확인·한계

1. **실사이트 미검증:** 실제 게이트 동의 요소의 상태 표지, 모달 구조(9/13 id는 관측), 마일리지 적용 뒤 변화, Npay 새 창 URL·문구·금액 표기, NaverPay 요청 본문을 D5 코드로 본 적이 없다. README의 `--continue-payment` 사용 금지는 로컬 결함 수정으로 풀리지 않는다. §5 T2b 실사이트 확인이 필요하다.
2. 실제 동의 요소에 `alreadyOn` 규칙의 표지가 없으면(`unknown`) 후반은 첫 동의에서 멈춘다. 실사이트에서 표지 확인이 필요하다.
7. 마일리지 차감 API 응답을 실제로 본 적이 없다. 적용 때 이 요청이 안 나가면 `mileage-deduct-response-unobserved`로 멈춘다. 적용액 대조는 보류.
8. Npay 창에 원 금액이 여러 개(할인·포인트 0원 등) 보이면 `provider-amount-ambiguous`로 미완료가 된다. 창 자체는 열려 있으므로 사용자는 볼 수 있다.
9. 사이트가 `noopener`로 창을 열면 opener가 없어 완료로 판정하지 못한다.
3. 실제 Npay가 네이버 로그인을 먼저 요구하면 `provider-window:login-required`로 끝난다(9/10 기록에 로그인 요구 관측). 사용자가 로그인해도 이 실행은 완료로 바꾸지 않는다.
4. ICN 도착(현대카드) 방향은 다루지 않는다.
5. 결제창 대기 25초는 로컬 정책이다.
6. Windows 실행 미검증.

### 7. 다음 작업

D5 아스트라 검토 → 수정 → D1~D5 전체 요약 정리.

---

## D1~D5 전체 요약 — 2026-09-14 01:19 KST

**상태: 로컬(T1, Mac)까지. 실사이트·Windows·09시 조건은 전부 미검증이며 준비 완료가 아니다.** 이번 작업 전체에서 실사이트 요청·주문·결제창 접속은 0회, 운영 9232/9233·예약 작업·설정·NOW/README/FACTS/API README는 변경하지 않았다. `dev-shots/state`의 실제 기록은 건드리지 않았다(9/13 intent `resolved` 그대로, 전송권 파일 없음).

| 단계 | 로컬 결과 | 아스트라 검토 | 반영 | 실사이트에서 확인할 핵심 |
|---|---|---|---|---|
| D1 동일 주문 인계 | 게이트 요청 `baggagePolicies` GET `orderId`로 화면 주문 판정, 인계 중 주문 요청 차단 | `d2574cfe` 4건 | 4 채택 | API 주문 뒤 게이트 재진입에서 이 요청·참조가 나오는지 |
| D2 달력 시작 준비 | 이번 실행 준비 세대 캡처를 메모리 보관, 준비 중 주문 요청 차단, 달력 문서에서 발사, 구간별 호출 수 | `165a57e4` 4건 | 4 채택 | 차단 뒤 캡처 유효·달력 문서 발송을 서버가 받는지 |
| D3 실제 계약·검증 | A2~A4 판정을 발사 경로에 연결, 실측과 다른 합성 조건 수정 | `2331c183` 3건 | 2 채택, 1 부분(잔액 계정 결속 보류) | 프레스티지·신규 날짜 응답, 잔액 서버 계약 |
| D4 전송 안전성 | 날짜 무관 단일 전송권(O_EXCL·디렉터리 fsync), 실행일·발사 시각 고정, 늦은 무장·기상 거부 | `9aaff071` 2건 | 2 채택 | Windows 경합·내구성 |
| D5 후반 연결 | 동의 상태 확인·차감 응답·Npay 선택·opener 창 판정·금액·결제 세션 참조 | `2ee60c2b` 4건 | 3 채택, 1 부분(적용액 대조 보류) | 동의 표지·차감 요청·Npay 화면·NaverPay 참조 |

전체 로컬 시험: `api_booking/test_*.py` 18개 파일 모두 exit 0(Mac Python 3.11.7, Playwright 1.62.0). 격리 브라우저 시험 4개 파일은 모든 요청을 로컬 응답으로 채우고 DNS를 막았다.

### 새 실행 방식(운영 문서 반영은 아스트라 몫)

```bash
# 읽기 전용 상태 확인
./.venv/bin/python api_booking/live_order.py --date 2027-09-09 --status
# 예: 무장 대기 뒤 09:00 발사, 게이트 인계 확인에서 멈춤(사용자가 잔액을 확인해 넣는다)
./.venv/bin/python api_booking/live_order.py --port 9242 --date 2027-09-09 --family KEBONUSPR \
  --capture-date '09월 07일' --capture-cabin 일반석 --reuse-member-check --own-mileage <확인값> \
  --at 09:00:00 --arm-file <사용자가 만드는 파일> --gate-only
```

- 위 명령은 **실제 주문을 만든다.** 캡처 준비 중 주문 요청은 막지만 실사이트에서 막힘을 확인하지 않았다. README §5 순서(T2a→T2b)와 사용자 허용 없이 실행하지 않는다.
- `--continue-payment`는 D5 로컬 수정이 들어갔지만 실사이트 미검증이라 README의 사용 금지를 풀 근거가 아니다.
- 주문이 한 번 나가면 `dev-shots/state/order-permit.json`이 남아 이후 모든 실행을 막는다. 사용자가 예약 상태를 확인하고 치운다.

### 실사이트로 넘어가기 전 수집할 증거(우선순위)

1. **주문 없는 T2a:** 이미 열린 일반석으로 `--dry` 1회. 준비 통과의 주문 요청 차단(`inputTravellers-blocked`)·캡처 3구간·달력 복귀·조회/운임 판정 `ready`(잔액 제외)를 확인한다. 차단이 실패하면 주문이 생길 수 있어 사용자 허용이 필요하다.
2. 게이트 동의 요소의 상태 표지(aria/class/체크박스)와 `bonusBookingDeductMileage` 발생 여부 — 주문 없이 확인할 방법이 없으면 T2b에서.
3. API 주문 1회 뒤 게이트 재진입 판정(D1 다음 증거)과 Npay 창 도착·금액·NaverPay 참조(D5).
4. `searchFamilyInfoList` 응답의 본인 가용 마일리지 필드(D3 보류 항목).
5. 회사 Windows에서 18개 시험 파일 실행(D4 완료 조건).

### 커밋·검토 상태

- 모든 변경은 미커밋이다. 각 단계 검토는 한 번씩만 받았고 수정 뒤 재검토는 하지 않았다.
- 정적 검토·로컬 시험은 T1이다. T2/T3 성공으로 쓰지 않는다.
