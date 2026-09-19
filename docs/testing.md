# 시험 체계

기준: 2026-09-19. 검증 보고 절차는 [ke-verify](../.claude/skills/ke-verify/SKILL.md), 실행 결과 기록은 [results](results/).

## 1. 시험 단계

| 단계 | 뜻 | 예 |
|---|---|---|
| **T1 로컬** | 픽스처·가짜 전송·로컬 Chromium. 실사이트 성공 아님 | `api_booking/run_tests.py`, `run-tests.ps1` |
| **T2 리허설** | 실사이트·이미 열린 날짜. 노선·등급·환경·도착 범위를 명시 | `--dry` 경로 점검, 워밍, 콜드 스타트 |
| **T3 실조건** | 실제 09:00 신규 개방. 동일 실행 로그로 확인, 횟수 명시(1/2 등) | 매일 09:00 실전 |

T2 성공을 09시 경쟁 성공으로, T1 통과를 실사이트 동작으로 보고하지 않는다.

## 2. 실행 명령

| 묶음 | 명령 | 범위 |
|---|---|---|
| API 예매 | `.\.venv\Scripts\python.exe api_booking\run_tests.py` (`--browser` 로컬 Chromium 포함) | `api_booking/test_*.py` 27개 파일, 약 420개 |
| 계측·일정·공용 | `.\.venv\Scripts\python.exe test\test_<이름>.py` 개별 | `test_calendar_rules`, `test_calendar_observer`, `test_day_readiness`, `test_runtime`, `test_timing_diagnostics`, `test_order_evidence`, `test_prepare_currency`, `test_payment_window`, `test_skills_sync` |
| 브라우저 매크로 | `.\run-tests.ps1` (31단계, 로컬 Chromium, 오래 걸림) | `test/` UI 매크로 시험·JS 유닛·빌드 |
| JS | `node test\test_autoconfirm.js`, `test_util.js`, `test_calendar_probe.js`, `test_probe.js`, `test_deeplink.js` | |

- `t.sh`는 Chrome 일괄 종료 코드가 있어 병행 환경에서 쓰지 않는다.
- `test_site_rehydrate.py`는 Git 제외 저장 소스(`dev-shots/api-source-2026-09-10`)가 없으면 건너뛴다.

## 3. 변경별 필수 시험

| 변경 | 시험 |
|---|---|
| `api_booking/` | `run_tests.py` 전체 + `--dry` 경로 점검(실사이트, 주문 없음)으로 영향 경로 확인 |
| 일정(`config/test_calendar.json`, `dev/test_calendar.py`) | `test_calendar_rules`, `test_calendar_observer` + `dev\test_calendar.py --render` 뒤 `docs/calendar.md` 커밋 |
| 로그인·Chrome·시계(`dev/setup.py`, `astra_browsers.ps1`, `runtime.py`) | `test_runtime`, `test_day_readiness` + 9232 로그인 스모크(`dev\setup.py --port 9232`) |
| 계측 | `test_calendar_observer`, `node test\test_calendar_probe.js`, `test_timing_diagnostics` |
| UI 매크로 | `node build.mjs` 뒤 핵심 8개(hud·parity·skipcal·calendar·deadclick·twoagree·pagewait·currency) |
| 스킬 | `.claude/skills`와 `.agents/skills`를 같게 고친 뒤 `test_skills_sync` |

## 4. 결과 기록 규칙

- 실사이트 실행(T2·T3)은 날짜별 `docs/results/YYYY-MM-DD.md` 한 장에 모은다. 틀:
  **요약 표 → 타임라인(보정 시각) → 계측 → 새로 확인한 사실 → 해석 한계 → 실행 식별자 → 남은 상태.**
- 원본 증거는 `dev-shots/`(Git 제외)에 두고 문서에는 실행 ID·경로만 적는다. 계정·예약번호·승객 정보는 적지 않는다.
- 새로 확인한 사실은 [FACTS](../FACTS.md)의 해당 주제 표에 한 줄로 올리고 결과 문서를 출처로 링크한다.
- 운영 규칙이 바뀌면 해당 명세(`docs/spec/*`)를 고친다. 결과 문서에 운영 지시를 쓰지 않는다.

## 5. 알려진 시험 상태 (2026-09-19)

- API T1 27개 파일 통과(`test_site_rehydrate` 건너뜀). 로컬 브라우저 시험 4개 파일 통과.
- `run-tests.ps1` 전체(31단계)는 이 PC에서 아직 돌리지 않았다.
