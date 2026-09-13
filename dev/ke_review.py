"""코덱스(codex exec review) 검토를 읽기 전용으로 실행하고 근거를 남긴다.

주문·브라우저 실행을 하지 않는다. 샌드박스는 read-only 고정이며 승인 요청은 하지 않는다.
결과는 dev-shots/reviews/<검토 ID>/ 에 저장한다.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import KST, atomic_json, runtime_hash  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
REVIEWS = ROOT / 'dev-shots' / 'reviews'

# PATH 에 codex 가 없을 때만 쓰는 설치 위치 후보. Windows 경로는 미검증이다.
CANDIDATES = {
    'Darwin': ['/Applications/ChatGPT.app/Contents/Resources/codex'],
    'Windows': [
        r'C:\Users\{user}\AppData\Local\Programs\ChatGPT\resources\codex.exe',
        r'C:\Program Files\ChatGPT\resources\codex.exe',
    ],
    'Linux': ['/usr/local/bin/codex'],
}

INSTRUCTIONS = """이 저장소는 대한항공 마일리지 좌석의 예매·계측 도구다. 아래 범위로만 검토한다.

## 읽을 원본
AGENTS.md(권한·제약) → NOW.md(현재 상태·과제) → README.md(동작·검증·변경 기준) → FACTS.md(입증된 사실).
api_booking/ 변경이면 api_booking/README.md 도 읽는다. docs/archive/ 는 과거 증거이며 현재 지시가 아니다.

## 절대 하지 않을 것
- 파일을 수정하거나 명령으로 상태를 바꾸지 않는다. 읽기와 분석만 한다.
- 브라우저·Chrome·playwright 를 실행하지 않는다. 9232/9233/9242 포트에 접속하지 않는다.
- 실사이트에 접속하거나 주문·결제를 만들지 않는다. 시험을 실행하지 않는다.
- 옆 폴더 flight_booking(Claude 원본)을 읽거나 언급하지 않는다.

## 볼 것
1. 정확성 결함: 시각·타임존(KST)·마감 판정·경계 조건·예외 처리·경쟁 상태. 근거를 코드 줄로 제시한다.
2. 운영 제약 위반: 예매 실패와 계측 실패의 분리, 사용자만 하는 최종 승인·실전 대기 시작, Chrome 을 이름으로 일괄 종료하지 않기, 운영 프로필·포트 재사용 금지.
3. 실행 환경: 앞으로의 기준은 Windows(.\\.venv\\Scripts\\python.exe)다. Mac 전용 경로·명령·프로세스 제어가 Windows 에서 깨지는 지점을 지적한다.
4. 시험 범위 과대 해석: 주석·문서·보고 문구가 T1 로컬 시험을 실사이트(T2)나 실제 09시(T3) 성공처럼 말하는 곳.
5. 문서와 코드의 불일치: NOW·README·FACTS 가 서술한 동작과 실제 코드의 차이.

