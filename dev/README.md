# dev/ 폴더 안내

세 가지가 섞여 있다(9/25 이후 재배치 예정, [README §5](../README.md#layout)).

| 구분 | 파일 | 쓰는 곳 |
|---|---|---|
| **공용** | `setup.py`(로그인·달력 준비), `runtime.py`(`measure_clock`·실행 ID·저장), `astra_browsers.ps1`/`.sh`·`browsers.ps1`(전용 Chrome), `close_astra_browser.py`, `browser_identity.py`, `session_health.py`(토큰 만료), `prepare_currency.py`(KRW), `payment_window.py`(결제 제공자 판정), `test_calendar.py`(일정), `ke_review.py`(Codex 교차 검토) | API 예매·계측·브라우저 예매 |
| **계측기** | `observer_chain.py`(9233 체인), `award_observer.py`(예매 조회·좌석 수), `calendar_observer.py`, `browser_timing.py` | [계측 명세](../docs/spec/observer.md) |
| **브라우저 예매(보류)** | `prepare_day.py`, `check_day.py`, `worker_health.py`, `stage_process.py`, `manual_booking.py`, `hybrid.py`, `departure_live.py`, `network_trace.py`, `autorun.py`, `daily.py`, `rehearse.py`, `ke_setup.py`, `watch_seats.py`, `preflight.py`, `drive.py`, `day.ps1`, `morning.ps1`, `astra_target.ps1`, `daily.cmd` | [브라우저 예매 명세](../docs/spec/browser-booking.md) |
