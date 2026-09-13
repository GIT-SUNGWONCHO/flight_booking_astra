#!/usr/bin/env bash
# open_lab.ps1 의 macOS 대응. API 연구용 9242 Chrome만 연다.
# 종료 코드: 0 준비됨 / 1 없음·준비 실패 / 2 알 수 없는 프로세스가 9242 점유.
# 운영 9232/9233 프로필을 건드리거나 복사하지 않고, 어떤 프로세스도 종료하지 않는다.
#
# 윈도우판의 Win32_Process 소유권 검사를 여기서는 lsof + ps 로 재현한다.
# dev/astra_browsers.sh 는 포트 존재만 보지만 이 스크립트는 포트를 쓰는 프로세스의
# 명령줄에 우리 포트와 우리 프로필이 함께 있는지까지 확인한다.
set -euo pipefail

PORT=9242
PROFILE_NAME=.api-profile
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
START_URL="https://www.koreanair.com/kr/ko"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="$ROOT/$PROFILE_NAME"
case "$PROFILE" in
  "$ROOT"/*) ;;
  *) echo "API profile outside Astra workspace" >&2; exit 1;;
esac
# 아래 소유권 검사는 인자를 공백으로 구분해 대조한다. 경로에 공백이 있으면 성립하지 않는다.
case "$PROFILE" in
  *[[:space:]]*) echo "API profile path must not contain whitespace" >&2; exit 1;;
esac

STATUS_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --status) STATUS_ONLY=1; shift;;
    -h|--help) echo "usage: open_lab.sh [--status]"; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

listeners() { lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true; }

# 그 PID 의 명령줄에 우리 포트와 우리 프로필이 인자 하나로 모두 있는지 본다.
# 부분 일치를 쓰면 `.api-profile-other` 같은 다른 프로필도 우리 것으로 인정된다.
# 앞뒤를 공백으로 감싸 인자 경계까지 대조한다(윈도우판의 (?:^|\s)...(?:\s|$)와 같은 뜻).
owned_pid() {
  local pid cmd padded
  for pid in $(listeners); do
    cmd="$(ps -ww -o command= -p "$pid" 2>/dev/null || true)"
    [ -n "$cmd" ] || continue
    padded=" $cmd "
    case "$padded" in
      *" --remote-debugging-port=$PORT "*)
        case "$padded" in
          *" --user-data-dir=$PROFILE "*) echo "$pid"; return 0;;
        esac;;
    esac
  done
  return 1
}

endpoint_ready() {
  curl -fsS --max-time 2 "http://127.0.0.1:$PORT/json/version" 2>/dev/null \
    | grep -q webSocketDebuggerUrl
}

report_status() {
  local pids owner
  pids="$(listeners)"
  if [ -z "$pids" ]; then echo "9242 사용 안 함 (연구 Chrome 없음)"; return 1; fi
  if owner="$(owned_pid)"; then
    if endpoint_ready; then
      echo "연구 Chrome 9242 사용 중 PID $owner (준비됨)"
      return 0
    fi
    # 우리 Chrome 이지만 아직 쓸 수 없다. 종료 코드 계약상 준비됨(0)이 아니다.
    echo "연구 Chrome 9242 사용 중 PID $owner (CDP 응답 없음)" >&2
    return 1
  fi
  echo "9242를 알 수 없는 프로세스가 점유 중: PID $(echo "$pids" | tr '\n' ' ')" >&2
  return 2
}

if [ "$STATUS_ONLY" = "1" ]; then
  report_status && exit 0 || exit $?
fi

if [ -n "$(listeners)" ]; then
  # 우리 것이 아니면 열지도 끄지도 않는다. 어떤 프로세스도 종료하지 않는다.
  if ! owned_pid >/dev/null; then
    report_status || true
    exit 2
  fi
else
  [ -x "$CHROME" ] || { echo "Chrome not found: $CHROME" >&2; exit 1; }
  "$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
    --no-first-run --no-default-browser-check --window-size=1600,1000 \
    --disable-popup-blocking --disable-background-timer-throttling \
    --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
    "$START_URL" >/dev/null 2>&1 &
fi

# 찬 시작은 10초를 넘길 수 있다. 윈도우판(20회)보다 길게 기다린다.
for _ in $(seq 1 60); do
  if endpoint_ready && owned_pid >/dev/null; then
    echo "Astra API research Chrome 9242 ready (PID $(owned_pid))"
    exit 0
  fi
  sleep 0.5
done
if owned_pid >/dev/null; then
  echo "연구 Chrome은 떠 있으나 30초 안에 CDP가 열리지 않았다 (PID $(owned_pid)). --status 로 다시 확인한다" >&2
else
  echo "API research Chrome did not become ready" >&2
fi
exit 1
