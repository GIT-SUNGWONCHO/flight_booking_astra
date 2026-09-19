# 운영 절차

기준: 2026-09-19, 집 PC(Windows). 동작 원리는 [API 예매 명세](spec/api-booking.md), 규칙은 [AGENTS](../AGENTS.md).
명령은 저장소 루트(`D:\development\ai_project\flight_booking_astra`)의 PowerShell 기준이다.

## 1. 새 PC 준비 (최초 1회)

```powershell
winget install --id Python.Python.3.13 -e --scope user        # Python 이 없을 때
& "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env                                    # 값은 사용자가 메모장에서 직접 채운다
```

- `.env`: `KE_SKYPASS_ID`/`KE_SKYPASS_PW`(와이프, 9232), `KE_LOGIN_MODE=skypass`, `KE_LOGIN_TAB` 비움(숫자면 스카이패스 탭).
  9233은 본인 네이버(Chrome 프로필 세션 사용, 처음 한 번 사용자가 9233 창에서 로그인). 비밀번호는 대화·문서·출력에 쓰지 않는다.
- Git 사용자는 저장소 로컬 설정만(`git config user.name/email`).

## 2. 매일 09:00 실전 (무인)

1. **전날 확인**
   - 일정: `.\.venv\Scripts\python.exe dev\test_calendar.py --day <실행일>` → 노선·편·출발일([calendar](calendar.md)).
   - 캡처 날짜: 같은 노선의 **이미 열린 날짜 중 일반석이 있는 날**(목표와 다른 날). 달력에서 확인.
   - 상태: `.\.venv\Scripts\python.exe api_booking\live_order.py --date <목표> --status` → "남은 주문 의도·전송권 없음"이어야 한다.
2. **작업 등록**(사용자 승인 뒤)
   ```powershell
   powershell -File api_booking\schedule_api_day.ps1 -Name Live0920 -Date 2026-09-20 -At 08:20 -ArgsLine "--mode live --target-date 2027-09-15 --capture-iso 2027-09-13 --origin FCO --destination ICN --flight 932 --family KEBONUSPR --own-mileage 100000 --at 09:00:00 --health-at 08:50:00 --capture-not-before 08:38:00 --reprep-cutoff 08:52:00 --pre-fire-ms 500 --open-retry-gap-ms 50 --observer"
   Get-ScheduledTask -TaskName 'Astra-*' | Get-ScheduledTaskInfo
   ```
3. **당일 흐름**: 08:20 체인 시작(9232 재기동·로그인, 9233 계측 체인) → 08:38 캡처 → 08:50 세션 점검(실패 시 08:52 전 재준비)
   → T-60초 시계 재측정 → T-15초 결속 → 09:00−선발사 발사 → 주문 → 인계 → 알림음.
4. **사용자 역할**: PC 켜 둠·Windows 로그인 유지, 08:20 이후 9232/9233 창 안 클릭 금지, 알림음 뒤 게이트에서 결제 여부 판단.
5. **끝난 뒤**: 콘솔 창 결과 확인 → 조사 모드면 `exit` → 작업 삭제 `schedule_api_day.ps1 -Name Live0920 -Remove`
   → 결과를 `docs/results/<날짜>.md`에 기록 → 주문 실패(`unknown`)면 §4.

## 3. 리허설 종류

| 종류 | 명령 골자 | 주문 |
|---|---|---|
| 주문 없는 경로 점검 | `live_order.py --dry --state-dir dev-shots/state-rehearsal/<이름> …`(열린 날짜 목표) | 없음(캡처는 차단 상태로 수행) |
| 워밍 | `api_day.py --mode rehearsal --family KEBONUSEY --target-date <열린 날> --capture-iso <다른 열린 날> --fire-in-min 5` | 실제 1건(미결제) |
| 콜드 스타트(실전 동일) | 9232 로그아웃·Chrome 전부 종료 → `schedule_api_day.ps1`로 `--mode rehearsal … --at/--health-at/--capture-not-before/--reprep-cutoff --observer` | 실제 1건(미결제) |

리허설은 `dev-shots/state-rehearsal/<runId>/`를 써서 실전 전송권과 섞이지 않는다. 미결제 예약은 결제 기한에 자동 취소된다.

## 4. 주문 실패(`unknown`)·전송권 정리

- 전송권·`unknown`이 있으면 다음 실행이 거부된다(의도된 안전장치).
- 예약번호가 없는 실패(예: business-error)는 사용자 확인 뒤:
  ```powershell
  .\.venv\Scripts\python.exe api_booking\live_order.py --date <목표> --day <그날> --release-no-reference --release-reason "<사유·승인자>"
  ```
  → `dev-shots/state/released/…`로 옮긴다. 예약번호가 있으면 거부되며, 사용자가 예약을 확인하는 별도 절차가 필요하다.
- 예약 목록에 없다는 사실로 좌석 해제를 판단하지 않는다(결제 전 좌석 확보 단계는 목록에 안 보인다).

## 5. 장애 대응

| 증상 | 조치 |
|---|---|
| 로그인 실패(`로그인 필요`) | `.env` 확인, 9232 창에서 추가 인증 요구 여부 확인. 추가 인증이 뜨면 사용자 처리 |
| 캡처 실패(운임칸·통화) | 콘솔 로그의 `통화 준비`·`운임 선택` 줄 확인. 화면 캡처로 원인 확인 후 코드 수정 → `--dry`로 재확인 |
| 종료 코드 3 반복 | 토큰·달력 이탈·저장 상태 원인 확인. 70분 방치 시 로그아웃·홈 이동 사례(9/19) |
| 종료 코드 2 | 주문이 나갔을 수 있다. **재실행 금지**, 상태·증거 확인 |
| 9233 계측 실패 | 예매와 무관. `observer-<runId>.log`·`runs/<runId>/award_observer.json`·`calendar_observer.json` 확인, 필요 시 9233 네이버 재로그인 |
| 남은 live_order | 다음 실전 `api_day --mode live`가 자동 종료한다. 수동 종료는 이 저장소 live_order 명령줄인지 확인 뒤 PID 한정 |

## 6. Chrome·프로필

- `dev-browser.cmd`(9232), `dev-browser2.cmd`(9233) → `dev/astra_browsers.ps1 -Port N [-Restart]`. 전용 프로필 `.debug-profile*`만 다룬다.
- Chrome을 프로세스 이름으로 일괄 종료하지 않는다. 종료는 `dev/close_astra_browser.py --port N` 또는 소유 프로필 PID 한정.
- 연구용 9242(`.api-profile`, `api_booking/open_lab.*`)는 운영과 분리한다.
