# Astra 예매·계측 작업 안내

기준: 2026-09-13. 현재 우선 작업은 **API 예매의 달력 시작·동일 주문 인계·실제 Npay 연결**이다. 기존 UI 일반 예매 테스트 반복은 사용자 지시로 중단했으며, 아래 기존 운영 명세는 API 준비 완료나 자동 실행 지시가 아니다.

| 원본 문서 | 역할 |
|---|---|
| [AGENTS](AGENTS.md) | 권한·영역·원본 보호 |
| [NOW](NOW.md) | 현재 상태·다음 작업 하나 |
| [API README §3~§6](api_booking/README.md#process) | 전체 처리 순서·클로드 개발 계획·시험/완료 조건 |
| [FACTS](FACTS.md) | 관측·근거·해석 한계 |
| [공통 설정](config/test_calendar.json) / [CALENDAR](CALENDAR.md) | 확정 일정 원본 / 생성본 |

<a id="new-session"></a>
## 1. 새 세션 시작

1. AGENTS → NOW → 이 README → FACTS → API README를 읽는다. 과거 보관본의 ‘다음 작업’을 따르지 않는다.
2. 같은 로컬 `flight_booking_astra`에서 `git status --short`로 미커밋/미추적 상태를 확인한다. 새 복제·reset·clean·재설치·재빌드를 인계 절차로 실행하지 않는다.
3. 필요한 코드·근거 파일 존재를 확인한다. `dev-shots/`는 Git 제외이므로 다른 PC에 없으면 과거 문서 기록과 직접 검증을 구분한다.
4. 운영 재개 때만 같은 실행의 프로세스·로그인·대기 상태를 읽기 점검한다. 과거 ready를 현재 ready로 사용하지 않는다.
5. 개발은 [API D1](api_booking/README.md#development)부터, 붙여 넣을 지시는 [클로드 프롬프트](api_booking/README.md#claude-prompt)를 사용한다.

문서 읽기/개발 요청이 실제 주문 시험의 허가를 뜻하지 않는다. API 연구의 새 실사이트 요청은 명시된 시험 범위에서만 수행한다.

<a id="configuration"></a>
## 2. 환경·로그인·일정

| 항목 | 기준 |
|---|---|
| 회사 실전 환경 | Windows, 기존 경로 `D:\Work\240_AI_project\99_side\flight_booking_astra` |
| 개발/실측 환경 | 9/12~13 Mac Python3.11.7. Windows 전체 흐름 검증을 대체하지 않음 |
| 예매 | 9232 / `.debug-profile`, 본인 네이버 연동 기본 |
| 계측 | 9233 / `.debug-profile2`, 본인 네이버 연동, 예매 실패 처리와 독립 |
| 연구 기본 | 9242 / `.api-profile`. 9232 예외 기록은 AGENTS 참조 |
| 일정 | Asia/Seoul, 실행일+360일, 실행 주말 포함, 출발일 일요일 제외. 구체 노선/날짜는 공통 JSON |
| 기존 UI 시각 | 준비08:20·점검08:50·개방09:00·선발사2500ms. API 발사값으로 자동 복제하지 않음 |

현재 Windows 가상환경 기록은 Python3.13.7이다. 최초 설치 때만 `py -3.13 -m venv .venv`, `pip install -r requirements.lock.txt`, `python -m playwright install chromium`, `node build.mjs`를 해당 환경에서 수행한다. 기존 환경은 매일 다시 설치하지 않는다.

- 기본 로그인은 본인 네이버. `KE_LOGIN_MODE=skypass`는 9232에만 적용하는 명시적 대체 설정이며 이때 네이버로 자동 전환하지 않는다. 9233은 네이버다.
- `dev/setup.py`는 프로필의 `.ke-login-mode`와 정책을 대조한다. 불일치는 재로그인이 필요하고, 수동 로그인 후 표시가 없으면 사용자 확인이 필요하다.
- `python dev/setup.py --port 9232 --confirm-login naver`는 로그인하지 않고 **사용자가 확인한 로그인 방식 표시만 저장**한다. 표시 자체가 계정/잔액의 서버 검증은 아니다.
- 비밀번호·회원 원문·토큰은 문서/로그에 쓰지 않는다. 추가 인증은 정상 화면에서 사용자 처리. 과거 배우자 세션을 본인 세션으로 간주하지 않는다.
- 기존 예매 토큰 판정은 개방+180초까지 만료 여유이며 향후 서버 세션 보장이 아니다. 실제 API 준비는 해당 계정/세션을 추가 확인해야 한다.
- 운영 NTP 시계 수정은 사용자 결정으로 보류한다. 실패 시 `clock.ok=false`를 동기화 성공으로 보고하지 않는다.
- 리허설 날짜는 이미 열린 허용 날짜를 사용하며 `test_calendar.py`가 계산한다. 계산 시각에 따라 값이 달라지고 실제 운항·재고는 별도 확인한다.

<a id="components"></a>
## 3. 구성과 수정 위치

| 구성 | 책임 |
|---|---|
| `dev/test_calendar.py` | 공통 일정 해석·CALENDAR 생성 |
| `dev/prepare_day.py`, `worker_health.py`, `check_day.py` | 준비·작업자 수명·읽기 점검 |
| `dev/astra_browsers.ps1`, `astra_browsers.sh`, `browser_identity.py` | 전용 Chrome·표시. 창 이름은 ready 증거 아님 |
| `dev/setup.py`, `prepare_currency.py`, `session_health.py` | 정상 로그인·검색 조건·KRW·로그인 만료 |
| `dev/manual_booking.py`, `ke_award/hud.js` | 기존 UI 사용자 대기·정시 발사·주입 유지·기록 |
| `ke_award/recorder.js`, `util.js`, `steps.json` | 기존 17단계·동적 날짜/등급·페이지 전환 |
| `build.mjs` | JS 원본→userscript 빌드. 산출물만 직접 수정 금지 |
| `dev/calendar_observer.py`, `ke_award/calendar_probe.js` | 달력 API P 계측·UI 별도 대조 |
| `dev/payment_window.py`, `network_trace.py`, `runtime.py` | 제공자 창·네트워크 증거·실행ID/해시/시계/저장 |
| `api_booking/` | API 독립 개발. 파일별 현황은 API README §2 |

<a id="booking"></a>
## 4. 유지해야 할 기존 UI 동작 조건

- 기본 경로는 달력 시작·2500ms 선발사다. 오래된 날짜 띠/DOM은 API 응답만으로 자동 갱신되지 않는다. 조회 화면 새로고침은 pageTicket 오류 사례가 있어 정상 경로와 구분한다.
- 준비는 이미 열린 조회 화면에서 KRW 선택→적용→재표시를 확인한다. 적용으로 달력 복귀·좌석 해제가 생길 수 있어 날짜/운임을 다시 확인한다.
- 노선은 실제 달력 POST의 segmentList와 대응 응답으로 검증한다. 공항 필드 없는 응답을 무조건 노선 오류로 보지 않되 요청 검증 없이 통과시키지 않는다.
- 목표 전체 날짜·편명/운항 조건·등급·양수 마일리지를 확인한다. 예전 녹화 날짜·셀 인덱스로 진행하지 않는다.
- 기존 단계: 날짜/검색 → 등급/KRW/다음 → 승객/연락처 → 두 동의 모달 각각 스크롤/확인 → 마일리지 → 결제수단 → 대한항공 결제하기.
- 연락처 확인은 중복 클릭 재시도를 제한한다. 동의 완료는 클릭 기록으로 대체하지 않는다. API 후반 연결에서도 이 조건을 유지한다.
- ICN 출발은 Npay, ICN 도착은 한국발행 카드→현대카드. 실패 시 다른 제공자로 대체하지 않는다.
- `prepare_day.py`/`manual_booking.py` 재시작은 대기·재생 상태를 초기화한다. 무장 후 상태 확인은 check_day로 한다.
- 정시 시작은 사용자 대기 후 HUD가 수행한다. 정상 준비가 대신 무장하지 않는다. 리허설의 시험용 시작은 별도다.
- manual_booking 작업자는 CDP/주입/기록을 유지한다. 준비 부모가 종료돼도 별도 작업자가 살아 있을 수 있으며, 대기 중 작업자 종료는 연속성을 깨뜨릴 수 있다.
- 일반 상한은 개방+240초. 정상 종료는 재생/대기를 해제하고 예외 종료는 잔여 상태를 별도 확인한다. 창 닫기·홈 복귀는 주문 해제 증거가 아니다.

<a id="measurement"></a>
## 5. 계측 조건

9233의 정상 달력 POST `calendarFareMatrix`를 포착해 같은 세션의 보조 탭에서 목표 전체 날짜·노선으로 반복한다. 화면을 자동 갱신한 것으로 가정하지 않고 종료 후 새 API/DOM을 대조한다.

| 항목 | 기준 |
|---|---|
| P | 목표 날짜의 fareFamilyList에서 KEBONUSPR과 같은 인덱스 status가 SOLDOUT이 아닌 조건. 항공편별 좌석 보유 증거 아님 |
| 표본 | 날짜 없음·HTTP/업무 오류·구조/노선 오류는 P없음과 분리 |
| 요청 | 간격1000ms·동시2·슬롯대기150ms·개별제한6초·기본90초 |
| 제한/지연 | 429/본문503은 대기, 401/403은 중단. 늦은 슬롯을 몰아 보내지 않고 누락/지연 기록 |
| 화면 | 계측 중 전체 재로딩0. API 결과·화면 P·화면을 만든 응답 세대 구분 |
| 완료 | ok=true는 유효 표본≥1일 뿐. 첫 유효·오류·누락·공백·전이·최종 대조 함께 보고 |

P있음→없음의 송수신 구간은 캐시/왕복/겹친 요청을 포함한다. 실제 개방을 확인하지 못했으면 ‘개방 후 몇 초 매진’이라고 하지 않는다. 계측 실패로 예매를 중단하지 않으며 같은 PC의 성능 부하는 공유됨을 기록한다.

<a id="operations"></a>
## 6. 기존 운영 명령 — 운영 재개를 지시받았을 때만

Windows 루트에서 실행한다. 날짜는 예시이며 공통 설정의 실제 실행일로 대체한다.

```powershell
# 읽기 전용 일정 확인
.\.venv\Scripts\python.exe dev\test_calendar.py --day 2026-09-14
# 해당 운영 실행의 상태 점검: 무장/새로고침 없음
.\.venv\Scripts\python.exe dev\check_day.py --day 2026-09-14
# 기존 전체 준비: 리허설 주문을 만들 수 있음. API 개발용 점검 명령 아님
.\.venv\Scripts\python.exe dev\prepare_day.py --day 2026-09-14
```

`--preview`도 실제 결제창 리허설로 주문을 만들 수 있다. `--cold`는 전용 Chrome 재시작이며 PC 부팅 시험이 아니다. 당일 준비는 실행일/08:50 마감/제외일을 검사한다. 일정에 있다고 자동 실전 투입하지 않는다.

예약 작업과 PC/앱 상태는 저장소 밖 상태다. 이번 문서 정리에서 조회·활성화·이전하지 않았다. 원본 `ke_morning`·`ke_precheck` 사용 중지는 유지한다. 예전 daily/autorun/watch_seats 실행법은 현재 운영에 섞지 않는다.

<a id="recovery"></a>
## 7. 로그·복구

- 부모 `dev-shots/runs/<ID>/prepare_day.json`의 service.runId/단계 로그에서 자식 실행을 찾는다. 가장 최신 JSON을 오늘 결과로 단정하지 않는다.
- `manual_booking.json`은 목표/설정/로그인/발사/단계/결제창, `network.json`은 제한된 응답·시각, `calendar_observer.json`은 표본·오류·P 전이·화면 대조를 확인한다.
- `browser_timing.json`과 ui-timing은 긴 작업·타이머·요청 전 지연 분석용이다. 원인을 전부 서버 또는 렌더링으로 단정하지 않는다.
- 상태 JSON의 실제 작업자 PID·실행ID·heartbeat와 `dev-shots/checks/`의 점검 결과를 대조한다. 부모 PID·창 존재·exit0 하나로 ready 판정 금지.
- check_day는 최신 당일 준비를 선택하고 미리보기를 제외한다. booking.ready와 사용자 대기 포함 readyForOpening, observer.ready를 따로 읽는다.
- 로그인/통화/목표 불일치는 해당 준비 단계에서 복구한다. 이미 대기 중인 별도 프로세스를 점검 실패로 종료하지 않는다.
- 주문 전송 후 응답 불명확은 상태 확인 전 API/UI 재주문 금지. 로컬 resolved·창 닫기·예약 목록에 없음만으로 서버 해제를 확정하지 않는다.
- 자세한 옛 운영 진단/실행 이력은 [정리 전 운영 문서](docs/archive/2026-09-13/before-README.md). 그 안의 옛 계정·주말·재개 지시는 현재 기준이 아니다.

<a id="validation"></a>
## 8. 검증·실제 결제창 기준

| 분류 | 의미 |
|---|---|
| T1 | 로컬 조건에서 코드 동작. 실제 계약·Windows·실사이트 성공으로 확대 금지 |
| T2 | 명시한 환경/여정/등급의 실제 리허설. 09시 경쟁 성공 아님 |
| T3 | 실제 신규 개방 조건의 관측. 연속2회 전에는 1/2 등 횟수 명시 |

- ICN 출발: 현재 실행의 새 pay.naver.com/m.pay.naver.com 창, 로딩 완료·대한항공 가맹점·금액·보이는 결제 버튼. API 흐름은 동일 주문·최종 금액 연결도 확인한다.
- ICN 도착: 새 ansimclick.hyundaicard.com/xacs3/ 창의 앱카드/PIN 선택·정상 로딩·오류/로그인 없음. 기존 판정의 금액 검증 한계는 유지한다.
- 로그인·오류·다른 제공자·기존 창은 통과가 아니다. 결제창 도착은 결제 승인/발권이 아니며 최종 승인은 사용자만 한다.
- 과거 `orderCreated=false`는 orderId 필드 미관측일 수 있다. 주문 실패/좌석 미보유의 단독 근거가 아니다.
- 신규 개방 의존 코드는 미개방→유효 재고→매진 fixture를 시험한다. 과거 전체 성공을 변경 코드의 완료로 소급하지 않는다.

<a id="handoff"></a>
## 9. 변경과 인계

[ke-verify](.claude/skills/ke-verify/SKILL.md)와 [ke-wrap](.claude/skills/ke-wrap/SKILL.md)를 따른다. API 단계별 명령/결과/해시/미확인은 API README §7의 방식으로 남긴다.

| 변경 | 관련 로컬 시험 |
|---|---|
| 일정 | test/test_calendar_rules.py 및 관련 일정 시험 |
| 준비·로그인·작업자 | test/test_day_readiness.py 및 변경 분기 시험 |
| UI 날짜·동의·재생·통화 | node build.mjs 후 핵심8개 |
| 제공자 판정 | test/test_payment_window.py, 실사이트 도착은 별도 T2 |
| 계측 | test/test_calendar_observer.py, test/test_calendar_probe.js 및 관련 진단 |
| API | api_booking/test_<관련>.py, 소비 경로의 회귀. 운영 자동 연결 금지 |

핵심8개는 hud/parity/skipcal/calendar/deadclick/twoagree/pagewait/currency이며 `python test/test_<이름>.py`를 개별 실행한다. t.sh는 Chrome 일괄 종료 코드가 있어 병행 환경에서 사용하지 않는다.

실행 코드/API 코드 커밋 전 [ke-review](.claude/skills/ke-review/SKILL.md)를 적용한다. 정적 검토는 T1/T2/T3가 아니다. 문서만 바꾸면 링크·명령·설정·코드 대조로 확인하며 실사이트 주문 시험을 하지 않는다.

<a id="deferred"></a>
## 10. 보류한 운영 문제 — 삭제된 완료 항목이 아님

| 남은 문제 | 재개 조건/근거 |
|---|---|
| Mac cold 종료·프로필 소유권 | 창 없는 Chrome 종료 실패, 운영 sh 소유권 보증 제한. Windows 연구 실행기로 일반화 금지 |
| 실제 부팅·아침 예약 | 활성/시간대/전달 지연·Mac 예약 미설정 기록. 현재 예약 상태는 별도 확인 필요 |
| 준비 자식 정리시간 | 5초 기준 초과 변동 반복. 임계값만 늘려 해결 처리 금지 |
| 초기 달력 지연·계측 | 9/11 초기15.522초 원인 미확정, 이후 슬롯 수정 T3 미검증. 9/12 오류 원인 추적 보류 |
| UI 날짜 복귀·최종 화면 대조·skipcal | 기존 간헐성/미대조 범위 유지. 관련 UI 변경 시 재시험 |
| ICN 도착 현대카드 | 방향별 새 코드의 전체 T2 미완료. 방향 전환 때 별도 검증 |
| ke_review Windows | 설치 경로·실행 호환 미검증. 마지막 일부 수정은 자체 시험 기록만 있음 |

실행 ID·상세 잔여 이력은 [정리 전 NOW](docs/archive/2026-09-13/before-NOW.md), 근거는 FACTS에서 찾아 읽는다. 현재 개발 우선순위는 NOW와 API README만 따른다.
