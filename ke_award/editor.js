/* ============================================================
 * editor.js  --  녹화한 단계 편집 UI
 *
 * 녹화는 한 번에 깔끔하게 되지 않는다. 실제로 이런 일이 생긴다:
 *   - 스크롤을 괜히 두 번 눌러서 불필요한 단계가 끼었다  -> 삭제
 *   - 마일리지 적용을 안 누르고 넘어갔다               -> 그 자리에 끼워넣기
 *   - 날짜 버튼은 라벨이 매일 바뀐다                    -> 셀렉터만 쓰도록 전환
 * 다시 처음부터 녹화하지 않고 고칠 수 있어야 한다.
 * ============================================================ */
(function () {
  'use strict';
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  /* API 예매(api_booking)는 화면을 누르지 않는다. 로그인·달력 준비가 KE_UTIL 만 빌려 쓴다.
   * 그 창에 이 모듈이 실리면 쓰지도 않는 패널이 뜨고, 남아 있던 무장·재생 상태가 정각에 화면을
   * 누를 수 있다. dev/setup.py --api-mode 가 이 표시를 세운다. */
  if (W.KE_API_MODE || window.KE_API_MODE) return;
  if (W.KE_EDIT || window.KE_EDIT) return;

  var U = W.KE_UTIL || window.KE_UTIL;

  function REC() { return W.KE_REC || window.KE_REC; }

  // ---- 엘리먼트 피커 -------------------------------------------------------
  var picking = null;

  function pick(cb) {
    stopPick();
    picking = cb;
    document.body.style.cursor = 'crosshair';
  }
  function stopPick() {
    picking = null;
    try { document.body.style.cursor = ''; } catch (e) {}
  }

  document.addEventListener('click', function (ev) {
    if (!picking) return;
    if (ev.target.closest && ev.target.closest('#ke-editor, #ke-hud')) return;
    ev.preventDefault();
    ev.stopPropagation();
    var el = ev.target.closest(U.CLICKABLE) || ev.target;
    var cb = picking;
    stopPick();
    cb({
      sel: U.cssPath(el),
      text: U.label(el),
      tag: el.tagName.toLowerCase(),
      url: location.pathname + location.search,
      selectorOnly: false
    });
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && picking) { stopPick(); render(); }
  }, true);

  // ---- UI ------------------------------------------------------------------
  var box = null;

  function close() { if (box) { box.remove(); box = null; } stopPick(); }

  function open() {
    if (box) { render(); return; }
    box = document.createElement('div');
    box.id = 'ke-editor';
    box.innerHTML =
      '<style>' +
      '#ke-editor{position:fixed;inset:0;z-index:2147483646;background:rgba(0,0,0,.55);' +
      'display:flex;align-items:center;justify-content:center;font:12px/1.5 -apple-system,"Malgun Gothic",sans-serif}' +
      '#ke-editor .w{background:#fff;border-radius:8px;width:min(620px,94vw);max-height:86vh;' +
      'display:flex;flex-direction:column;padding:14px;color:#222}' +
      '#ke-editor h4{margin:0 0 8px;font-size:14px;color:#0b4da2}' +
      '#ke-editor .rows{overflow:auto;flex:1;border:1px solid #eee;border-radius:4px}' +
      '#ke-editor .r{display:flex;align-items:center;gap:6px;padding:5px 7px;border-bottom:1px solid #f0f0f0}' +
      '#ke-editor .r:nth-child(odd){background:#fafafa}' +
      '#ke-editor .n{width:22px;color:#888;text-align:right;flex:none}' +
      '#ke-editor .t{flex:1;min-width:0}' +
      '#ke-editor .t b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}' +
      '#ke-editor .t code{font-size:10px;color:#888;display:block;overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap}' +
      '#ke-editor button{cursor:pointer;border:1px solid #ccc;background:#fff;border-radius:3px;' +
      'padding:2px 6px;font:inherit;flex:none}' +
      '#ke-editor .ins{width:100%;border:1px dashed #bbb;color:#06c;background:#fff;margin:0;' +
      'border-radius:0;padding:1px;font-size:11px}' +
      '#ke-editor .foot{display:flex;gap:6px;margin-top:10px}' +
      '#ke-editor .foot button{padding:6px 10px}' +
      '#ke-editor .hint{font-size:11px;color:#666;margin-bottom:6px}' +
      '#ke-editor .so{font-size:10px;padding:1px 4px}' +
      '#ke-editor .so.on{background:#06c;color:#fff;border-color:#06c}' +
      '</style>' +
      '<div class="w"><h4>예매 단계 편집</h4>' +
      '<div class="hint">✕ 삭제 · ↑↓ 순서 · <b>＋</b> 그 자리에 빠진 단계 끼워넣기 · ' +
      '<b>고정</b> 은 라벨이 매일 바뀌는 버튼(날짜)에 사용</div>' +
      '<div class="rows" id="ke-ed-rows"></div>' +
      '<div class="foot">' +
      '<button id="ke-ed-add" style="flex:1">맨 끝에 추가</button>' +
      '<button id="ke-ed-json" style="flex:1">JSON</button>' +
      '<button id="ke-ed-close" style="flex:1">닫기</button>' +
      '</div></div>';
    document.documentElement.appendChild(box);
    box.querySelector('#ke-ed-close').onclick = close;
    box.querySelector('#ke-ed-add').onclick = function () { startInsert(REC().state.steps.length); };
    box.querySelector('#ke-ed-json').onclick = function () { close(); REC().showExport(); };
    box.addEventListener('click', function (e) { if (e.target === box) close(); });
    render();
  }

  function startInsert(at) {
    var r = REC();
    close();
    pick(function (step) {
      r.insertAt(at, step);
      open();
    });
    // 피커 안내는 HUD 토스트 대신 콘솔+커서로 (패널이 가려질 수 있어서)
    console.log('%c[KE_EDIT] 추가할 버튼을 클릭하세요 (ESC 취소)', 'color:#06c;font-weight:bold');
  }

  function render() {
    if (!box) return;
    var r = REC();
    if (!r) return;
    var steps = r.state.steps;
    var rows = box.querySelector('#ke-ed-rows');
    rows.innerHTML = '';

    function insertBar(at) {
      var b = document.createElement('button');
      b.className = 'ins';
      b.textContent = '＋ 여기에 추가';
      b.onclick = function () { startInsert(at); };
      rows.appendChild(b);
    }

    insertBar(0);
    steps.forEach(function (s, i) {
      var row = document.createElement('div');
      row.className = 'r';
      row.innerHTML =
        '<span class="n">' + (i + 1) + '</span>' +
        '<span class="t"><b></b><code></code></span>' +
        '<button class="so" title="셀렉터만 사용 (라벨 무시)">고정</button>' +
        '<button title="위로">↑</button><button title="아래로">↓</button>' +
        '<button title="삭제">✕</button>';
      row.querySelector('b').textContent = s.text || '(라벨 없음)';
      row.querySelector('code').textContent = s.sel || '';
      var so = row.querySelector('.so');
      if (s.selectorOnly) so.classList.add('on');
      so.onclick = function () { r.setStep(i, { selectorOnly: !s.selectorOnly }); render(); };
      var btns = row.querySelectorAll('button');
      btns[1].onclick = function () { r.moveStep(i, -1); render(); };
      btns[2].onclick = function () { r.moveStep(i, 1); render(); };
      btns[3].onclick = function () { r.removeStep(i); render(); };
      rows.appendChild(row);
      insertBar(i + 1);
    });

    if (!steps.length) {
      var empty = document.createElement('div');
      empty.style.cssText = 'padding:14px;color:#888;text-align:center';
      empty.textContent = '단계가 없습니다. 패널에서 ● 녹화 를 먼저 하세요.';
      rows.appendChild(empty);
    }
  }

  var API = { open: open, close: close, render: render, pick: pick };
  try { W.KE_EDIT = API; } catch (e) {}
  if (W !== window) { try { window.KE_EDIT = API; } catch (e) {} }
})();
