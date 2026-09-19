# AI 도구 공통 진입 안내

## 1. 적용할 규칙

이 폴더는 Astra 작업 영역이다. AI 도구 이름과 관계없이 **[AGENTS.md](AGENTS.md)**를 작업 규칙의 원본으로 읽는다. 이 파일에 별도 권한·실행 규칙을 만들지 않는다.

## 2. 읽는 순서

1. [AGENTS](AGENTS.md): 작업 범위와 권한
2. [NOW](NOW.md): 현재 상태와 다음 과제
3. [README](README.md): 도구 명세와 운영 절차
4. [FACTS](FACTS.md): 확인된 근거와 한계
5. **[API 개발 원본 §3~§6](api_booking/README.md#process)**: 전체 프로세스·D1~D5 개발·실전 진입 조건. 첫 작업은 [§4 D1](api_booking/README.md#development), 붙여 넣을 프롬프트는 [§8](api_booking/README.md#claude-prompt).

새 계정·새 세션의 상태 확인·시작 문구는 [README의 인계 절차](README.md#new-session)를 따른다.

## 3. 이전 기록

[정리 전 CLAUDE.md](docs/archive/2026-09-09/before-CLAUDE.md)는 과거 자료다. 이웃 원본 폴더에 대한 권한은 AGENTS를 따르며, 이 안내가 작업 범위를 넓히지 않는다.
