"""CLI:  python -m ke_award <command>

  clock            NTP 오프셋과 현재 KST 를 확인 (평소 점검용)
  login            브라우저를 띄워 로그인 -> 프로필에 세션 저장
  probe            수동으로 한 번 예매 흐름을 밟으며 URL/버튼/모달 라벨을 수집
  run              config.yaml 대로 정시 발사 + 재조회
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import clock


def cmd_clock(_args) -> int:
    off = clock.sync()
    print(f"  로컬  : {clock.now_kst(0.0)}")
    print(f"  보정  : {clock.now_kst(off)}")
    print(f"  오프셋: {off * 1000:+.1f}ms")
    if abs(off) > 1.0:
        print("  ! 로컬 시계가 1초 이상 틀어져 있습니다. 관리자 PowerShell 에서 'w32tm /resync' 권장.")
    return 0


def _runner(args):
    from .runner import Config, Runner
    cfg = Config.load(args.config) if Path(args.config).exists() else Config()
    return Runner(cfg), cfg


def cmd_login(args) -> int:
    r, cfg = _runner(args)
    r.launch()
    try:
        r.page.goto(cfg.login_url)
        print("  브라우저에서 로그인하세요 (2단계 인증/보안문자 포함).")
        input("  완료되면 Enter > ")
        print("  로그인 상태: " + ("확인됨" if r.is_logged_in() else
                                 "확인 실패 - config 의 logged_in_text 를 조정하세요"))
        print(f"  세션은 {Path(cfg.profile_dir).resolve()} 에 남습니다.")
    finally:
        r.close()
    return 0


def cmd_probe(args) -> int:
    """수동 리허설. 자동 클릭은 끈 채로 흐름만 관찰해 config 재료를 모은다."""
    r, cfg = _runner(args)
    r.launch()
    try:
        r.ctx.add_init_script("try{window.KE_AUTO.enabled=false;}catch(e){}")
        r.page.goto(cfg.login_url)
        print("""
  [리허설 모드] 자동 클릭은 꺼져 있습니다. 평소처럼 손으로 진행하세요.
    1) 로그인 -> 보너스 항공권 조회 -> 날짜/구간/프레스티지 선택
    2) 조회 결과가 뜨면 그 화면에서 Enter  (URL 을 기록합니다)
    3) 이어서 안내사항 모달을 끝까지 손으로 통과한 뒤 다시 Enter
       (통과한 버튼 라벨을 뽑아 config 에 넣을 수 있게 정리해줍니다)
""")
        input("  조회 결과 화면에서 Enter > ")
        url = r.page.url
        print(f"\n  search_url: {url}\n")

        # 화면의 버튼 라벨을 훑어 config 후보를 뽑는다
        labels = r.page.evaluate(r"""() => {
          const out = [];
          document.querySelectorAll('button,a[role="button"],[role="button"],input[type="submit"]')
            .forEach(el => {
              const r = el.getBoundingClientRect();
              const t = (el.innerText || el.value || '').replace(/\s+/g,' ').trim();
              if (t && r.width > 2 && r.height > 2) out.push(t.slice(0, 30));
            });
          return [...new Set(out)];
        }""")
        print("  화면의 버튼 라벨:")
        for t in labels[:40]:
            print(f"    - {t}")

        input("\n  안내사항 모달을 끝까지 통과한 뒤 Enter > ")
        print(f"  최종 URL: {r.page.url}")
        txt = r.page.inner_text("body")[:400].replace("\n", " ")
        print(f"  최종 화면 텍스트(앞부분): {txt}")

        out = Path("probe_result.txt")
        out.write_text(
            f"search_url: {url}\nfinal_url: {r.page.url}\n\nbuttons:\n"
            + "\n".join(f"  - {t}" for t in labels)
            + f"\n\nfinal_text:\n{txt}\n",
            encoding="utf-8",
        )
        print(f"\n  {out} 에 저장했습니다. 이 내용으로 config.yaml 을 채우세요.")
    finally:
        input("  브라우저를 닫으려면 Enter > ")
        r.close()
    return 0


def cmd_run(args) -> int:
    if not Path(args.config).exists():
        print(f"  {args.config} 가 없습니다. ke_award/config.example.yaml 을 복사해 채우세요.")
        return 1
    r, cfg = _runner(args)
    r.launch()
    try:
        out = r.run()
        print(f"\n  결과: {out.value}")
        input("  브라우저를 닫으려면 Enter > ")
        return 0 if out.name in ("SUCCESS", "CLICKED") else 2
    finally:
        r.close()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="ke_award", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-c", "--config", default="config.yaml")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name, fn in (("clock", cmd_clock), ("login", cmd_login),
                     ("probe", cmd_probe), ("run", cmd_run)):
        sub.add_parser(name).set_defaults(fn=fn)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
