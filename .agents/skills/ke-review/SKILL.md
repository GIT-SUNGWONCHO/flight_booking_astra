---
name: ke-review
description: "코드·문서 변경을 코덱스(codex exec review)로 교차 검토한다. 읽기 전용으로 실행하고 결과를 근거와 함께 남긴다. 주문·브라우저 실행은 하지 않는다."
---

# 코덱스 교차 검토 지침

## 1. 목적과 적용 시점

이 지침은 한 도구의 판단을 다른 도구로 교차 확인하는 절차다. 작업 권한은 [AGENTS](../../../AGENTS.md), 검증 기준은 [ke-verify](../ke-verify/SKILL.md)와 [README의 검증 항목](../../../README.md#validation)이 원본이다. 코덱스 검토는 그 기준을 대신하지 않고 추가 근거만 만든다.

적용 시점은 다음 셋이다.

- 실행 코드(`dev/`·`ke_award/`·`userscript/`)나 `api_booking/` 변경을 커밋·인계하기 전
- 09시 결과 해석·설계 문서를 고치기 전, 특히 수치 해석을 철회하거나 새로 주장할 때
- 사용자가 검토를 요청할 때

09시 준비·대기 중에는 실행하지 않는다. 검토는 읽기 전용이지만 같은 PC의 자원을 쓰므로 실전 시간대를 피한다.

## 2. 실행

```bash
# Mac: 미커밋·미추적 변경 전체
./.venv/bin/python dev/ke_review.py

# 분기 전체 / 특정 커밋 / 초점 지정
./.venv/bin/python dev/ke_review.py --base codex/hybrid-booking
./.venv/bin/python dev/ke_review.py --commit dc2b07f
./.venv/bin/python dev/ke_review.py --focus '마감 판정의 타임존 처리만 본다'
```

Windows에서는 실행기를 `.\.venv\Scripts\python.exe`로 바꾼다. `codex` 실행 파일은 PATH → `KE_CODEX` 환경변수 → OS별 설치 경로 순으로 찾는다. **Windows 후보 경로는 아직 검증하지 않았다.** 찾지 못하면 `KE_CODEX`로 직접 지정한다.

`--dry-run`은 명령과 대상 파일만 출력하고 모델을 호출하지 않는다. 범위가 맞는지 먼저 확인할 때 쓴다.

## 3. 강제되는 제약

`dev/ke_review.py`는 코덱스를 `sandbox_mode="read-only"`·`approval_policy="never"`로 실행한다. 검토자는 파일을 고치거나 브라우저·시험을 실행할 수 없다. 이 설정을 우회하는 인자를 추가하지 않는다.

검토 지시문은 예매 9232·계측 9233·연구 9242 프로필 접속과 실사이트 주문을 금지하고, 이웃 `flight_booking`(Claude 원본)을 읽지 않게 한다. 검토 결과가 파일 수정을 제안해도 **적용은 별도 작업**이며 AGENTS의 변경 규칙을 다시 따른다.

## 4. 결과 읽기

결과는 `dev-shots/reviews/<검토 ID>/`에 남는다. 검토 ID는 운영 실행 ID와 같은 `YYYYMMDD-HHMMSS-해시` 형식이며 서로 다른 기록이다.

| 파일 | 내용 |
|---|---|
| `review.md` | 검토 보고서. 이것만 근거로 인용한다 |
| `ke_review.json` | 범위·대상 파일·브랜치·HEAD·runtimeHash·종료 코드·이벤트 오류 |
| `prompt.md` | 그 실행에 실제로 넣은 검토 지시문 |
| `scope.diff` | 실제로 검토된 변경 본문. `scopeDigest`의 원본 |
| `events.jsonl`·`stderr.log` | 원본 이벤트와 오류. 실패 원인 추적용 |

`ok`가 `false`이거나 `review.md`가 비었으면 검토가 성립하지 않은 것이다. 부분 출력을 통과로 보고하지 않는다.

`runtimeHash`는 운영 실행 코드만 덮으므로 `api_booking/`·문서 변경을 식별하지 못한다. **어느 변경본을 검토했는지는 `scopeDigest`로 대조한다.** 없는 참조를 `--base`·`--commit`에 주면 모델을 호출하기 전에 종료 코드 1로 중단하며, 이때는 기록 폴더도 만들지 않는다.

검토자는 저장된 `scope.diff`가 아니라 작업 트리를 다시 읽는다. 그래서 실행 전후의 지문을 대조해 `scopeStable`에 남기고, **검토 중 파일이 바뀌면 `ok=false`로 무효 처리한다.** 검토를 돌리는 동안 대상 파일을 편집하지 않는다.

## 5. 지적을 다룰 때

1. 지적마다 **해당 코드·로그를 직접 확인**한다. 코덱스가 읽지 못한 실행 기록(`dev-shots/runs/`)을 근거로 반박할 수 있다.
2. 실행 환경 차이를 구분한다. Mac에서만 재현되는 문제와 Windows 기준 결함을 나눈다.
3. 채택·기각·보류로 나누고, 기각은 이유를 적는다. 보류는 [NOW의 과제](../../../NOW.md)에 번호와 다음 확인 조건을 남긴다.
4. 코드를 고쳤으면 [ke-verify](../ke-verify/SKILL.md)와 [README의 변경별 시험](../../../README.md#handoff)을 그대로 적용한다. 검토 통과는 시험 통과가 아니다.

## 6. 보고 형식

**검토 ID·범위 → 지적 수와 채택/기각 → 실제로 고친 것 → 남은 확인** 순서로 쓴다. 검토자가 읽지 않은 범위를 통과로 말하지 않는다. 예: ‘`20260912-150203-3cf73083` 미커밋 13개 검토 — 지적 4건 중 2건 채택·수정, 1건 기각(로그로 반증), 1건 보류. Windows 경로는 여전히 미검증.’
