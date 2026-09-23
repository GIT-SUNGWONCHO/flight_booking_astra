# Astra — 대한항공 마일리지 좌석 09:00 예매

09:00(KST)에 열리는 360일 뒤 마일리지 좌석을 잡는 도구 모음이다. 결제는 항상 사용자가 한다.
작업 규칙은 [AGENTS](AGENTS.md), 지금 상태는 [NOW](NOW.md).

## 1. 구성 요소

| 구성 | 상태 | 포트·계정 | 명세 |
|---|---|---|---|
| **API 예매** | **주력**. 무인 체인(작업 스케줄러 08:20 → 09:00 발사) | 9232 `.debug-profile`, 와이프 스카이패스(.env) | [docs/spec/api-booking.md](docs/spec/api-booking.md) |
| **계측기** | 운영. 예매와 독립(실패해도 예매 계속) | 9233 `.debug-profile2`, 본인 네이버 | [docs/spec/observer.md](docs/spec/observer.md) |
| 브라우저(UI 매크로) 예매 | **보류·대체 수단** | 9232 | [docs/spec/browser-booking.md](docs/spec/browser-booking.md) |
| API 연구 도구 | 연구 전용 | 9242 `.api-profile` | [api_booking/README.md](api_booking/README.md) |

## 2. 문서 지도

| 문서 | 한 가지 역할 |
|---|---|
| [AGENTS](AGENTS.md) | AI 작업 규칙·권한·금지 사항 |
| [NOW](NOW.md) | 현재 상태 한 장: 다음 목표·남은 상태·다음 작업 |
| [FACTS](FACTS.md) | 주제별 실측 사실과 해석 한계 |
| [docs/spec/](docs/spec/) | 구성 요소별 동작 명세 |
| [docs/operations.md](docs/operations.md) | 실행 절차: 새 PC 준비, 매일 실전, 리허설, 실패 정리, 장애 대응 |
| [docs/testing.md](docs/testing.md) | 시험 단계(T1~T3)·명령·변경별 필수 시험·결과 기록 규칙 |
| [docs/calendar.md](docs/calendar.md) | 테스트 일정(생성본. 원본 `config/test_calendar.json`) |
| [docs/results/](docs/results/) | 날짜별 실사이트 실행 결과 |
| [docs/review/](docs/review/) | 외부 검토 대조·가설 검토. **확정 사실이 아니다**(확정분만 FACTS로 올린다) |
| [docs/archive/](docs/archive/) | 과거 문서·증거. 실행 지시가 아니다 |

같은 내용을 두 문서의 원본으로 두지 않는다. 결과는 results → 사실은 FACTS → 동작은 spec → 상태는 NOW 순서로 반영한다.

## 3. 빠른 시작

```powershell
# 일정 확인
.\.venv\Scripts\python.exe dev\test_calendar.py --day 2026-09-20
# 실전 상태(전송권·미해결 주문) 확인
.\.venv\Scripts\python.exe api_booking\live_order.py --date 2027-09-15 --status
# 로컬 시험
.\.venv\Scripts\python.exe api_booking\run_tests.py
# 매일 실전 등록: docs/operations.md §2
```

<a id="layout"></a>
## 4. 폴더

| 폴더 | 내용 |
|---|---|
| `api_booking/` | API 예매 운영 체인(`api_day.py`, `live_order.py` …)·연구 도구·시험. [폴더 안내](api_booking/README.md) |
| `dev/` | 공용(로그인 `setup.py`, 시계 `runtime.py`, Chrome `astra_browsers.ps1`) + 계측기 + 브라우저 예매 실행기. [폴더 안내](dev/README.md) |
| `ke_award/`, `userscript/`, `build.mjs` | 브라우저 매크로 JS 원본·빌드 산출물, 계측 프로브(`calendar_probe.js`) |
| `config/` | 공통 일정 `test_calendar.json` |
| `test/` | 브라우저 매크로·계측·일정·공용 시험(`run-tests.ps1`) |
| `docs/` | 명세·운영·시험·일정·결과·보관 |
| `dev-shots/` | 실행 증거·상태(Git 제외) |

## 5. 폴더 재배치 계획 (9/25 이후)

지금은 `dev/`에 공용·계측·브라우저 예매가 섞여 있다. 매일 09:00 테스트 중 경로를 바꾸면 사고 위험이 커서 9/25 중요 목표 이후
한 번에 옮긴다: `common/`(로그인·시계·Chrome·세션), `booking_api/`(+`tools/`, `tests/`), `booking_browser/`, `observer/`,
그리고 세 묶음 전체 시험 실행기. 옮긴 뒤 전체 T1과 `--dry` 경로 점검을 한 번 돌린다.
