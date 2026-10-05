# 설치 — 새 PC 에서 처음 한 번

Windows 10/11 기준이다. 30분쯤 걸린다. 끝나면 [설정](config.md)으로 간다.

## 1. 필요한 것

| 무엇 | 왜 | 어디서 |
|---|---|---|
| Windows PC | 예약 실행이 Windows 작업 스케줄러와 콘솔 창을 쓴다 | - |
| Google Chrome | 프로그램이 전용 Chrome 창을 띄워 그 안에서 요청을 보낸다 | https://www.google.com/chrome |
| Python 3.10 이상 | 프로그램이 파이썬으로 돼 있다 | https://www.python.org/downloads/ |
| 대한항공 계정 | 스카이패스 아이디·비밀번호, 또는 네이버 연동 계정 | - |
| 결제 수단 | 좌석을 잡은 뒤 결제는 사람이 한다 | [실전 §5](real-day.md) |

Python 을 설치할 때 첫 화면의 **"Add python.exe to PATH"** 를 반드시 켠다.

macOS 에서는 로그인·점검·연습(`astra.py` 를 직접 실행)까지는 되도록 만들어져 있지만 2026-09 이후 확인하지 않았고,
예약(`schedule`)과 바로 실행(`run`)은 되지 않는다. 이 문서는 Windows 만 다룬다.

## 2. 폴더 받기

받은 폴더를 한글·공백이 없는 짧은 경로에 둔다. 예: `D:\astra`.

- 압축 파일로 받았으면 그 자리에 푼다.
- Git 을 쓰면 `git clone <주소> D:\astra`.

**남에게서 받은 폴더에 `.env` 나 `.debug-profile`·`.api-profile` 로 시작하는 폴더가 들어 있으면 지운다.**
그것은 준 사람의 비밀번호와 로그인 세션이다.

## 3. 처음 실행

폴더에서 PowerShell(또는 명령 프롬프트)을 열고:

```powershell
.\astra.cmd setup
```

처음에는 파이썬 실행 환경(`.venv` 폴더)을 만들고 필요한 패키지를 받느라 몇 분 걸린다. 그 뒤:

```
[ 확인 ] .env 를 만들었다
[ 확인 ] config\run.json 를 만들었다
[ 확인 ] 파이썬 3.13.x
[ 확인 ] Chrome: C:\Program Files\Google\Chrome\Application\chrome.exe
[ 확인 ] 필요한 패키지가 설치돼 있다
```

`[ 문제 ]` 줄이 있으면 그 줄이 말하는 대로 고치고 다시 실행한다.

- "Python 3 was not found" → Python 을 설치(위 표)하고 PowerShell 창을 새로 연 뒤 다시.
- PowerShell 이 "스크립트를 실행할 수 없습니다"라고 하면 `astra.cmd` 가 아니라 `.ps1` 을 직접 부른 것이다. `.\astra.cmd` 로 부른다.

## 4. PC 설정 — 실전 날 아침에 PC 가 깨어 있어야 한다

프로그램은 08:20 에 스스로 시작한다. 그때 PC 가 꺼져 있거나 Windows 에서 로그아웃돼 있으면 아무 일도 일어나지 않는다.

| 설정 | 어떻게 |
|---|---|
| 전원 | 노트북이면 전원 어댑터를 꽂아 둔다 |
| 절전 | 설정 → 시스템 → 전원 → "화면 및 절전"에서 **절전 모드: 안 함**(전원 연결 시). 프로그램이 도는 동안에는 스스로 절전을 막지만, 08:20 전에 잠들어 있으면 깨우지 못할 수 있다 |
| 로그인 | 전날 밤 Windows 에 **로그인한 채로** 둔다. 화면 잠금(Win+L)은 괜찮다. 로그아웃·사용자 전환·종료는 안 된다 |
| Windows 업데이트 | 그날 새벽에 재부팅되지 않게 "업데이트 일시 중지"를 걸어 둔다 |
| 시계 | 맞추지 않아도 된다. 프로그램이 인터넷 시각 서버와 견줘 스스로 보정한다(수 초 틀려 있어도 된다) |
| 인터넷 | 유선이 낫다. 와이파이도 된다(2026-09 은 전부 가정용 회선에서 했다) |

## 5. 잘 설치됐는지

```powershell
.\astra.cmd check
```

설정을 아직 안 고쳤으면 본보기 값으로 읽힌다. `[ 문제 ]` 가 없으면 설치는 끝났다.

프로그램 자체를 시험해 보려면(사이트에 접속하지 않는다, 5분):

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium     # 시험용 브라우저, 처음 한 번
.\.venv\Scripts\python.exe run_tests.py
```

마지막 줄이 `통과 NN · 실패 0` 이면 된다. 자세한 것은 [시험 체계](../testing.md).

## 6. 다음

1. [설정](config.md) — 노선·날짜·계정·마일리지
2. [로그인](login.md)
3. [연습](rehearse.md)
4. [실전](real-day.md)
