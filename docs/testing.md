# 시험 체계

기준: 2026-10-04. 검증 보고 절차는 [ke-verify](../.claude/skills/ke-verify/SKILL.md), 실행 결과 기록은 [results](results/).

## 1. 시험 단계

| 단계 | 뜻 | 예 |
|---|---|---|
| **T1 로컬** | 픽스처·가짜 전송·로컬 Chromium. 실사이트 성공 아님 | `run_tests.py` |
| **T2 리허설** | 실사이트·이미 열린 날짜. 노선·등급·환경·도착 범위를 명시 | `--dry` 경로 점검, 워밍, 콜드 스타트 |
| **T3 실조건** | 실제 09:00 신규 개방. 동일 실행 로그로 확인, 횟수 명시(1/2 등) | 매일 09:00 실전 |

T2 성공을 09시 경쟁 성공으로, T1 통과를 실사이트 동작으로 보고하지 않는다.

## 2. 실행 명령

실행기는 저장소 맨 위의 `run_tests.py` 하나다. 실사이트와 로그인된 Chrome 에 접속하지 않는다.

| 묶음 | 명령 | 범위 |
|---|---|---|
| 전부 | `.\.venv\Scripts\python.exe run_tests.py` | 아래 넷 전부. 약 5분 |
| 예매 프로그램 | `.\.venv\Scripts\python.exe run_tests.py live` | `api_booking/test_*.py` |
| 페이지 스크립트·공용 | `.\.venv\Scripts\python.exe run_tests.py engine` | `test/` — 유저스크립트(HUD·재생), 계측기, 시계, JS 유닛 |
| 옛 브라우저 매크로 | `.\.venv\Scripts\python.exe run_tests.py macro` | `macro/test/` |
| 조사 도구 | `.\.venv\Scripts\python.exe run_tests.py research` | `research/test/` |

- `-k 글자` 로 이름을 골라 돌린다(`-k hud -k skipcal`). `--no-browser` 는 로컬 Chromium 을 띄우는 시험을 뺀다.
- 실행기는 Chrome 을 이름으로 종료하지 않는다. 멈춘 시험은 `--timeout`(기본 300초) 뒤 끊고 실패로 센다.
- `test_site_rehydrate.py`는 Git 제외 저장 소스(`dev-shots/api-source-2026-09-10`)가 없으면 건너뛴다.

## 3. 변경별 필수 시험

| 변경 | 시험 |
|---|---|
| `api_booking/` | `run_tests.py live` 전체 + `--dry` 경로 점검(실사이트, 주문 없음)으로 영향 경로 확인 |
| 일정(`config/test_calendar.json`, `dev/test_calendar.py`) | `run_tests.py -k calendar` + `dev\test_calendar.py --render` 뒤 `docs/calendar.md` 커밋 |
| 로그인·Chrome·시계(`dev/setup.py`, `astra_browsers.ps1`, `runtime.py`) | `run_tests.py -k runtime -k day_readiness -k server_clock` + 9232 로그인 스모크(`dev\setup.py --port 9232`) |
| 계측 | `run_tests.py -k observer -k calendar_probe -k timing` |
| 페이지 스크립트(`ke_award/*.js`) | `node build.mjs` 뒤 `run_tests.py engine`. 급하면 핵심 8개: `-k hud -k parity -k skipcal -k test_calendar.py -k deadclick -k twoagree -k pagewait -k currency` |
| 옮긴 폴더(`macro/`, `research/`) | 해당 묶음 |
| 스킬 | `.claude/skills`와 `.agents/skills`를 같게 고친 뒤 `run_tests.py -k skills_sync` |

## 4. 결과 기록 규칙

- 실사이트 실행(T2·T3)은 날짜별 `docs/results/YYYY-MM-DD.md` 한 장에 모은다. 틀:
  **요약 표 → 타임라인(보정 시각) → 계측 → 새로 확인한 사실 → 해석 한계 → 실행 식별자 → 남은 상태.**
- 원본 증거는 `dev-shots/`(Git 제외)에 두고 문서에는 실행 ID·경로만 적는다. 계정·예약번호·승객 정보는 적지 않는다.
- 새로 확인한 사실은 [FACTS](../FACTS.md)의 해당 주제 표에 한 줄로 올리고 결과 문서를 출처로 링크한다.
- 운영 규칙이 바뀌면 해당 명세(`docs/spec/*`)를 고친다. 결과 문서에 운영 지시를 쓰지 않는다.

## 5. 알려진 시험 상태 (2026-10-04)

- 전체 84개 파일: 통과 83 · 실패 0 · 건너뜀 1(`test_site_rehydrate`, 저장 소스 없음). 집 PC(Windows 11, 한글).
- 9/11 부터 이 PC 에서 실패하던 `test_booking_failures`·`test_openwait` 는 **시험 쪽 문제**였다(10/04 수정).
  픽스처 응답에 `charset` 이 없으면 한글 Windows 의 Chromium 이 EUC-KR 로 읽어 `월`·`일` 이 깨진다.
  새 픽스처는 `content_type='text/html; charset=utf-8'` 또는 `<meta charset="utf-8">` 를 붙인다.
