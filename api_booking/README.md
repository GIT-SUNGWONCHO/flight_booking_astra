# api_booking/ 폴더 안내

**보통은 저장소 맨 위의 `astra` 명령으로 쓴다**([쓰는 법](../docs/guide/real-day.md)). 여기는 그 밑에서 도는 프로그램이다.
동작 명세는 [docs/spec/api-booking.md](../docs/spec/api-booking.md), 낮은 수준의 절차는 [docs/operations.md](../docs/operations.md).
이전 개발 계획·연구 기록(D1~D5, 9/14 조사)은 [보관본](../docs/archive/2026-09-19/before-api-README.md)에 있다.

| 구분 | 파일 |
|---|---|
| 운영 체인 | `api_day.py`(체인), `schedule_api_day.ps1`(작업 스케줄러), `live_order.py`(실행기), `capture_date.py`(캡처 날짜 고르기) |
| 판정 | `pipeline.py`, `availability.py`, `fare.py`, `eligibility.py`, `travellers.py`, `payment.py`, `order_flow.py` |
| 브라우저·전송 | `transport.py`, `site_drive.py`, `state_bridge.py`, `connected_bridge.py`, `bridge_guard.py`, `handoff.py`, `resume_gate.py` |
| 안전·증거 | `permit.py`, `order_evidence.py`, `evidence.py`, `recovery.py`, `explicit_retry.py` |
| 시험 | `test_*.py`. 일괄 실행은 저장소 맨 위에서 `python run_tests.py live` |

조사 도구(`collect.py`·`analyze.py`·`award_id_probe.py` 등)는 2026-10-04 에 [research/](../research/README.md)로 옮겼다.
