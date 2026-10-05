# 운영 절차 — `astra` 가 대신 부르는 것들

기준: 2026-10-05, Windows. **쓰는 법은 [실전 안내](guide/real-day.md)에 있다.** 이 문서는 그 밑에서 실제로 도는 명령과,
`astra` 를 거치지 않고 직접 다룰 때의 절차다. 동작 규칙은 [API 예매 명세](spec/api-booking.md), 권한은 [AGENTS](../AGENTS.md).
명령은 저장소 맨 위에서 PowerShell 로 친다. `python` 은 `.\.venv\Scripts\python.exe`.

## 1. `astra` 명령이 하는 일

| 명령 | 실제로 하는 일 |
|---|---|
| `astra setup` | `.env.example` → `.env`, `config/run.example.json` → `config/run.json`(없을 때만). Python·Chrome·패키지 확인 |
| `astra login` | 자리마다 `dev/astra_browsers.ps1 -Port N`(네이버 연동은 `-Restart -Show`) → `dev/setup.py … --api-mode`. 네이버 연동이 로그아웃 상태면 사람이 로그인하길 최대 10분 기다린다 |
| `astra check` | 설정 검증, `.env` 이름이 채워졌는지, 개방일 계산, 자리별 `live_order.py --status --state-dir …`, 예약 작업 목록 |
| `astra check --online` | 위에 더해 자리마다 Chrome 기동 → `setup.py` → 달력 칸 읽기(`capture_date`). 주문·좌석 선택 없음 |
| `astra plan [--commands]` | `astra_config.plan_runs` 결과와 실행될 `api_day.py` 인자 |
| `astra rehearse` | `api_day.py --mode rehearsal …`(일반석, 그 계정의 1순위 자리, `--capture-iso auto`, `--fire-in-min 6`)를 그 창에서 바로 |
| `astra schedule` | 실행마다 `api_booking/schedule_api_day.ps1 -Name <MMDD>-<계정>-<first·fallback> -Date … -At … -ArgsLine "…"` |
| `astra schedule --remove` / `--list` | 같은 스크립트 `-Remove` / `Get-ScheduledTask 'Astra-*'` |
| `astra run [--again] [--only 이름]` | 같은 스크립트 `-Now`(작업 등록 없이 콘솔 창 묶음을 바로 띄운다) |
| `astra status` | `dev-shots/api-day/live-<날짜>-*.json`, 자리별 `order-intent-<날짜>.json`, 신호 파일을 읽어 문장으로 |

작업 스케줄러 등록·삭제는 사용자가 `astra schedule` 로 직접 하거나, AI 가 할 때는 매번 승인을 받는다([AGENTS §3](../AGENTS.md)).

## 2. 한 실행의 낮은 수준 명령

`astra plan --commands` 가 보여 주는 줄 그대로다. 본보기(9/25 구성)의 첫 실행:

```powershell
python api_booking\api_day.py --mode live --target-date 2027-09-20 --capture-iso auto `
  --origin FCO --destination ICN --flight 932 --port 9232 --family KEBONUSPR --own-mileage 100000 `
  --pre-fire-ms 800 --at 09:00:00 --health-at 08:50:00 --capture-not-before 08:38:00 --reprep-cutoff 08:52:00 `
  --live-state-dir dev-shots/state-9232 --order-outcome-file dev-shots/gate/2026-09-25-wife.json --observer
