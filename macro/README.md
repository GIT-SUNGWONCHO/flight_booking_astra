# macro/ — 옛 브라우저 매크로 실행기 (보존)

**지금 쓰는 예매 프로그램이 아니다.** 지금 쓰는 것은 [api_booking/](../api_booking/README.md) 이다.
이 폴더는 2026-09-08 ~ 09-12 에 쓰던 **화면을 대신 눌러 주는 방식**의 실행기다.
API 방식이 막혔을 때의 대체 수단으로 남겨 둔다. 2026-09-12 이후 실사이트에서 돌린 적이 없으므로
**다시 쓰려면 리허설부터 해야 한다**(사이트 화면이 바뀌었을 수 있다).

동작 조건·알려진 문제는 [브라우저 예매 명세](../docs/spec/browser-booking.md)에 있다.

## 무엇이 어디에 있나

매크로는 두 부분이다.

| 부분 | 위치 | 하는 일 |
|---|---|---|
| **페이지 안에서 도는 스크립트** | [`ke_award/`](../ke_award/) → `node build.mjs` → [`userscript/`](../userscript/) | 화면 패널(HUD)·녹화한 17단계 재생·정시 발사. **이 폴더에 없다** — 로그인 준비(`dev/setup.py`)가 같은 스크립트를 쓰기 때문에 제자리에 둔다 |
| **그 스크립트를 띄우고 감시하는 실행기** | 이 폴더 | Chrome 에 붙어 스크립트를 주입하고, 준비·점검·기록을 한다 |

| 파일 | 역할 |
|---|---|
| `prepare_day.py` · `astra_target.ps1` | 08:20 준비(로그인·달력·통화). 정시 발사는 하지 않는다 |
| `check_day.py` · `worker_health.py` · `stage_process.py` | 08:50 읽기 전용 점검, 준비 프로세스 수명 관리 |
| `manual_booking.py` | 사람이 화면 패널에서 **대기 시작**을 누르는 예매. 주입 유지와 기록만 한다 |
| `hybrid.py` · `departure_live.py` · `network_trace.py` · `prepare_currency.py` | 달력 선조회, 조회 화면에서 날짜 바꾸기, 네트워크 시각 기록, 통화 KRW |
| `autorun.py` · `daily.py` · `daily.cmd` · `morning.ps1` · `day.ps1` | 무인 실행(초기 방식). 정해진 시각에 준비·발사·기록 |
| `rehearse.py` · `preflight.py` · `ke_setup.py` · `watch_seats.py` · `drive.py` | 리허설, 사전 점검, 준비 재시도, 좌석 계측, 개발용 조작 |
| `_macro_paths.py` | 이 폴더의 실행기가 [`dev/`](../dev/README.md)의 공용 모듈(로그인·시계·일정)을 찾게 한다 |
| `test/` | 이 폴더 실행기의 시험. `python run_tests.py macro` |

페이지 스크립트의 시험은 [`test/`](../test/)에 있다(`python run_tests.py engine`).

## 주의

- `prepare_day.py --preview` 와 `--rehearsal` 은 **실제 예약(미결제)을 만들 수 있다.**
- 결제창(카드사·Npay 창) 안의 승인은 어떤 실행기도 누르지 않는다. 사람만 한다.
