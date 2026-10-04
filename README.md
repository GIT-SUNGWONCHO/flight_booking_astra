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
.\.venv\Scripts\python.exe run_tests.py
# 매일 실전 등록: docs/operations.md §2
```

<a id="layout"></a>
## 4. 폴더

| 폴더 | 내용 |
|---|---|
| `api_booking/` | **예매 프로그램.** 무인 체인 `api_day.py`, 실행기 `live_order.py`. [폴더 안내](api_booking/README.md) |
| `dev/` | 예매 프로그램이 함께 쓰는 공용 모듈(로그인 `setup.py`, 시계 `runtime.py`, Chrome `astra_browsers.ps1`)과 계측기. [폴더 안내](dev/README.md) |
| `ke_award/`, `userscript/`, `build.mjs` | 페이지 안에서 도는 스크립트의 원본과 빌드 결과. 로그인 준비와 옛 매크로가 함께 쓴다 |
| `config/` | 일정 `test_calendar.json` |
| `test/` | 페이지 스크립트·계측기·시계 시험과 픽스처 |
| `macro/` | 옛 브라우저 매크로 실행기(보존). [폴더 안내](macro/README.md) |
| `research/` | 조사 도구. 예매에 쓰지 않는다. [폴더 안내](research/README.md) |
| `docs/` | 명세·운영·시험·일정·결과·검토·[증거 견본](docs/samples/README.md)·보관 |
| `run_tests.py` | 시험 일괄 실행. [시험 체계](docs/testing.md) |
| `dev-shots/` | 실행 증거·상태(Git 제외) |

2026-10-04 에 `dev/`·`api_booking/`에 섞여 있던 옛 매크로 실행기와 조사 도구를 `macro/`·`research/`로 갈라냈다.
예매 프로그램과 공용 모듈은 자리를 옮기지 않았다.
