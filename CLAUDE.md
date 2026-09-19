# AI 도구 진입 안내

이 폴더는 Astra 작업 영역이다. 도구 이름과 관계없이 **[AGENTS.md](AGENTS.md)**가 작업 규칙의 원본이다.
이 파일에 별도 권한·실행 규칙을 만들지 않는다.

읽는 순서: [AGENTS](AGENTS.md) → [NOW](NOW.md) → [README](README.md)(문서 지도) → 해당 [명세](docs/spec/)·[운영](docs/operations.md) → [FACTS](FACTS.md).

스킬은 `.claude/skills/`(Claude)와 `.agents/skills/`(Codex)에 같은 내용으로 둔다. 한쪽을 고치면 다른 쪽에 그대로 복사하고
`test/test_skills_sync.py`로 확인한다.
