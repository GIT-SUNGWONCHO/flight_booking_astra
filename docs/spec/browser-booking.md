# 브라우저(UI 매크로) 예매 명세 — 보류·대체 수단

기준: 2026-09-19. **현재 운영 경로는 [API 예매](api-booking.md)다.** 이 방식은 사용자 지시로 반복 테스트를 멈춘
보류 상태이며, API 경로가 막힐 때의 대체 수단으로 보존한다. 재개는 사용자 지시가 있을 때만 한다.

## 1. 구성

| 파일 | 역할 |
|---|---|
| `ke_award/recorder.js`·`util.js`·`steps.json`·`hud.js`·`editor.js`·`autoconfirm.js`·`probe.js` | 17단계 녹화 재생·날짜/등급 동적 선택·HUD(대기·선발사 2500ms) |
| `build.mjs` → `userscript/ke-award-macro.user.js` | JS 원본을 사용자 스크립트로 빌드. 산출물을 직접 고치지 않는다 |
| `ke_award/runner.py`·`clock.py`·`__main__.py` | Playwright 러너·NTP(`clock.py`는 API의 `measure_clock`도 사용) |
| `dev/prepare_day.py`·`check_day.py`·`worker_health.py`·`stage_process.py` | 08:20 준비·읽기 점검·작업자 수명 |
| `dev/manual_booking.py`·`hybrid.py`·`departure_live.py`·`network_trace.py` | 사용자 대기·정시 발사·조회 화면 경로·네트워크 증거 |
| `dev/autorun.py`·`daily.py`·`rehearse.py`·`ke_setup.py`·`watch_seats.py`·`preflight.py`·`drive.py` | 과거 실행기·리허설·보조. 현재 운영 명령과 섞지 않는다 |
| `dev/*.ps1`·`*.cmd`·`*.sh`(`day.ps1`, `morning.ps1`, `browsers.ps1`, `astra_target.ps1`, `daily.cmd`, `dev-browser*.cmd/sh`) | Windows/Mac 실행 보조 |

## 2. 유지할 동작 조건 (재개 시 그대로 적용)

- 기본 경로는 달력 시작·HUD 대기·2500ms 선발사. HUD가 정시에 시작하며 준비 프로그램이 대신 무장하지 않는다.
- 단계: 날짜/검색 → 등급/KRW/다음 → 승객/연락처 → 두 동의 모달(각각 스크롤·확인) → 마일리지 → 결제수단 → 결제하기.
- KRW 적용은 달력 복귀·좌석 해제를 일으킬 수 있어 날짜·운임을 다시 확인한다. 홈 검색은 통화를 USD로 되돌린다(9/19).
- 동의 완료는 클릭 기록으로 대체하지 않는다(재클릭은 체크 해제). 연락처 확인 중복 클릭 재시도를 제한한다.
- 결제수단: ICN 출발 Npay, ICN 도착 한국발행 카드→현대카드. 실패 시 다른 제공자로 바꾸지 않는다.
- 일반 상한 개방+240초. 창 닫기·홈 복귀는 주문 해제 증거가 아니다.
- 과거 운영 명령(재개 지시 때만): `dev/test_calendar.py --day`, `dev/check_day.py --day`, `dev/prepare_day.py --day`
  (`--preview`도 주문을 만들 수 있다, `--cold`는 Chrome 재시작이지 부팅 시험이 아니다).

## 3. 알려진 실측과 보류 문제

| 항목 | 기록 |
|---|---|
| 9/9 일반석 | 실제 Npay 도착, 재리허설 17단계 성공 |
| 9/10 09시 | 날짜 선택 0/17 중단 |
| 9/11 Windows 09시 | 2/17 중단, 달력 요청 시작 발사+15.522초(원인 미확정) |
| 9/12 Mac 09시 | 08:59:57.387 발사, +13.66초에 2/17 매진 중단 |
| 보류 | Mac cold 종료·프로필 소유권, 준비 자식 정리시간 5초 초과 변동, UI 날짜 복귀·skipcal 간헐성, 실제 부팅·아침 예약 |

상세 이력은 [정리 전 README](../archive/2026-09-19/before-README.md)와 [2026-09-13 보관본](../archive/2026-09-13/before-README.md).
