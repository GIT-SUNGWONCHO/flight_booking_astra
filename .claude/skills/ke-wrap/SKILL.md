---
name: ke-wrap
description: "작업 종료·09시 결과 확인 후 문서 역할에 맞춰 현재 상태와 근거를 인계한다. 중복 이력과 운영 지시를 만들지 않는다."
---

# 문서 갱신·인계 지침

## 1. 문서 원본

읽기 순서는 AGENTS → NOW → README → 명세 → FACTS다. 한 정보를 여러 문서의 원본으로 관리하지 않는다.

| 변경된 정보 | 갱신 위치 |
|---|---|
| AI 작업 범위·권한·핵심 제약 | [AGENTS](../../../AGENTS.md) |
| 다음 목표·최근 결과·남은 상태·다음 작업 | [NOW](../../../NOW.md), 30줄 이내 |
| 구성 요소 동작 | [docs/spec/](../../../docs/spec/) |
| 실행 절차·장애 대응 | [docs/operations.md](../../../docs/operations.md) |
| 시험 단계·명령·기록 규칙 | [docs/testing.md](../../../docs/testing.md) |
| 실사이트 실행 결과(날짜별) | [docs/results/](../../../docs/results/) |
| 관측 사실·해석 한계 | [FACTS](../../../FACTS.md), 주제별 표 |
| 확정 일정 | [공통 설정](../../../config/test_calendar.json) 변경 후 `dev/test_calendar.py --render`로 docs/calendar.md 생성 |
| 폴더·문서 지도 | [README](../../../README.md), 폴더별 README |

`docs/archive/`는 과거 증거이며 현재 실행 지시가 아니다.

## 2. 실행 결과를 남길 때

`dev-shots/api-day/`의 체인 보고서·콘솔 로그, `dev-shots/state*/`의 `fire-timing`·주문 증거·의도, `dev-shots/runs/`의
`calendar_observer.json`을 읽는다. 결과는 [docs/testing.md §4](../../../docs/testing.md) 틀로 `docs/results/<날짜>.md`에 쓰고,
새 사실은 FACTS 해당 표에 한 줄로 올린다. 과거 로그인·창 상태를 현재 상태로 보고하지 않는다.

## 3. 갱신 원칙

- 문서는 한글, 식별자·명령·파일명은 원문. 계정·토큰·결제 식별자 원문은 기록하지 않는다.
- 새 문서를 늘리기보다 위 원본에 반영한다. 긴 과거 자료는 `docs/archive/<날짜>/`로 옮긴다.
- 명세·현재 구현·검증 범위를 구분한다. 하지 않은 시험을 했다고 쓰지 않는다.

## 4. 완료 전 확인과 보고

링크·명령·캘린더 생성본 일치·스킬 두 사본 일치(`test/test_skills_sync.py`)·문서 간 충돌을 검사한다.
사용자에게 **어느 문서를 어떤 역할로 바꿨는지, 무엇을 확인했고 무엇이 미완료인지** 보고한다.
