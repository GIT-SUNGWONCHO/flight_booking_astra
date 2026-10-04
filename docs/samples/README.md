# 증거 견본 — 정상일 때 이렇게 남는다

2026-09-25 본선(프레스티지 1 + 일반석 1 확보)의 **실제 기록**에서 예약번호와 승객 지문만 지운 것이다.
프로그램이 잘 안 될 때 **내 기록을 이 견본과 나란히 놓고 어디서 달라지는지** 본다.
그날 무슨 일이 있었는지는 [9/25 결과](../results/2026-09-25.md)에 있다.

원본은 실행한 PC의 `dev-shots/` 에 생긴다. 이 폴더는 Git 에 올라가지 않는다(개인 기록이 섞이기 때문이다).

## 1. 콘솔 로그 — 제일 먼저 본다

실행하면 뜨는 검은 창의 내용이다. 같은 내용이 `dev-shots/api-day/<이름>-<날짜>-<시각>-console.log` 로 남는다.
(Windows 가 저장한 원본은 UTF-16 이다. 메모장으로는 그냥 열린다.)

| 견본 | 상황 | 여기를 본다 |
|---|---|---|
| [console-1-success.log](console-1-success.log) | 처음부터 끝까지 정상. 08:20 기동 → 08:38 준비 → 09:00 발사 → 결제창 | 전체 순서의 기준 |
| [console-2-rejected.log](console-2-rejected.log) | 주문이 `ERT.15012` 로 거절됨(좌석을 남이 먼저 잡음) | `판정=business-error` 줄. **프로그램 고장이 아니다** |
| [console-3-fallback.log](console-3-fallback.log) | 프레스티지가 거절되자 일반석으로 돌려 잡음 | `대체 게이트: 신호=proceed` 줄 |
| [console-4-capture-failed.log](console-4-capture-failed.log) | 준비 단계에서 실패(`검색 이동 없음`). 주문은 나가지 않았다 | `준비 통과 결과` 에 `'search': False` |

정상 실행에서 반드시 나오는 줄(순서대로):

```
달력 도착                     로그인과 노선 설정이 끝났다
준비 통과 결과: {... 'contact': True ...}    이미 열린 날짜로 끝까지 한 번 가 봤다(주문은 차단)
[arm] 점검 통과                발사 준비 완료. 이제 정각까지 기다린다
[health] 점검 통과             10분 전 로그인이 살아 있다
시계 보정(최종)                1분 전 시계를 다시 맞췄다
T0 발사                       조회를 보냈다
조회#1 ... 판정=selected       좌석이 있다
운임 ... 판정=validated
주문 ... 판정=order-recorded   ← 좌석을 잡았다
[사용자 차례] 현대카드 창이 열렸다   ← 여기서부터 사람이 결제한다
```

어느 줄에서 끊겼는지가 곧 원인의 위치다.

## 2. 파일로 남는 기록

| 견본 | 원본 위치 | 내용 |
|---|---|---|
| [api-day-report.json](api-day-report.json) | `dev-shots/api-day/live-<실행ID>.json` | 한 번의 실행 요약. 단계별 종료 코드, 발사 시각 |
| [fire-timing.json](fire-timing.json) | `dev-shots/<상태폴더>/fire-timing-<날짜>-<ID>.json` | 시계 보정값, 조회를 보낸 시각·받은 시각. `sentVsOpenMs` 가 정각 대비 몇 ms 였는지 |
| [order-received-success.json](order-received-success.json) | `dev-shots/<상태폴더>/order-evidence/<ID>/received.json` | 주문 응답. `segmentStatus: "HK"` 면 좌석 확보. `orderRequestStartedAt` 이 **주문을 보낸 정확한 시각** |
| [order-received-rejected.json](order-received-rejected.json) | 같은 위치 | 거절된 주문. `diagnostic.error.code` 에 사유 |
| [order-judgment.json](order-judgment.json) | `…/order-evidence/<ID>/judgment.json` | 프로그램의 최종 판정 한 줄 |
| [gate-signal-rejected.json](gate-signal-rejected.json) · [gate-signal-taken.json](gate-signal-taken.json) | `dev-shots/gate/<이름>.json` | 프레스티지 실행이 일반석 실행에게 남기는 신호. `business-error` + `referencePresent: false` 일 때만 일반석이 주문한다 |
| [award_observer.json](award_observer.json) | `dev-shots/runs/<실행ID>/award_observer.json` | 계측기(9233)가 정각 전후 150ms 간격으로 본 좌석 수. `seatChanges` 가 좌석이 줄어든 순간 |

시각 읽는 법(헷갈리기 쉽다):

- 콘솔 로그의 `[09:00:02.152]` 는 **PC 시계**다. 보정 전이다.
- `fire-timing.json` 과 `award_observer.json` 의 `edge`·`seatChanges` 는 **보정한 시각**이다(09:00:00 = 0초).
- `award_observer.json` 의 `events[]` 안 `sentAt`·`receivedAt` 은 **보정 전 PC 시각(밀리초)** 이다.
- 자세한 규칙은 [계측 명세 §5.1](../spec/observer.md).

## 3. 안 될 때 무엇을 모아서 물어보나

1. 콘솔 로그 전체(`dev-shots/api-day/*-console.log`).
2. 같은 실행의 `live-<실행ID>.json`.
3. 발사까지 갔다면 `fire-timing-*.json` 과 `order-evidence/<ID>/` 폴더.
4. 계측을 켰다면 `dev-shots/runs/<실행ID>/award_observer.json`.

**남에게 보내기 전에** `received.json` 의 `pnr`(예약번호)을 지운다. 그 밖의 파일에는 이름·회원번호·비밀번호·
로그인 토큰이 들어가지 않게 만들어져 있지만, 보내기 전에 한 번 열어 확인한다.
`.env` 와 `.debug-profile*`·`.api-profile*` 폴더(로그인된 Chrome)는 **절대 보내지 않는다.**