```

대체 실행은 `--family KEBONUSEY --at 09:00:04 --pre-fire-ms 500 --order-gate-file <같은 신호 파일> --order-gate-timeout 5`.

당일 흐름: 08:20 체인 시작(Chrome 재기동·로그인, 첫 실행은 계측 체인도) → 캡처 날짜 고르기 → 08:38 캡처 통과 →
08:50 세션 점검(실패 시 08:52 전 재준비) → T-60초 시계 재측정 → T-15초 결속 → 발사 → 주문 → 인계 → 알림음.

설정 파일 없이 예전처럼 직접 부를 수도 있다. 그때는 `.env` 의 `KE_LOGIN_MODE=skypass` 가 9232·9243 의 로그인 방식을 정한다.

## 3. 연습의 종류

| 종류 | 명령 골자 | 주문 |
|---|---|---|
| 주문 없는 경로 점검 | `live_order.py --dry --state-dir dev-shots/state-rehearsal/<이름> …`(열린 날짜 목표) | 없음(캡처는 차단 상태로 수행) |
| 연습(`astra rehearse`) | `api_day.py --mode rehearsal --family KEBONUSEY --target-date <열린 날> --capture-iso auto --fire-in-min 6` | 실제 1건(미결제) |
| 실전과 같은 시각 흐름 | `schedule_api_day.ps1` 로 `--mode rehearsal … --at/--health-at/--capture-not-before/--reprep-cutoff [--observer]` | 실제 1건(미결제) |

연습은 `dev-shots/state-rehearsal/<runId>/` 를 써서 실전 기록과 섞이지 않는다. 미결제 예약은 스스로 풀린다(10~32분을 봤다).

## 4. 주문 기록(전송권·의도)

- 12시간 넘은 기록은 다음 실행이 `released/<시각>-auto/` 로 옮긴다. 사람이 할 일이 없다.
- 12시간 안에 주문을 보낸 자리는 새 실행이 주문하지 않는다. 확인: `python api_booking\live_order.py --date <목표> --status --state-dir dev-shots\state-<포트>`.
- 그래도 다시 보내려면 `astra run --again`(또는 `api_day.py … --again`). **사용자가 지시했을 때만.** 앞선 주문이 좌석을
  잡았으면 좌석이 하나 더 잡힌다. 결과가 `unknown` 이면 먼저 대한항공 예약 조회로 확인한다.
- 기록 파일을 손으로 옮기거나 지우지 않는다. 예약 목록에 없다는 사실로 좌석 해제를 판단하지 않는다(결제 전 좌석 확보 단계는 목록에 안 보인다).

## 5. 장애 대응

증상별 처리는 [문제 해결](guide/troubleshooting.md). 종료 코드만 다시 적는다.

| `live_order` 종료 코드 | 뜻 | `api_day` |
|---|---|---|
| 0 | 완료, 사용자 차례, 또는 대체 게이트가 주문하지 않음 | 끝 |
| 3 | 주문 전 준비 무효(세션·통화·저장 상태) | 로그인부터 재준비 |
| 4 | 준비 통과 실패, 주문 요청 없음 | Chrome 재기동 뒤 재준비 |
| 1 | 예외로 죽음 | 상태 폴더에 전송권이 없고 의도가 준비 단계면 4 와 같이. 아니면 끝 |
| 2 | 거부·실패. **주문이 나갔을 수 있다** | 끝. 다시 실행하지 않는다 |

재준비는 `--max-attempts`(기본 3)와 `--reprep-cutoff` 안에서만 한다.

그 밖에:

- 작업이 제시각에 안 뜸: 같은 이름의 이전 콘솔이 `pause` 로 남아 있으면 새 기동이 무시된다(9/20).
- 계측 실패: 예매와 무관. `observer-<runId>.log`·`runs/<runId>/award_observer.json`.
- 남은 `live_order`: 같은 자리의 다음 실전 `api_day --mode live` 가 스스로 종료시킨다. 손으로 할 때는 이 저장소의
  `live_order` 명령줄인지 확인하고 PID 를 지정한다.

## 6. Chrome·프로필

- 자리는 다섯 개로 고정이다([설정 §1.6](guide/config.md#16-브라우저-자리)). `dev/astra_browsers.ps1 -Port N [-Restart] [-Show]` 가
  그 자리의 전용 프로필만 다룬다.
- Chrome 을 프로세스 이름으로 일괄 종료하지 않는다. 종료는 `python dev\close_astra_browser.py --port N`.
- API 예매·계측 창에는 `setup.py --api-mode` 로 들어간다(매크로 패널·재생 엔진을 싣지 않는다).
  옛 매크로 실행기(`macro/`)는 그 표시 없이 들어간다. 두 방식을 같은 창에서 섞지 않는다.
- 조사용 9242 전용 기동 스크립트 `research/open_lab.*` 는 예매와 같은 프로필(`.api-profile`)을 쓴다. 예매 준비 중에는 쓰지 않는다.
