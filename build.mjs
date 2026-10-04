/* util + steps + recorder + editor + hud  ->  userscript/ke-award-macro.user.js
 *
 * autoconfirm.js(라벨 추측 클릭)는 유저스크립트에서 뺐다. 녹화 재생이 순서를
 * 정확히 알고 있는데 옆에서 추측으로 누르면 방해만 된다 - 실측에서 팝업을 먼저
 * 띄워 재생을 10초 멈춰세웠고, 재생이 끝난 뒤 결제 직후 팝업까지 눌러버렸다.
 * Playwright 러너(runner.py)는 녹화가 없는 경로라 계속 쓴다.
 * 엔진을 한 곳에서만 관리하기 위한 스티처.  실행:  node build.mjs
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs';
import { dirname } from 'node:path';
import { execSync } from 'node:child_process';

/* 버전은 손으로 올리면 반드시 까먹는다. 그러면 붙여넣은 스크립트가 최신인지
 * 확인할 방법이 없어진다(실제로 이전 빌드로 계속 테스트한 적이 있다).
 * 커밋 수를 마이너로, 짧은 해시를 빌드 표시로 써서 빌드할 때마다 자동으로 정한다. */
function gitInfo() {
  const run = (c) => execSync(c, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
  try {
    const count = run('git rev-list --count HEAD');
    const hash = run('git rev-parse --short HEAD');
    const dirty = run('git status --porcelain') ? '-dirty' : '';
    return { version: `1.${count}.0${dirty}`, hash: hash + dirty };
  } catch (e) {
    return { version: '1.0.0-nogit', hash: 'nogit' };
  }
}
const GIT = gitInfo();

/* 시험이 단계를 바꿔 빌드해 볼 때 쓴다(test_precedence). 주지 않으면 평소 경로다.
 * 예전에는 시험이 steps.json 과 빌드 결과를 잠깐 덮어썼다가 되돌렸는데, 그러면 시험을 돌릴
 * 때마다 Git 이 추적하는 파일이 바뀌고, 그 사이에 로그인 준비가 시험용 스크립트를 주입할 수 있었다. */
const STEPS_PATH = process.env.KE_BUILD_STEPS || 'ke_award/steps.json';
const OUT_PATH = process.env.KE_BUILD_OUT || 'userscript/ke-award-macro.user.js';

/* @grant 를 none 으로 두면 Tampermonkey 가 페이지 컨텍스트에 <script> 로 밀어넣는데,
 * CSP 가 강한 사이트(항공사 등)에서는 그게 차단되어 스크립트가 조용히 죽는다.
 * unsafeWindow 를 grant 하면 샌드박스에서 실행되어 CSP 를 타지 않는다.
 * (그래서 각 모듈이 KE_AUTO/KE_HUD 를 unsafeWindow 에도 노출한다 - 콘솔 접근용) */
const HEADER = `// ==UserScript==
// @name         대한항공 마일리지 예매 보조 (KE Award Macro)
// @namespace    local.ke.award
// @version      ${GIT.version}
// @description  예매 단계 녹화/재생 + 오픈시각 정시 발사 + 안내사항 모달 즉시 통과
// @author       local
// @match        *://*.koreanair.com/*
// @match        *://koreanair.com/*
// @run-at       document-start
// @grant        unsafeWindow
// @noframes     false
// ==/UserScript==
/* 주의: 본인 계정으로 본인 여정을 예매하는 용도로만 사용하세요.
 *       재조회 간격은 800ms 미만으로 낮추지 마세요 (봇 탐지 / 서버 부담).  */
`;

/* 두 모듈은 한 파일로 합쳐지므로, 앞 모듈에서 예외가 top-level 로 새면
 * 뒤 모듈이 아예 실행되지 않는다. 각각 격리한다. */
function isolate(name, src) {
  return `
// ---------- ${name} ----------
try {
${src}
} catch (e) {
  console.error('[KE] ${name} 로드 실패:', e);
}
`;
}

/* 녹화 단계를 스크립트 안에 굽는다.
 * localStorage 에만 두면 PC 를 옮길 때 사라지고 git 으로 관리도 안 된다.
 * steps.json 을 커밋해두면 어느 PC 에서 빌드해도 같은 단계를 들고 간다. */
function bakedSteps() {
  const path = STEPS_PATH;
  let steps = [];
  let at = '';
  if (existsSync(path)) {
    const raw = JSON.parse(readFileSync(path, 'utf8'));
    steps = Array.isArray(raw) ? raw : raw.steps || [];
    at = (raw && raw.recordedAt) || '';
  }
  const body = `  var S = ${JSON.stringify(steps)};
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  try { W.KE_STEPS_BAKED = S; } catch (e) {}
  if (W !== window) { try { window.KE_STEPS_BAKED = S; } catch (e) {} }
  if (S.length) console.log('[KE] 내장 단계 ' + S.length + '개${at ? ' (' + at + ')' : ''}');`;
  return { js: `(function () {\n${body}\n})();`, count: steps.length };
}

// util 이 가장 먼저 로드되어야 recorder/hud 가 KE_UTIL 을 쓸 수 있고,
// steps 는 recorder 보다 먼저 있어야 기본값으로 집어갈 수 있다
const baked = bakedSteps();
const out = [
  HEADER,
  isolate('util', readFileSync('ke_award/util.js', 'utf8')),
  isolate('build', `(function () {
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  var B = { version: '${GIT.version}', hash: '${GIT.hash}' };
  try { W.KE_BUILD = B; } catch (e) {}
  if (W !== window) { try { window.KE_BUILD = B; } catch (e) {} }
})();`),
  /* probe 는 가능한 한 일찍 - 페이지가 좌석 조회를 쏘기 전에 fetch/XHR 을 감싸야 한다 */
  isolate('probe', readFileSync('ke_award/probe.js', 'utf8')),
  isolate('steps', baked.js),
  isolate('recorder', readFileSync('ke_award/recorder.js', 'utf8')),
  isolate('editor', readFileSync('ke_award/editor.js', 'utf8')),
  isolate('hud', readFileSync('ke_award/hud.js', 'utf8')),
].join('\n');

mkdirSync(dirname(OUT_PATH), { recursive: true });
writeFileSync(OUT_PATH, out, 'utf8');
console.log(`built ${OUT_PATH}  (${out.split('\n').length} lines, ${out.length} bytes)`);