## 보고 형식
심각도 높은 순으로, 항목마다 **파일:줄 → 무엇이 왜 틀렸는가 → 재현/영향 → 제안**을 쓴다.
확인하지 못한 것은 추측하지 말고 '미확인'으로 남긴다. 발견이 없으면 없다고 쓴다. 한국어로 쓴다.
"""


def find_codex(explicit=None):
    if explicit:
        path = Path(explicit).expanduser()
        if not path.exists():
            raise SystemExit(f'지정한 codex 실행 파일 없음: {path}')
        return str(path)
    env = os.environ.get('KE_CODEX')
    if env:
        return find_codex(env)
    found = shutil.which('codex')
    if found:
        return found
    user = os.environ.get('USERNAME') or os.environ.get('USER') or ''
    for raw in CANDIDATES.get(platform.system(), []):
        path = Path(raw.replace('{user}', user))
        if path.exists():
            return str(path)
    raise SystemExit('codex 실행 파일을 찾지 못했다. PATH 에 넣거나 KE_CODEX 로 경로를 지정한다.')


def run_text(cmd):
    """출력 디코딩을 UTF-8 로 고정한다.

    한국어 Windows 에서 text=True 는 기본 코드 페이지(cp949)로 해석해
    UTF-8 한글 diff 를 깨뜨리거나 UnicodeDecodeError 를 낸다.
    """
    return subprocess.run(cmd, cwd=ROOT, capture_output=True,
                          text=True, encoding='utf-8', errors='replace')


def git(*args, keep_indent=False):
    """실패를 빈 출력과 구분하지 않는다. 범위 조회에는 git_strict 를 쓴다."""
    done = run_text(['git', *args])
    if done.returncode != 0:
        return ''
    # porcelain 의 앞 두 칸은 상태 문자다. strip 하면 첫 줄의 경로가 잘린다.
    return done.stdout.rstrip('\n') if keep_indent else done.stdout.strip()


def git_strict(*args):
    """범위 조회용. git 실패를 '변경 없음'으로 삼키지 않고 중단한다."""
    done = run_text(['git', '--no-optional-locks', *args])
    if done.returncode != 0:
        raise SystemExit(f'검토 범위를 확인하지 못했다: git {" ".join(args)}\n'
                         f'{done.stderr.strip()}')
    return done.stdout


def verify_ref(ref):
    done = run_text(['git', 'rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'])
    if done.returncode != 0:
        raise SystemExit(f'검토 범위를 확인하지 못했다: 참조 {ref} 를 찾을 수 없다.')
    return done.stdout.strip()


def scope_source(args, files):
    """실제로 검토되는 변경 본문. 이것으로 대상 지문을 만든다.

    검토자는 어느 모드에서도 저장된 diff 가 아니라 작업 트리를 읽는다.
    그래서 범위 diff 뿐 아니라 대상 파일의 현재 내용까지 지문에 넣는다.
    그래야 검토 중 편집을 scopeStable 로 잡아낸다.
    """
    if args.commit:
        text = git_strict('show', args.commit)
    elif args.base:
        text = git_strict('diff', f'{args.base}...HEAD')
    else:
        text = git_strict('diff', 'HEAD')
    for name in files:
        path = ROOT / name
        if not path.is_file():
            text += f'\n--- 작업 트리에 없음 {name} ---\n'
            continue
        try:
            body = path.read_text(encoding='utf-8')
        except (OSError, UnicodeDecodeError):
            body = f'<읽을 수 없음: {path.stat().st_size} bytes>'
        text += f'\n--- 작업 트리 {name} ---\n{body}'
    return text


def scope_text(args):
    """codex exec review 는 --uncommitted/--base/--commit/--title 을 검토 지시문과 함께 쓰지 못한다.

    지시문을 유지하는 쪽을 택하고 범위는 지시문 안에서 지정한다.
    범위 지정이 없으면 review 가 현재 작업 트리의 변경 diff 를 스스로 제공한다.
    """
    if args.commit:
        return (f'커밋 {args.commit}',
                f'`git --no-optional-locks show {args.commit}` 의 변경만 검토한다.')
    if args.base:
        return (f'{args.base} 기준 분기 변경',
                f'`git --no-optional-locks diff {args.base}...HEAD` 의 변경만 검토한다.')
    return ('미커밋·미추적 변경',
            'review 가 제공하는 현재 작업 트리의 변경 diff 를 검토한다.')


def changed_files(args):
    # -z: 따옴표·이스케이프 없이 NUL 로 구분한다. 한글·공백·개행 경로에 필요하다
    if args.commit:
        verify_ref(args.commit)
        out = git_strict('show', '--name-only', '--pretty=', '-z', args.commit)
        return [f for f in out.split('\0') if f]
    if args.base:
        verify_ref(args.base)
        out = git_strict('diff', '--name-only', '-z', f'{args.base}...HEAD')
        return [f for f in out.split('\0') if f]
    # -uall: 미추적 디렉터리를 묶지 않고 파일 단위로 받는다
    # -z: 공백·한글 경로를 따옴표로 감싸지 않는다. porcelain 기본형은 감싼다
    records = git_strict('status', '--porcelain', '-uall', '-z').split('\0')
    names, skip = [], False
    for record in records:
        if not record:
            continue
        if skip:  # 이름 변경의 원래 경로 필드
            skip = False
            continue
        if len(record) > 3:
            names.append(record[3:])
            skip = record[0] in 'RC' or record[1] in 'RC'
    return names


def parse_events(path):
    """JSONL 이벤트에서 오류만 모은다. 성공 판정은 종료 코드와 보고서로 한다."""
    errors = []
    for line in path.read_text(encoding='utf-8', errors='replace').splitlines():
        line = line.strip()
        if not line.startswith('{'):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        kind = str(event.get('type') or event.get('msg', {}).get('type') or '')
        if 'error' in kind.lower():
            errors.append(event)
    return errors


def main():
    p = argparse.ArgumentParser(description='코덱스 읽기 전용 검토 실행')
    p.add_argument('--base', help='이 브랜치 기준의 분기 변경을 검토')
    p.add_argument('--commit', help='해당 커밋의 변경을 검토')
    p.add_argument('--focus', help='추가 검토 지시 (문장)')
    p.add_argument('--title', help='검토 요약에 표시할 제목')
    p.add_argument('--model', help='모델 지정. 없으면 codex 기본값')
    p.add_argument('--timeout', type=int, default=900, help='초 단위 제한, 기본 900')
    p.add_argument('--codex', help='codex 실행 파일 경로')
    p.add_argument('--dry-run', action='store_true', help='명령만 출력하고 실행하지 않음')
    args = p.parse_args()

    codex = find_codex(args.codex)
    # 범위 확인이 실패하면 기록 폴더를 만들기 전에 중단한다.
    scope, scope_rule = scope_text(args)
    files = changed_files(args)
    source = scope_source(args, files)
    if not files and not source.strip():
        raise SystemExit(f'검토할 변경이 없다: {scope}')
    scope_digest = hashlib.sha256(source.encode('utf-8')).hexdigest()

    review_id = datetime.now(KST).strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8]
    out = REVIEWS / review_id
    prompt = INSTRUCTIONS + f'\n## 검토 범위\n{scope}. {scope_rule}\n'
    if files:
        prompt += '\n대상 파일:\n' + '\n'.join(f'- {f}' for f in files) + '\n'
    if args.title:
        prompt += f'\n이번 검토 제목: {args.title}\n'
    if args.focus:
        prompt += f'\n## 이번에 특히 볼 것\n{args.focus}\n'

    # `codex exec review` 는 --color 를 받지 않는다. 색은 -c 로만 끈다.
    cmd = [codex, 'exec', 'review', '--json',
           '-c', 'sandbox_mode="read-only"', '-c', 'approval_policy="never"',
           '-o', str(out / 'review.md')]
    if args.model:
        cmd += ['--model', args.model]
    cmd.append(prompt)

    meta = {
        'reviewId': review_id,
        'scope': scope,
        'title': args.title,
        'readOnly': True,
        'sandbox': 'read-only',
        'codex': codex,
        'codexVersion': run_text([codex, '--version']).stdout.strip(),
        # --model 을 주지 않으면 codex 기본값이다. JSONL 이벤트는 모델명을 남기지 않는다.
        'model': args.model or '<codex 기본값>',
        'platform': f'{platform.system()} {platform.release()}',
        'branch': git('rev-parse', '--abbrev-ref', 'HEAD'),
        'head': git('rev-parse', 'HEAD'),
        'changedFiles': files,
        # runtimeHash 는 운영 실행 코드만 덮는다. 검토 대상 자체는 scopeDigest 로 식별한다.
        'runtimeHash': runtime_hash(),
        'scopeDigest': scope_digest,
        'scopeBytes': len(source.encode('utf-8')),
        'command': cmd[:-1] + ['<검토 지시문: prompt.md>'],
        'startedAt': datetime.now(KST).isoformat(),
    }
    if args.dry_run:
        # 모델을 부르지 않는 점검이므로 기록 폴더를 만들지 않는다
        meta['dryRun'] = True
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        return 0

    out.mkdir(parents=True, exist_ok=True)
    (out / 'scope.diff').write_text(source, encoding='utf-8')
    (out / 'prompt.md').write_text(prompt, encoding='utf-8')

    with (out / 'events.jsonl').open('wb') as events, (out / 'stderr.log').open('wb') as errlog:
        try:
            done = subprocess.run(cmd, cwd=ROOT, stdin=subprocess.DEVNULL,
                                  stdout=events, stderr=errlog, timeout=args.timeout)
            code = done.returncode
            meta['timedOut'] = False
        except subprocess.TimeoutExpired:
            code = 124
            meta['timedOut'] = True

    # 모델은 저장한 scope.diff 가 아니라 작업 트리를 다시 읽는다.
    # 검토 중 대상이 바뀌면 보고서와 지문이 어긋나므로 끝나고 다시 대조한다.
    try:
        after = scope_source(args, changed_files(args))
        meta['scopeDigestAfter'] = hashlib.sha256(after.encode('utf-8')).hexdigest()
    except SystemExit as exc:
        meta['scopeDigestAfter'] = None
        meta['scopeRecheckError'] = str(exc)
    meta['scopeStable'] = meta['scopeDigestAfter'] == scope_digest

    meta['finishedAt'] = datetime.now(KST).isoformat()
    meta['exitCode'] = code
    meta['eventErrors'] = parse_events(out / 'events.jsonl')[:20]
    report = out / 'review.md'
    meta['reportBytes'] = report.stat().st_size if report.exists() else 0
    meta['ok'] = code == 0 and meta['reportBytes'] > 0 and meta['scopeStable']
    atomic_json(out / 'ke_review.json', meta)

    print(f'검토 ID: {review_id}')
    print(f'범위: {scope} / 파일 {len(files)}개')
    print(f'종료 코드: {code} / 보고서: {report if meta["reportBytes"] else "없음"}')
    if not meta['scopeStable']:
        print('검토 중 대상이 바뀌었다. 보고서와 scopeDigest 가 어긋나므로 무효로 본다.')
    if meta['eventErrors']:
        print(f'이벤트 오류 {len(meta["eventErrors"])}건 — ke_review.json 확인')
    if not meta['ok']:
        print('실패: 보고서를 근거로 쓰지 않는다. stderr.log 를 읽는다.')
    return 0 if meta['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
