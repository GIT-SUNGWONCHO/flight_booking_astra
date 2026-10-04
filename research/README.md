# research/ — 조사 도구

**예매에 쓰지 않는다.** "왜 이렇게 만들었나"를 확인할 때 썼던 도구들이다.
결과는 [FACTS](../FACTS.md)와 [docs/results/](../docs/results/)·[docs/review/](../docs/review/)에 정리돼 있다.
같은 조사를 다시 하거나, 사이트가 바뀌어 예매가 안 될 때 원인을 찾는 데 쓴다.

| 파일 | 무엇을 알아냈나 / 언제 쓰나 | 실사이트 요청 |
|---|---|---|
| `flow_trace.py` | 사람이 직접 예매할 때 브라우저가 부르는 API 순서를 기록한다. **주문 요청은 차단한다.** 사이트가 바뀐 것 같을 때 맨 먼저 쓴다 | 사람이 조작한 만큼 |
| `seat_watch.py` | 이미 열린 날짜의 좌석 수 변화를 일정 간격으로 본다 | 조회만 |
| `request_shape_probe.py` | 조회 요청 본문을 줄여도 되는지 본다(결론: 줄여도 빨라지지 않는다) | 조회만 |
| `award_id_probe.py` | 조회를 건너뛰고 운임부터 보낼 수 있는지 본다(결론: 안 된다) | 조회·운임, 주문 없음 |
| `order_path_probe.py` | 운임을 건너뛰고 주문할 수 있는지 본다(결론: 안 된다) | 문서의 절차를 따른다 |
| `collect.py` · `contracts.py` · `analyze.py` | 실행 중 요청·응답의 **형태**(비밀값 제외)를 모으고 요약한다 | 붙어서 관찰만 |
| `deadline.py` | 초기의 마감 시각 추정. **가정이 틀렸다** — 판정에 쓰지 않는다. 기록으로만 둔다 | 없음 |
| `app_trace.py` · `ct_hosts.py` | 휴대폰 앱이 다른 입구를 쓰는지 조사(결론: 웹과 같은 경로) | 앱 트래픽 관찰 / 공개 인증서 목록만 |
| `open_lab.ps1` · `open_lab.sh` | 조사용 Chrome(9242)만 따로 연다 | - |
| `_research_paths.py` | 이 폴더의 도구가 [`api_booking/`](../api_booking/README.md)·[`dev/`](../dev/README.md) 모듈을 찾게 한다 |  |
| `test/` | 이 폴더 도구의 시험. `python run_tests.py research` |  |

각 파일 맨 위 설명에 사용법과 안전 조건이 있다. 실행 전에 그것부터 읽는다.
