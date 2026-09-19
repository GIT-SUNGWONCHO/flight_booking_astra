# AI 작업 규칙

AI가 지켜야 할 작업 범위·권한·변경 규칙이다. 시스템 구성은 [README](README.md), 동작은 [docs/spec/](docs/spec/).
이전 판은 [보관본](docs/archive/2026-09-19/before-AGENTS.md).

## 1. 작업 시작

1. [NOW](NOW.md)에서 다음 목표·남은 상태를 확인한다.
2. [README](README.md)의 문서 지도에서 해당 구성 요소 명세와 [운영](docs/operations.md)을 읽는다.
3. [FACTS](FACTS.md)에서 관련 실측과 한계를 대조한다. 날짜·노선은 [공통 일정](config/test_calendar.json)이 원본이다.
4. `git status --short`로 미커밋 상태를 확인한다. reset·clean·재설치를 인계 절차로 하지 않는다.

문서 확인 요청만으로 주문·예약 작업 등록·개발을 시작하지 않는다. 개발은 **계획을 먼저 보고하고 사용자 승인 뒤** 진행한다.
`docs/archive/`는 과거 증거이며 실행 지시가 아니다.

## 2. 작업 영역

| 영역 | 허용 범위 |
|---|---|
| API 예매(운영) | 이 저장소, 9232·`.debug-profile`. 2026-09-19 사용자 승인으로 9232에서 API 예매를 운영한다 |
| 계측 | 9233·`.debug-profile2`(본인 네이버). 예매와 실패를 분리한다 |
| API 연구 | 9242·`.api-profile`(`api_booking/open_lab.*`). 프로필 복사 금지 |
| 이웃 원본 | 옆 `flight_booking` 폴더와 9222/9223은 읽거나 연결하지 않는다 |

Chrome을 프로세스 이름으로 일괄 종료하지 않는다. 종료는 소유 프로필·포트·PID를 확인한 범위에 한정한다
(사용자 개인 Chrome은 사용자 지시가 있을 때 정상 종료만). 원본 예약 작업 `ke_morning`·`ke_precheck`는 다시 켜지 않는다.

## 3. 행동 권한과 핵심 제약

- 2026-09-19 사용자 명시 승인: **리허설과 실전테스트에서는 프로그램이 무장·발사하고 주문 1건까지 진행할 수 있다.**
  최종 결제는 사용자만 하며 결제창·카드사 창 안에서는 누르지 않는다. 이 승인 밖(중요목표 등)은 사용자 지시를 따로 받는다.
- 작업 스케줄러 등록·삭제, 전송권·`unknown` 보관(`--release-no-reference`)은 **매번 사용자 승인 뒤**에 한다.
- 주문은 실행당 1회. 응답이 불명확하면 재전송하지 않는다. 예약 목록으로 좌석 확보·해제를 판단하지 않는다.
- 계측의 준비·로그인·실행 실패를 이유로 예매를 멈추지 않는다.
- 미검증 값(선발사·계측 간격 가드 상향 등)은 사용자가 결정한 경우에만 실전에 넣는다.
- 실사이트 요청·주문은 허용된 시험 범위에서만 한다. 캡처 준비도 주문을 만들 수 있으므로 `--dry`라는 이름만으로 무주문으로 분류하지 않는다.

## 4. 변경과 검증

- 실행 코드 변경은 [시험 체계](docs/testing.md)의 변경별 필수 시험을 통과시킨다. 필요하면 [ke-review](.claude/skills/ke-review/SKILL.md)로 교차 검토한다.
- 결과는 [ke-verify](.claude/skills/ke-verify/SKILL.md) 기준(T1 로컬 / T2 리허설 / T3 실조건)으로 구분해 보고한다.
- 다음 실전 준비 중(08:20~09:05)에는 그날 실행에 쓰는 코드를 바꾸지 않는다.
- 작업은 브랜치에 단계마다 커밋·푸시한다. `.env`와 `dev-shots/`는 커밋하지 않는다.

## 5. 기록과 인계

문서는 한글로 쓰고 파일명·명령·식별자는 원문을 유지한다. 계정 원문·비밀번호·토큰·결제 식별자를 기록하지 않는다.
비밀번호는 `.env`에만 두고 대화·문서·출력에 옮기지 않으며, AI는 `.env` 값을 읽어 출력하지 않는다.

저장 허용(Git 제외 `dev-shots/state/order-evidence/`): `pnr`, `orderId`, 목표 날짜·공항·항공편·운임 등급, segmentStatus,
currency, totalAmount, mileage, 주문 요청/응답 시각, 실행 ID, 허용 목록 응답 진단, 승객 식별자 지문(2026-09-14 승인),
**주문 오류 코드·메시지(정제)**(2026-09-19 승인). 응답 전체·승객 원문·쿠키·인증/결제 토큰·pageTicket·trace/uuid는 저장하지 않는다.
식별자를 일반 문서·터미널 출력으로 옮기지 않는다.

문서 역할(한 정보는 한 곳): 상태 [NOW](NOW.md), 동작 [docs/spec/](docs/spec/), 절차 [docs/operations.md](docs/operations.md),
시험 [docs/testing.md](docs/testing.md), 사실 [FACTS](FACTS.md), 실행 결과 [docs/results/](docs/results/),
일정 [config/test_calendar.json](config/test_calendar.json)→[docs/calendar.md](docs/calendar.md). 마무리는 [ke-wrap](.claude/skills/ke-wrap/SKILL.md).
