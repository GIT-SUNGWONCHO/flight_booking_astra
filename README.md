# ASTRA — 대한항공 마일리지 좌석을 개방 시각에 잡는 프로그램

대한항공 마일리지(보너스) 좌석은 **출발 360일 전 09:00(한국 시각)** 에 열리고, 인기 노선의 프레스티지는 몇 초 만에
사라진다. 이 프로그램은 그 시각에 맞춰 **조회 → 운임 → 주문**을 2~3초 안에 보내 좌석을 잡고, 결제창을 열어 둔다.

- **결제는 사람이 한다.** 프로그램은 결제창 안에서 아무것도 누르지 않는다.
- 2026-09-25 에 이 프로그램으로 로마→인천 KE932(2027-09-20) **프레스티지 1석과 일반석 1석**을 두 계정으로 잡았다.
- 돌리면 잡히는 도구는 아니다. 프레스티지가 한 석뿐인 날은 여섯 번 중 한 번 잡았다. 안 되면 일반석으로 돌려
  빈손을 피하게 돼 있다. [왜 이렇게 만들었나](docs/guide/why.md)

## 1. 쓰는 순서

Windows PC, Google Chrome, Python 이 필요하다. 명령은 이 폴더에서 PowerShell 로 친다.

| 순서 | 할 일 | 명령 | 설명 |
|---|---|---|---|
| 1 | 설치 | `.\astra.cmd setup` | [설치](docs/guide/install.md) |
| 2 | 설정: 노선·날짜·계정·마일리지 | 메모장으로 `config\run.json`, `.env` | [설정](docs/guide/config.md) |
| 3 | 설정 확인 | `.\astra.cmd check` | |
| 4 | 로그인 | `.\astra.cmd login` | [로그인](docs/guide/login.md) |
| 5 | **연습**(이미 열린 날짜로 끝까지) | `.\astra.cmd rehearse` | [연습](docs/guide/rehearse.md) |
| 6 | 전날 저녁: 점검하고 예약 | `.\astra.cmd check --online` → `.\astra.cmd schedule` | [실전](docs/guide/real-day.md) |
| 7 | 그날 09:00: 알림음이 나면 결제 | `.\astra.cmd status` | [실전 §4](docs/guide/real-day.md) |

무엇이 언제 실행될지는 `.\astra.cmd plan`, 안 될 때는 [문제 해결](docs/guide/troubleshooting.md).

## 2. 먼저 알아 둘 것

- **연습을 건너뛰지 않는다.** 대한항공 사이트는 바뀐다. 2026-10-05 에 열흘 만에 돌려 보니 주문 직전 점검이 막혀 있었다
  (고쳤다). 실전 며칠 전에 연습이 끝까지 가는지 반드시 본다.
- **좌석은 결제해야 내 것이 된다.** 결제하지 않으면 대한항공이 푼다(10~32분을 봤다). 연습에서 생기는 예약도 그렇게 풀린다.
- **결제수단을 확인한다.** 기본 설정은 한국 도착편에 현대카드, 출발편에 네이버페이 결제창을 연다. 그것을 쓰지 않으면
  설정의 `"payment"` 를 `"manual"` 로 둔다 — 결제수단 고르기 앞에서 멈춘다. [설정 §1.7](docs/guide/config.md)
- **그날 아침 PC 가 켜져 있고 Windows 에 로그인돼 있어야 한다.** [설치 §4](docs/guide/install.md)
- **마일리지 잔액을 직접 적는다.** 프로그램이 읽어 오지 않는다. [설정 §1.2](docs/guide/config.md)
- 자동화 접근은 대한항공 이용약관에 어긋날 수 있다. 본인·가족 계정으로 본인 여정에만 쓴다.

## 3. 이 폴더를 남에게 줄 때

아래는 **빼고** 준다. 내 비밀번호와 로그인된 브라우저다.

- `.env`
- `.debug-profile`, `.debug-profile2`, `.debug-profile3`, `.api-profile`, `.api-profile2`
- `config\run.json` (내 계정 이름·마일리지), `dev-shots\` (내 예약 기록)

Git 으로 받으면 이것들은 처음부터 들어 있지 않다.

## 4. 문서

**쓰는 사람**

| 문서 | 내용 |
|---|---|
| [설치](docs/guide/install.md) · [설정](docs/guide/config.md) · [로그인](docs/guide/login.md) | 준비 |
| [연습](docs/guide/rehearse.md) · [실전](docs/guide/real-day.md) · [문제 해결](docs/guide/troubleshooting.md) | 실행 |
| [동작 원리](docs/guide/how-it-works.md) · [왜 이렇게 만들었나](docs/guide/why.md) | 무엇을 왜 하는지와 근거 |
| [증거 견본](docs/samples/README.md) | 성공한 날의 실제 기록. 내 기록과 견줘 볼 때 |
| [옛 브라우저 매크로](docs/guide/macro.md) | 화면을 대신 눌러 주는 예전 방식(보존) |

**고치는 사람**(사람이든 AI 든)

| 문서 | 한 가지 역할 |
|---|---|
| [AGENTS](AGENTS.md) | 작업 규칙·권한·금지 사항 |
| [NOW](NOW.md) | 현재 상태 한 장 |
| [FACTS](FACTS.md) | 주제별 실측 사실과 해석 한계, 거둔 주장 |
| [docs/spec/](docs/spec/) | 구성 요소별 동작 명세 |
| [docs/operations.md](docs/operations.md) | 낮은 수준의 실행 절차(`astra` 가 대신 불러 주는 명령들) |
| [docs/testing.md](docs/testing.md) | 시험 단계·명령·변경별 필수 시험 |
| [docs/results/](docs/results/) | 날짜별 실사이트 실행 결과 |
| [docs/review/](docs/review/) | 외부 검토 대조·가설 검토. **확정 사실이 아니다** |
| [docs/calendar.md](docs/calendar.md) | 2026-09 시험 일정(생성본. 원본 `config/test_calendar.json`) |
| [docs/archive/](docs/archive/) | 과거 문서. 실행 지시가 아니다 |

같은 내용을 두 문서의 원본으로 두지 않는다. 결과는 results → 사실은 FACTS → 동작은 spec → 쓰는 법은 guide → 상태는 NOW.

<a id="layout"></a>
## 5. 폴더

| 폴더·파일 | 내용 |
|---|---|
| `astra.cmd` · `astra.py` | **입구.** 모든 명령 |
| `config/` | `run.example.json`(설정 본보기), `run.json`(내 설정, Git 제외), `test_calendar.json`(2026-09 일정) |
| `api_booking/` | 예매 프로그램 본체. [폴더 안내](api_booking/README.md) |
| `dev/` | 예매 프로그램이 함께 쓰는 공용 모듈(로그인·시계·Chrome·설정 읽기)과 계측기. [폴더 안내](dev/README.md) |
| `ke_award/` · `userscript/` · `build.mjs` | 페이지 안에서 도는 스크립트의 원본과 빌드 결과. 로그인 준비와 옛 매크로가 쓴다 |
| `macro/` | 옛 브라우저 매크로 실행기(보존). [폴더 안내](macro/README.md) |
| `research/` | 조사 도구. 예매에 쓰지 않는다. [폴더 안내](research/README.md) |
| `test/` · `run_tests.py` | 시험과 일괄 실행. [시험 체계](docs/testing.md) |
| `docs/` | 문서 |
| `dev-shots/` | 실행 기록·상태(Git 제외) |
