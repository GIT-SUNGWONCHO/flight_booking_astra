#!/usr/bin/env bash
# astra_browsers.ps1 의 macOS 대응. 이 worktree 의 9232/9233 프로필만 다룬다.
# 윈도우판의 Win32_Process 소유권 검사는 여기서 lsof 로 대체하며,
# "포트를 쓰는 프로세스가 이 프로필의 Chrome 인지"까지는 검증하지 않는다.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
RESTART=0; PORT=0
while [ $# -gt 0 ]; do
  case "$1" in
    -Restart|--restart) RESTART=1; shift;;
    -Port|--port) PORT="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
launch_one() {
  local port="$1" profile_name="$2"
  local profile="$ROOT/$profile_name"
  case "$port" in 9232|9233) ;; *) echo "Only Astra ports 9232 and 9233 are allowed" >&2; exit 1;; esac
  if [ "$RESTART" = "1" ] && lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    "$ROOT/.venv/bin/python" "$ROOT/dev/close_astra_browser.py" --port "$port" || true
    for _ in $(seq 1 60); do
      lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 || break
      sleep 0.5
    done
    lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1 && { echo "Port $port did not close" >&2; exit 1; }
  fi
  if ! lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
    "$CHROME" --remote-debugging-port="$port" --user-data-dir="$profile" \
      --no-first-run --no-default-browser-check --window-size=1600,1000 \
      --disable-popup-blocking --disable-background-timer-throttling \
      --disable-backgrounding-occluded-windows --disable-renderer-backgrounding \
      "https://www.koreanair.com/kr/ko" >/dev/null 2>&1 &
  fi
  local ready=0
  for _ in $(seq 1 20); do
    if curl -fsS --max-time 2 "http://127.0.0.1:$port/json/version" 2>/dev/null | grep -q webSocketDebuggerUrl; then ready=1; break; fi
    sleep 0.5
  done
  [ "$ready" = "1" ] || { echo "Chrome endpoint $port did not become ready" >&2; exit 1; }
  "$ROOT/.venv/bin/python" "$ROOT/dev/browser_identity.py" --port "$port" || echo "Chrome is ready, but its cosmetic profile label needs retry on $port" >&2
  nohup "$ROOT/.venv/bin/python" "$ROOT/dev/browser_identity.py" --port "$port" --keep >/dev/null 2>&1 &
  echo "Astra Chrome port $port ready"
}
[ "$PORT" = "0" ] || [ "$PORT" = "9232" ] && launch_one 9232 .debug-profile || true
[ "$PORT" = "0" ] || [ "$PORT" = "9233" ] && launch_one 9233 .debug-profile2 || true
