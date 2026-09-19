"""스킬 원본은 하나다: .claude/skills(Claude)와 .agents/skills(Codex)가 같은 내용이어야 한다.

한쪽만 고치면 이 시험이 실패한다. 고친 쪽을 다른 쪽에 그대로 복사한다.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
claude = {p.relative_to(ROOT / '.claude' / 'skills'): p.read_bytes()
          for p in (ROOT / '.claude' / 'skills').rglob('*') if p.is_file()}
agents = {p.relative_to(ROOT / '.agents' / 'skills'): p.read_bytes()
          for p in (ROOT / '.agents' / 'skills').rglob('*') if p.is_file()}
assert claude.keys() == agents.keys(), f'파일 목록 불일치: {sorted(set(claude) ^ set(agents))}'
diff = [str(k) for k in claude if claude[k] != agents[k]]
assert not diff, f'내용 불일치: {diff}'
print(f'스킬 {len(claude)}개 파일 일치')
