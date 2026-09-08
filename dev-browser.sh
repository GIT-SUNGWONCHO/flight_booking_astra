#!/usr/bin/env bash
# 개발용 Chrome 을 원격 디버깅 포트와 함께 띄운다.
# 이 창에서 한 번만 로그인해두면, 이후 테스트 스크립트가 CDP 로 붙어서
# 스크립트 주입 / 재생 / DOM 확인을 전부 자동으로 한다.
#
#   ./dev-browser.sh        (한 번 실행해두고 그대로 두세요)
#
# 주의: 평소 쓰는 Chrome 프로필이 아니라 이 프로젝트 전용 프로필(.debug-profile)을 쓴다.
#       최신 Chrome 은 기본 프로필에 원격 디버깅을 허용하지 않기 때문.
cd "$(dirname "$0")"
exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9232 \
  --user-data-dir="$PWD/.debug-profile" \
  --no-first-run \
  --no-default-browser-check \
  "https://www.koreanair.com/kr/ko"
