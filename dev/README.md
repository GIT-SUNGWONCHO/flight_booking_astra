# dev/ 폴더 안내

**예매 프로그램([api_booking/](../api_booking/README.md))이 함께 쓰는 공용 모듈과 계측기**만 있다.

| 구분 | 파일 | 쓰는 곳 |
|---|---|---|
| **브라우저·로그인** | `astra_browsers.ps1`/`.sh`·`browsers.ps1`(전용 Chrome 띄우기), `setup.py`(로그인·달력 준비), `browser_identity.py`(창 이름표), `session_health.py`(로그인 만료 확인), `close_astra_browser.py` | 예매·계측 |
| **시계·실행 기록** | `runtime.py`(`measure_clock`·실행 ID·저장), `server_clock.py`, `browser_timing.py` | 예매·계측 |
| **일정** | `test_calendar.py` + [`config/test_calendar.json`](../config/test_calendar.json) → [docs/calendar.md](../docs/calendar.md) | 계측·옛 매크로 |
| **계측기** | `observer_chain.py`(9233 체인), `award_observer.py`(좌석 수), `calendar_observer.py` | [계측 명세](../docs/spec/observer.md) |
| **그 밖** | `payment_window.py`(결제 제공자 판정), `ke_review.py`(Codex 교차 검토 - 개발 보조) | |

2026-10-04 에 갈라낸 것: 옛 브라우저 매크로 실행기는 [macro/](../macro/README.md), 조사 도구는 [research/](../research/README.md).
