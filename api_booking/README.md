# api_booking/ 폴더 안내

동작 명세는 [docs/spec/api-booking.md](../docs/spec/api-booking.md), 실행 절차는 [docs/operations.md](../docs/operations.md).
이전 개발 계획·연구 기록(D1~D5, 9/14 조사)은 [보관본](../docs/archive/2026-09-19/before-api-README.md)에 있다.

| 구분 | 파일 |
|---|---|
| 운영 체인 | `api_day.py`(체인), `schedule_api_day.ps1`(작업 스케줄러), `live_order.py`(실행기) |
| 판정 | `pipeline.py`, `availability.py`, `fare.py`, `eligibility.py`, `travellers.py`, `payment.py`, `order_flow.py` |
| 브라우저·전송 | `transport.py`, `site_drive.py`, `state_bridge.py`, `connected_bridge.py`, `bridge_guard.py`, `handoff.py`, `resume_gate.py` |
| 안전·증거 | `permit.py`, `order_evidence.py`, `evidence.py`, `recovery.py`, `explicit_retry.py` |
| 연구 도구(운영 미사용) | `collect.py`, `contracts.py`, `analyze.py`, `deadline.py`(잘못된 마감 가정 포함), `open_lab.ps1`/`.sh`(9242) |
| 시험 | `test_*.py`, 일괄 실행 `run_tests.py` |
