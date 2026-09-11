/* 공용 유틸. autoconfirm / hud / recorder 가 함께 쓴다. */
(function () {
  'use strict';
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  if (W.KE_UTIL || window.KE_UTIL) return;

  function visible(el) {
    if (!el || el.nodeType !== 1 || !el.isConnected) return false;
    if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
    var r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    var st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity || '1') < 0.05) return false;
    return true;
  }

  /* 화면에 "보인다"(visible)와 "지금 누를 수 있다"(hittable)는 다르다.
   * 모달이 떠 있으면 그 뒤의 버튼도 getBoundingClientRect/opacity 상으로는 멀쩡히
   * 보이지만, 실제로 클릭하면 모달이 먹는다. 그 좌표에서 실제로 잡히는 요소가
   * 자기 자신(또는 조상/자손)인지로 판정한다.
   * 화면 밖이라 판정이 불가능하면 기본은 통과시킨다 - fireClick 이 스크롤해서 누른다.
   *
   * strict=true 는 "단계를 건너뛸지" 판단할 때 쓴다. 거기서는 판정 보류를 통과로
   * 처리하면 안 된다: 모달 안쪽 아래에 숨어 있어 화면 밖인 [확인] 버튼을 "누를 수
   * 있다" 고 오판해서, 아직 끝나지 않은 스크롤 단계를 건너뛰어 버렸다(실측).
   * 건너뛰기는 확실한 근거가 있을 때만 해야 한다. */
  function hittable(el, strict) {
    var r;
    try { r = el.getBoundingClientRect(); } catch (e) { return !strict; }
    var x = r.left + r.width / 2, y = r.top + r.height / 2;
    var W2 = window.innerWidth || 0, H2 = window.innerHeight || 0;
    if (x < 0 || y < 0 || x > W2 || y > H2) return !strict;   // 화면 밖
    var top;
    try { top = document.elementFromPoint(x, y); } catch (e) { return !strict; }
    if (!top) return !strict;
    /* 우리 패널이 위를 덮고 있는 건 막힌 게 아니다. fireClick 은 좌표로 누르는 게
     * 아니라 요소에 이벤트를 직접 쏘므로 z-order 와 무관하게 닿는다. 이걸 빼지 않으면
     * 패널이 가린 버튼(화면 하단의 결제하기 등)을 "모달에 막혔다" 고 오판한다. */
    try { if (top.closest && top.closest('#ke-hud, #ke-editor, #ke-export')) return true; } catch (e) {}
    return top === el || el.contains(top) || top.contains(el);
  }

  /* 무엇이 이 요소를 덮고 있나 (가림 진단용).
   *
   * hittable 은 "막혔다/아니다" 만 알려줘서, 실전에서 '가림 1.1초' 가 찍혀도 정체를
   * 알 수 없었다. 줄이려면 무엇이 덮었는지를 봐야 한다 - 로딩 오버레이인지, 광고인지,
   * 스티키 헤더인지에 따라 대응이 다르다. 그래서 덮은 요소를 그대로 남긴다. */
  function coverInfo(el) {
    function d(n) {
      if (!n) return null;
      var q = {};
      try { q = n.getBoundingClientRect(); } catch (e) {}
      return {
        tag: n.tagName || '',
        id: n.id || '',
        cls: (n.className || '').toString().slice(0, 90),
        text: (n.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 50),
        rect: { x: Math.round(q.x || 0), y: Math.round(q.y || 0),
                w: Math.round(q.width || 0), h: Math.round(q.height || 0) }
      };
    }
    try {
      var r = el.getBoundingClientRect();
      var x = r.left + r.width / 2, y = r.top + r.height / 2;
      var top = document.elementFromPoint(x, y);
      if (!top) return { why: 'elementFromPoint 가 아무것도 못 잡음', target: d(el) };
      return { target: d(el), cover: d(top), coverParent: d(top.parentElement) };
    } catch (e) {
      return { why: String(e).slice(0, 60) };
    }
  }

  /* 이미 켜져(동의되어) 있는가.
   * 동의 버튼은 토글이라 이미 켜진 걸 다시 누르면 꺼진다. 녹화에 같은 동의가 두 번
   * 들어가 있으면 두 번째 클릭이 동의를 풀어버리고, 그 뒤 모달이 안 떠서 흐름이 통째로
   * 막힌다(실측). 판정이 안 되면 false 를 돌려 예전처럼 그냥 누른다 - 없던 기능이라
   * 오판으로 안 누르는 것보다 낫다. */
  function alreadyOn(el) {
    if (!el) return false;
    var a = el.getAttribute('aria-pressed') || el.getAttribute('aria-checked')
         || el.getAttribute('aria-selected');
    if (a === 'true') return true;
    if (a === 'false') return false;
    var n = el;
    for (var d = 0; d < 2 && n; d++) {
      var cl = n.classList;
      if (cl) {
        for (var i = 0; i < cl.length; i++) {
          if (/^(active|selected|checked|on|agreed|is-active|is-selected|is-checked)$/i.test(cl[i])) {
            return true;
          }
        }
      }
      n = n.parentElement;
    }
    var inp = el.querySelector && el.querySelector('input[type="checkbox"],input[type="radio"]');
    return !!(inp && inp.checked);
  }

  /* 저장 키를 탭마다 다르게 만든다.
   *
   * localStorage 는 같은 사이트의 모든 탭이 공유한다. 노선별로 탭을 띄워 동시에
   * 돌리면 네 탭이 같은 키를 서로 덮어써서, 발사 뒤 새로고침할 때 "남이 마지막에
   * 쓴 값" 을 읽는다. 실측(2026-08-29): 4노선 동시 실행에서 두 탭이 자기 설정
   * (단계 수·목표 날짜)을 잃고 멈췄다. 진행 위치(idx)도 공유라 서로의 진행을
   * 덮어쓸 수 있다.
   *
   * sessionStorage 는 탭마다 따로이고 새로고침은 견딘다 - 탭을 가리키는 표식으로 딱 맞다.
   * 그 표식이 없는 첫 방문에는 예전 공용 키에서 한 번 옮겨와, 쓰던 사람이 녹화를
   * 잃지 않게 한다. */
  function tabKey(base) {
    var id = null;
    try { id = sessionStorage.getItem('ke_award_tab'); } catch (e) {}
    /* sessionStorage 는 사이트가 sessionStorage.clear() 를 부르면 통째로 날아간다.
     * 그러면 같은 탭인데도 새 표식을 받아 이전 진행/녹화를 못 찾는다.
     * window.name 은 같은 탭 안에서 이동·새로고침을 견디고 clear() 에도 안 지워지므로
     * 예비 표식으로 함께 남긴다. */
    if (!id) {
      try {
        var m = /(?:^|;)ke_tab=([a-z0-9]+)/.exec(String(window.name || ''));
        if (m) id = m[1];
      } catch (e) {}
    }
    if (!id) id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    try { sessionStorage.setItem('ke_award_tab', id); } catch (e) {}
    try {
      var nm = String(window.name || '').replace(/(?:^|;)ke_tab=[a-z0-9]+/g, '');
      window.name = (nm ? nm + ';' : '') + 'ke_tab=' + id;
    } catch (e) {}
    var key = base + ':' + id;
    try {
      if (localStorage.getItem(key) === null) {
        var old = localStorage.getItem(base);      // 예전 공용 값에서 물려받는다
        if (old !== null) localStorage.setItem(key, old);
      }
    } catch (e) {}
    return key;
  }

  function label(el) {
    var t = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!t) t = el.value || el.getAttribute('aria-label') || el.title || '';
    return t.slice(0, 40);
  }

  /* 안정적인 CSS 경로. 자동 생성 클래스(ng-*, css-해시 등)와 상태 클래스는
   * 리렌더마다 바뀌므로 빼고, 구조(nth-of-type)에 기대는 쪽이 오래 간다. */
  var VOLATILE = /^(ng-|v-|is-|has-|css-|sc-|jsx-|active|selected|hover|focus|open|show|current|on)$/i;

  // 16자 이상 이어지는 16진수는 렌더/세션마다 새로 발급되는 컴포넌트 인스턴스 id일 확률이
  // 높다(예: ac0e9a2f7f9ead9dbd368853f47deb65CalendarFareBonusMain). 앵커로 쓰면 다음 로드 때
  // 100% 매칭 실패하므로 건너뛰고 더 위 조상의 안정적인 id 를 찾는다.
  function looksHashy(id) { return /[0-9a-f]{16,}/i.test(id); }
  function stableId(el) { return el.id && !/^\d/.test(el.id) && !looksHashy(el.id); }

  function cssPath(el) {
    if (!el || el.nodeType !== 1) return '';
    if (stableId(el)) {
      try { if (document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id); }
      catch (e) {}
    }
    var parts = [];
    while (el && el.nodeType === 1 && parts.length < 7) {
      if (stableId(el)) { parts.unshift('#' + CSS.escape(el.id)); break; }
      var s = el.tagName.toLowerCase();
      var cls = [];
      for (var i = 0; i < el.classList.length && cls.length < 2; i++) {
        if (!VOLATILE.test(el.classList[i])) cls.push(el.classList[i]);
      }
      if (cls.length) {
        try { s += '.' + cls.map(function (c) { return CSS.escape(c); }).join('.'); } catch (e) {}
      }
      var p = el.parentElement;
      if (p) {
        var same = [];
        for (var j = 0; j < p.children.length; j++) {
          if (p.children[j].tagName === el.tagName) same.push(p.children[j]);
        }
        if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
      }
      parts.unshift(s);
      el = p;
    }
    return parts.join(' > ');
  }

  // 대한항공 사이트는 <kds-button_1> 같은 커스텀 엘리먼트를 많이 쓰는데 button/role=button
  // 이 아닌 경우가 있어 원래 목록으로는 안 잡혔다. class 에 btn/button 이 들어간 것까지 넓힌다.
  var CLICKABLE = 'button, a, input[type="button"], input[type="submit"], input[type="checkbox"], ' +
                  '[role="button"], [role="tab"], [role="checkbox"], label, ' +
                  '.btn, .button, [class*="btn" i], [class*="button" i]';

  /** open shadow root 내부까지 훑는다 (일부 위젯이 웹컴포넌트). autoconfirm.js 와 동일 전략. */
  function candidates(root) {
    var out = [];
    try { out = Array.prototype.slice.call(root.querySelectorAll(CLICKABLE)); } catch (e) { return out; }
    try {
      root.querySelectorAll('*').forEach(function (n) {
        if (n.shadowRoot) out = out.concat(candidates(n.shadowRoot));
      });
    } catch (e) {}
    return out;
  }

  var PREFIX_LEN = 10;

  /* "검색"/"확인" 같은 흔한 라벨은 예매 화면 말고도 사이트 공통 헤더(전체 검색
   * 아이콘 등)에도 있을 수 있다. 그러면 엉뚱한 걸 눌러버리고도 클릭 자체는
   * 성공한 것처럼 보여서 화면이 안 넘어가는데 recorder 는 다음 단계로 넘어가버린다.
   * header/nav/banner 영역은 텍스트 폴백 후보에서 아예 제외한다. */
  function inChrome(el) {
    var n = el;
    for (var d = 0; d < 8 && n; d++) {
      var tag = n.tagName;
      if (tag === 'HEADER' || tag === 'NAV') return true;
      var role = n.getAttribute && n.getAttribute('role');
      if (role === 'banner' || role === 'navigation') return true;
      n = n.parentElement;
    }
    return false;
  }

  /** 셀렉터 우선, 없으면 라벨 텍스트로 폴백해서 화면에 보이는 요소를 찾는다.
   * 날짜 셀처럼 클릭 후에만 "선택됨" 같은 상태 문구가 뒤에 붙는 라벨은 정확히 일치가
   * 안 되므로, 정확 일치가 실패하면 앞부분(날짜/편명 등 실제 내용)이 같은 요소로
   * 한 번 더 찾는다. 너무 짧은 라벨(확인/동의 등)은 접두어만으로 오매칭 위험이 있어
   * 제외한다. */
  function findEl(sel, text, opts) {
    opts = opts || {};
    if (sel) {
      try {
        var list = document.querySelectorAll(sel);
        for (var i = 0; i < list.length; i++) if (visible(list[i])) return list[i];
      } catch (e) {}
    }
    if (text && !opts.selectorOnly) {
      var all = candidates(document);
      for (var k = 0; k < all.length; k++) {
        if (visible(all[k]) && !inChrome(all[k]) && label(all[k]) === text) return all[k];
      }
      if (text.length > PREFIX_LEN) {
        var prefix = text.slice(0, PREFIX_LEN);
        for (var j = 0; j < all.length; j++) {
          if (visible(all[j]) && !inChrome(all[j]) && label(all[j]).indexOf(prefix) === 0) return all[j];
        }
      }
    }
    return null;
  }

  /* 로그인이 살아 있는가.
   * 매일 자동 시도를 걸어두면 밤새 세션이 풀리는 게 가장 흔한 실패다. 그 상태로
   * 09:00 에 발사하면 로그인 화면만 붙잡고 헛돌다 끝난다 - 그날 좌석은 날아간다.
   * 확실히 로그아웃일 때만 false 를 돌린다(판정이 애매하면 진행시킨다 - 멀쩡한
   * 발사를 막는 게 더 나쁘다). */
  function loggedOut() {
    var all = candidates(document), sawLogin = false, sawMy = false;
    for (var i = 0; i < all.length; i++) {
      if (!visible(all[i])) continue;
      var t = label(all[i]).replace(/\s/g, '');
      if (t === '로그인' || t === 'Login' || t === 'SignIn') sawLogin = true;
      if (t.indexOf('마이페이지') !== -1 || t.indexOf('로그아웃') !== -1) sawMy = true;
    }
    return sawLogin && !sawMy;
  }

  /** 라벨에 text 가 "들어 있는" 요소를 찾는다.
   * ensure 컨트롤은 라벨이 상태를 담는다("통화 KRW" / "통화 USD"). 정확 일치로는
   * 영원히 못 찾으므로 부분 일치가 필요하다. 여러 개면 라벨이 가장 짧은 것
   * (= 가장 구체적인 요소)을 고른다. */
  function findContaining(text, exclude) {
    if (!text) return null;
    var all = candidates(document), best = null, bestLen = 1e9;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el === exclude || !visible(el) || inChrome(el)) continue;
      var t = label(el);
      if (t.indexOf(text) === -1) continue;
      if (t.length < bestLen) { best = el; bestLen = t.length; }
    }
    return best;
  }

  /** 재생이 단계를 못 찾고 멈췄을 때 왜 못 찾았는지 - 셀렉터가 몇 개 매치됐는지,
   * 텍스트가 일치하는 후보가 있는지 - 를 요약한다. 스크린샷 한 장으로 원인 파악이
   * 되도록 패널 상태줄에 그대로 얹는 용도. */
  function diagnose(sel, text) {
    var out = { selMatches: 0, selVisible: 0, selError: null, textMatches: 0, textVisible: 0, prefixMatches: 0 };
    if (sel) {
      try {
        var list = document.querySelectorAll(sel);
        out.selMatches = list.length;
        for (var i = 0; i < list.length; i++) if (visible(list[i])) out.selVisible++;
      } catch (e) { out.selError = String(e.message || e).slice(0, 60); }
    }
    if (text) {
      var all = candidates(document);
      for (var k = 0; k < all.length; k++) {
        if (inChrome(all[k])) continue;
        if (label(all[k]) === text) {
          out.textMatches++;
          if (visible(all[k])) out.textVisible++;
        }
      }
      if (text.length > PREFIX_LEN) {
        var prefix = text.slice(0, PREFIX_LEN);
        for (var j = 0; j < all.length; j++) {
          if (visible(all[j]) && !inChrome(all[j]) && label(all[j]).indexOf(prefix) === 0) out.prefixMatches++;
        }
      }
    }
    return out;
  }

  function diagnoseText(sel, text) {
    var d = diagnose(sel, text);
    if (d.selError) return '셀렉터 오류: ' + d.selError;
    var parts = [];
    parts.push('셀렉터 매치 ' + d.selMatches + '개(보임 ' + d.selVisible + ')');
    if (d.prefixMatches) parts.push('접두어일치(보임) ' + d.prefixMatches + '개');
    parts.push('텍스트일치 ' + d.textMatches + '개(보임 ' + d.textVisible + ')');
    return parts.join(', ');
  }

  /* el.click() 은 'click' 이벤트 하나만 던진다. 최신 디자인시스템 커스텀 엘리먼트
   * (kds-button_1 류) 는 내부적으로 pointerdown/pointerup 이나 mousedown/mouseup 을
   * 듣고 반응하는 경우가 있어서, click 만 던지면 예외 없이 "성공"으로 보이지만 실제
   * 사이트는 아무 반응을 안 하는 사고가 난다(요소는 찾았는데 화면이 안 넘어감).
   * 실제 마우스 클릭과 같은 이벤트 시퀀스를 순서대로 던져서 이 클래스의 컴포넌트도
   * 커버한다. */
  /* 커스텀 엘리먼트(kds-button 계열)는 껍데기만 DOM 에 보이고, 실제 클릭 핸들러는
   * shadow root 안쪽 네이티브 <button> 에 붙어 있다. 이벤트는 위로만 올라가지 shadow
   * 안쪽으로 내려가지 않으므로, 껍데기에 클릭을 쏘면 예외도 안 나면서 아무 반응이
   * 없다(요소는 찾았는데 화면이 안 넘어가는 증상). 안쪽 진짜 버튼까지 파고든다.
   * 감싸기만 하는 래퍼(자식 하나)도 같이 통과한다. */
  function realTarget(el) {
    var best = el, n = el;
    for (var d = 0; n && d < 6; d++) {
      var next = null;
      if (n.shadowRoot) {
        try { next = n.shadowRoot.querySelector('button, a[href], input, [role="button"]'); } catch (e) {}
      }
      if (!next && n.children && n.children.length === 1) next = n.children[0];
      if (!next) break;
      n = next;
      if (/^(BUTTON|A|INPUT)$/.test(n.tagName)) best = n;
    }
    return best;
  }

  function fireClick(el) {
    el = realTarget(el);
    try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    var r;
    try { r = el.getBoundingClientRect(); } catch (e) { r = { left: 0, top: 0, width: 0, height: 0 }; }
    var cx = r.left + r.width / 2, cy = r.top + r.height / 2;
    var base = { bubbles: true, cancelable: true, composed: true, view: window, clientX: cx, clientY: cy };
    function dispatch(Ctor, type, extra) {
      try { el.dispatchEvent(new Ctor(type, Object.assign({}, base, extra || {}))); } catch (e) {}
    }
    if (window.PointerEvent) {
      dispatch(PointerEvent, 'pointerdown', { pointerId: 1, pointerType: 'mouse', isPrimary: true });
    }
    dispatch(MouseEvent, 'mousedown', { button: 0 });
    if (window.PointerEvent) {
      dispatch(PointerEvent, 'pointerup', { pointerId: 1, pointerType: 'mouse', isPrimary: true });
    }
    dispatch(MouseEvent, 'mouseup', { button: 0 });
    try { el.click(); } catch (e) { dispatch(MouseEvent, 'click', { button: 0 }); }
  }

  /** 라벨에서 월/일을 뽑아 "MM-DD" 로 만든다. "16 08월 16일 (월) , 성수기 일반석"
   * 같은 라벨에서 08-16 을 얻는다. 못 뽑으면 null. */
  function monthDay(text) {
    var t = String(text || '');
    /* 연-월-일이 먼저다. "2027-08-22" 에 월-일 패턴을 먼저 대면 "27-08" 을 집어
     * 27월로 읽고 버린다. */
    var m = t.match(/(\d{4})\s*[-\/.]\s*(\d{1,2})\s*[-\/.]\s*(\d{1,2})/);   // 2027-08-22
    if (m) m = [m[0], m[2], m[3]];
    if (!m) m = t.match(/(\d{1,2})\s*월\s*(\d{1,2})\s*일/);                   // 08월 16일
    if (!m) m = t.match(/(\d{1,2})\s*[-\/.]\s*(\d{1,2})/);                   // 08-16, 8/16
    if (!m) return null;
    var mo = +m[1], d = +m[2];
    if (mo < 1 || mo > 12 || d < 1 || d > 31) return null;
    return ('0' + mo).slice(-2) + '-' + ('0' + d).slice(-2);
  }

  /** 목표 날짜 입력이 감지된 셀과 같은 날인가. "08-27", "8/27", "08월 27일" 모두 받는다. */
  function sameDate(expect, labelText) {
    var a = monthDay(expect), b = monthDay(labelText);
    if (!a || !b) return null;    // 판정 불가 - 호출자가 결정
    return a === b;
  }

  /** 달력에서 가장 나중(=제일 늦게 열린) 날짜 셀을 찾는다.
   *
   * 예전에는 요금등급 글자(일반석/프레스티지)가 붙은 셀만 후보로 삼았다. 그런데 그
   * 마커는 화면이 다 그려진 뒤에야 붙는 경우가 있어서, 재생이 그 전에 훑으면 하루
   * 이틀 앞선 날짜를 골랐다(실측: 22일이어야 하는데 20일이 선택됨).
   * 등급 마커와 무관하게 "날짜 숫자가 있는 마지막 셀" 을 고른다. 엉뚱한 날 예매는
   * 목표 날짜 검사가 막는다 - 그게 훨씬 확실한 방어선이다. */
  /* 목표 날짜를 정했으면 그 날짜 칸을 직접 찾는다.
   *
   * 예전에는 목표 날짜가 있어도 "가장 나중 날짜" 만 찾아놓고 목표와 다르면 새로고침을
   * 반복했다. 목표가 최신일과 같을 때만 되는 구조라, 그렇지 않으면 영원히 돌았다
   * (실측 2026-08-28: 목표 08-18, 감지 08-22 로 무한 새로고침).
   *
   * 09:00 에는 목표 날짜가 아직 안 열려 있는 게 정상이다 - 그때만 못 찾고,
   * 그 경우에만 새로고침해서 다시 본다. */
  function findOpenDate(idPrefix, want) {
    if (!want) return findLatestOpenDate(idPrefix);
    var md = monthDay(want);
    if (!md) return null;
    var list = openDateCells(idPrefix);
    for (var i = 0; i < list.length; i++) {
      if (sameDate(md, label(list[i])) === true) return list[i];
    }
    return null;
  }

  /* 지금 고를 수 있는 날짜 칸들 (문서 순서). */
  function openDateCells(idPrefix) {
    idPrefix = idPrefix || 'dep-fare-';
    var list, out = [];
    try { list = document.querySelectorAll('[id^="' + idPrefix + '"]'); } catch (e) { return out; }
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (!visible(el)) continue;
      if (el.getAttribute('aria-disabled') === 'true' || el.disabled) continue;
      var cls = typeof el.className === 'string' ? el.className : '';
      if (/disable|unavail|soldout/i.test(cls)) continue;
      if (!/\d/.test(label(el))) continue;    // 달력 여백 칸(빈 셀) 제외
      out.push(el);
    }
    return out;
  }

  function findLatestOpenDate(idPrefix) {
    idPrefix = idPrefix || 'dep-fare-';
    var list;
    try { list = document.querySelectorAll('[id^="' + idPrefix + '"]'); } catch (e) { return null; }
    var best = null;
    for (var i = 0; i < list.length; i++) {
      var el = list[i];
      if (!visible(el)) continue;
      if (el.getAttribute('aria-disabled') === 'true' || el.disabled) continue;
      var cls = typeof el.className === 'string' ? el.className : '';
      if (/disable|unavail|soldout/i.test(cls)) continue;
      if (!/\d/.test(label(el))) continue;    // 달력 여백 칸(빈 셀) 제외
      best = el;
    }
    return best;
  }

  /* 좌석 등급으로 항공편 카드를 찾는다.
   * 라벨이 "항공편명 KE901 일반석 52,500 마일" 형태라, 등급 이름 + '마일' 로 고르면
   * 편명이나 필요 마일리지가 바뀌어도, id/클래스(#classEconomyList0 등)가 등급마다
   * 달라도 그대로 동작한다. 같은 등급 카드가 여러 개면 라벨이 가장 짧은 것(=가장
   * 구체적인 요소)을 고른다. */
  /* 이 운임 카드가 대한항공이 직접 띄우는 편의 것인가.
   *
   * 파리 노선처럼 조회 결과에 코드셰어가 같이 뜬다. KE5901 은 편명만 KE 이고 실제로는
   * 에어프랑스 운항이라 편명으로는 못 가른다. 화면에는 편마다 "대한항공 운항" /
   * "에어프랑스 운항" 이 붙으므로 그것을 본다.
   * 카드에서 위로 올라가다 처음 만나는 '운항' 표기가 그 편의 것이다 (더 올라가면
   * 두 편을 다 품은 목록이라 구분이 사라진다). */
  function operatedByKE(el) {
    var n = el;
    for (var d = 0; d < 8 && n; d++) {
      var t = '';
      try { t = n.innerText || ''; } catch (e) {}
      if (/운항/.test(t)) return /대한항공\s*운항/.test(t);
      n = n.parentElement;
    }
    return false;   // 표기를 못 찾으면 보수적으로 제외한다
  }

  /** 좌석 등급으로 항공편 카드를 찾는다.
   * opts.anyCarrier 를 켜지 않으면 대한항공 운항편만 고른다 (코드셰어 제외). */
  function findCabin(cabin, opts) {
    if (!cabin) return null;
    opts = opts || {};
    var all = candidates(document), best = null, bestLen = 1e9;
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (!visible(el) || inChrome(el)) continue;
      var t = label(el);
      if (t.indexOf(cabin) === -1 || t.indexOf('마일') === -1) continue;
      if (!opts.anyCarrier && !operatedByKE(el)) continue;
      if (t.length < bestLen) { best = el; bestLen = t.length; }
    }
    return best;
  }

  /** 지금 화면에 그 등급이 코드셰어(외항사)로만 있는가. 멈출 때 이유를 정확히 말하려고. */
  function cabinOnlyCodeshare(cabin) {
    return !findCabin(cabin) && !!findCabin(cabin, { anyCarrier: true });
  }

  // A click can be lost during the currency redraw. Confirm both the actual
  // radio and the application's nonzero mileage total before submitting Next.
  function cabinSelection(cabin) {
    var el = findCabin(cabin);
    var id = el && el.getAttribute('for');
    var input = id ? document.getElementById(id) : null;
    var checked = !!(input && input.type === 'radio' && input.checked);
    var widget = document.querySelector('#payment-widget');
    var match = label(widget).match(/([0-9][0-9,]*)\s*마일/);
    var priced = !!(match && Number(match[1].replace(/,/g, '')) > 0);
    return {el: el, checked: checked, priced: priced, ready: checked && priced};
  }

  /** 조회 결과(운임 카드)가 화면에 그려졌는가.
   *
   * "고른 등급이 없다" 와 "페이지가 아직 안 떴다" 는 전혀 다른 상황인데, findCabin 은
   * 둘 다 null 을 돌려준다. 구분하지 않으면 페이지가 뜨기도 전에 "좌석 없음" 으로
   * 읽고 새로고침해서, 페이지가 뜰 틈이 없다 - 실측(2026-08-28)에서 조회 화면이
   * 무한 새로고침만 했다. 달력에서 똑같은 사고를 이미 겪고 openDateCells 로 고쳤는데
   * 좌석 쪽은 그대로였다.
   *
   * 등급이 무엇이든 운임 카드에는 마일 표시가 붙는다. 하나라도 있으면 목록이
   * 그려진 것이고, 그때부터 "없다" 고 말할 수 있다. */
  function cabinListReady() {
    var all;
    try { all = candidates(document); } catch (e) { return false; }
    for (var i = 0; i < all.length; i++) {
      if (!visible(all[i]) || inChrome(all[i])) continue;
      if (label(all[i]).indexOf('마일') !== -1) return true;
    }
    return false;
  }

  /* 위험품 안내 팝업은 끝까지 내려야 [확인] 이 열린다. "아래로 스크롤" 버튼이
   * 스크롤에 따라 움직이거나 화면 밖으로 밀리면 클릭이 빗나가 거기서 멈춘다.
   * 버튼을 누르는 것과 별개로, 스크롤 가능한 영역을 직접 바닥까지 내려 확실히 한다.
   * (사이트가 스크롤 이벤트로 판정하는 경우도 있어 버튼 클릭도 그대로 유지한다) */
  function scrollToBottom() {
    var n = 0;
    var all;
    try { all = document.querySelectorAll('div,section,article,main,ul,ol'); } catch (e) { return 0; }
    for (var i = 0; i < all.length; i++) {
      var el = all[i];
      if (el.scrollHeight - el.clientHeight < 24) continue;   // 스크롤할 게 없음
      if (!visible(el)) continue;
      var oy;
      try { oy = getComputedStyle(el).overflowY; } catch (e) { continue; }
      if (oy !== 'auto' && oy !== 'scroll') continue;
      try {
        el.scrollTop = el.scrollHeight;
        el.dispatchEvent(new Event('scroll', { bubbles: true }));
        n++;
      } catch (e) {}
    }
    try {
      var d = document.scrollingElement || document.documentElement;
      if (d && d.scrollHeight - d.clientHeight > 24) { d.scrollTop = d.scrollHeight; n++; }
    } catch (e) {}
    return n;
  }

  /** 조회 화면이 "어느 날을 조회 중인지". "MM-DD" 또는 null.
   *
   * 주소에는 날짜가 없고(/departure 뿐), 서버 응답의 departureDate 는 좌석이 있을
   * 때만 나온다. 그런데 우리가 정작 알아야 하는 순간은 좌석이 아직 없을 때다 -
   * 09:00 직전에 목표 날짜로 맞춰두고 기다리는 상황이 그것이다.
   *
   * 그때 남는 근거는 검색 위젯의 날짜 입력칸뿐이다(실측 라벨: "08월 21일 (토)").
   * 왕복이면 가는 날/오는 날 둘이 있으므로 첫 번째를 쓴다. */
  var DATEINPUT = 'kds-dateinput, [class*="ui-dateinput__host"], [class*="-dateinput"]';

  function searchedDate() {
    var el = dateInputEl();
    return el ? monthDay(label(el)) : null;
  }

  /* 조회 화면의 날짜 선택 달력.
   *
   * 실측 구조(2026-08-27):
   *   #month202708 > … > td.ui-datepicker__td.-available   라벨 "18 18일, 수요일"
   *
   * 두 가지가 중요하다:
   *   - 컨테이너 id 에 연월이 그대로 박혀 있다(month + YYYYMM). 그래서 몇 번째
   *     칸인지 세지 않아도 목표 날짜를 정확히 집을 수 있다. 화면이 조금 바뀌어도
   *     버티는 유일한 방법이다.
   *   - 예약 가능한 날만 -available 이 붙는다. 이게 "그 날짜가 열렸는가" 를 알려주는
   *     신호다. 09:00 전에는 새로 열릴 날짜에 이게 없다.
   *
   * 라벨은 "18 18일, 수요일" 이라 월이 없다. monthDay() 로는 못 읽는다 - 월은
   * 컨테이너 id 에서 오고 일자만 라벨에서 읽는다. */
  function findPickerDate(mmdd, year) {
    var md = monthDay(mmdd);
    if (!md) return null;
    var y = year || nextYearFor(md);
    var box;
    try { box = document.getElementById('month' + y + md.slice(0, 2)); } catch (e) { return null; }
    if (!box) return { el: null, available: false, why: '그 달(' + y + md.slice(0, 2) + ') 달력이 화면에 없습니다' };
    var want = +md.slice(3), tds;
    try { tds = box.querySelectorAll('td'); } catch (e) { return null; }
    for (var i = 0; i < tds.length; i++) {
      var td = tds[i];
      if (!visible(td)) continue;
      /* 라벨 앞머리의 숫자가 그 날의 일자다. "18 18일, 수요일" -> 18 */
      if (parseInt(label(td), 10) !== want) continue;
      var av = false;
      try { av = td.classList.contains('-available'); } catch (e) {}
      return { el: td, available: av,
               why: av ? '' : md + ' 은(는) 아직 고를 수 없습니다 (예약 가능 창 밖)' };
    }
    return { el: null, available: false, why: md + ' 칸을 그 달 달력에서 찾지 못했습니다' };
  }

  /* 조회 결과 가운데의 날짜 띠. 이걸 눌러야 그 자리에서 다시 조회된다.
   *
   * 실측(2026-08-28):
   *   #flexible-date > li.flexible-date__item > button.flexible-date__link.-active
   *   라벨 "출발일 21 (토) 선택 가능"
   *
   * 위쪽 검색 위젯의 날짜칸을 고치고 [항공편 검색] 을 누르는 길도 있는데, 그건
   * 달력 페이지로 되돌아간다. 건너뛰려던 바로 그 페이지다. 여기 띠를 눌러야 한다.
   *
   * 라벨에 월이 없다("21 (토)"). 목표 날짜의 일자만 맞춰 보고, 진짜 그 날짜로
   * 조회됐는지는 서버 응답으로 확인한다. */
  var STRIP_SEL = '#flexible-date li, .flexible-date__item';

  function findStripDate(mmdd) {
    var md = monthDay(mmdd);
    if (!md) return null;
    /* 띠 라벨에는 월이 없다("21 (토)"). 그래서 일자만 맞춰 보면 다른 달의 같은
     * 일자를 집는다 - 12-25 를 찾다가 8월 25일을 누르는 식이다. 지금 화면이 몇 월을
     * 보고 있는지는 검색 위젯의 날짜칸이 알려주므로, 달이 다르면 아예 없다고 한다. */
    var nowMd = searchedDate();
    var crossMonth = nowMd && nowMd.slice(0, 2) !== md.slice(0, 2);
    var Pstrip = W.KE_PROBE || window.KE_PROBE;
    var dates = crossMonth && Pstrip && Pstrip.latestStripDates ? Pstrip.latestStripDates() : [];
    if (crossMonth && (dates.indexOf(md) < 0 || dates.indexOf(nowMd) < 0)) {
      return { el: null, selectable: false,
               why: md + ' 은(는) 이 화면(' + nowMd + ')의 날짜 띠에 없습니다 (다른 달)' };
    }
    var want = +md.slice(3), items;
    try { items = document.querySelectorAll(STRIP_SEL); } catch (e) { return null; }
    if (!items.length) return { el: null, selectable: false, why: '날짜 띠가 화면에 없습니다' };
    var targetIndex = -1;
    if (crossMonth) {
      // 날짜 선택 후 서버 목록은 재정렬돼도 DOM 창 범위는 그대로 남을 수 있다.
      // 두 목록의 절대 index 대신 현재 선택일과 목표일 사이의 간격을 대조한다.
      var days = Array.prototype.map.call(items, function(it) {
        var m = label(it).match(/출발일\s*(\d{1,2})/);
        return m ? +m[1] : null;
      });
      var currentDay = +nowMd.slice(3), currentIndex = days.indexOf(currentDay);
      targetIndex = days.indexOf(want);
      if (dates.filter(function(d){return d===md;}).length !== 1 ||
          currentIndex < 0 || targetIndex < 0 ||
          days.filter(function(d){return d===want;}).length !== 1 ||
          days.filter(function(d){return d===currentDay;}).length !== 1 ||
          targetIndex-currentIndex !== dates.indexOf(md)-dates.indexOf(nowMd)) {
        return {el:null,selectable:false,why:'날짜 띠와 최신 서버 날짜의 대응이 불명확합니다'};
      }
    }
    for (var i = 0; i < items.length; i++) {
      if (crossMonth && i !== targetIndex) continue;
      var it = items[i];
      if (!visible(it)) continue;
      var t = label(it);
      /* "출발일 21 (토) 선택 가능" - 출발일 뒤 숫자가 그 날의 일자다. */
      var m = t.match(/출발일\s*(\d{1,2})/) || t.match(/(\d{1,2})/);
      if (!m || +m[1] !== want) continue;
      var btn = it.querySelector('button, a') || it;
      var no = /없음/.test(t);                       // 좌석 없음 / 운항편 없음
      var ok = !no && (/선택\s*가능/.test(t) || hasCls(btn, '-active') || hasCls(it, '-active'));
      return { el: btn, selectable: ok, label: t.slice(0, 40),
               why: ok ? '' : (md + ' 은(는) 아직 고를 수 없습니다: ' + t.slice(0, 24)) };
    }
    return { el: null, selectable: false, why: md + ' 이(가) 날짜 띠에 없습니다' };
  }

  function hasCls(el, c) {
    try { return !!(el && el.classList && el.classList.contains(c)); } catch (e) { return false; }
  }

  /** 조회 화면의 '가는 날' 입력칸. 눌러야 달력이 열린다.
   *
   * 셀렉터가 겹겹이 걸린다: 바깥 감싸개(div.-dateinput)도, 안쪽 실제 칸
   * (kds-dateinput_1.ui-dateinput__host)도 같이 잡힌다. 바깥을 누르면 안쪽에 붙은
   * 클릭 핸들러가 안 돌아 달력이 열리지 않는다(실측에서 여기서 막혔다).
   * 다른 후보를 품고 있지 않은 것 = 가장 안쪽만 남긴다. */
  function dateInputEl() {
    var els;
    try { els = document.querySelectorAll(DATEINPUT); } catch (e) { return null; }
    var hit = [];
    for (var i = 0; i < els.length; i++) {
      if (visible(els[i]) && monthDay(label(els[i]))) hit.push(els[i]);
    }
    for (var j = 0; j < hit.length; j++) {
      var inner = false;
      for (var k = 0; k < hit.length; k++) {
        if (k !== j && hit[j].contains(hit[k])) { inner = true; break; }
      }
      if (!inner) return hit[j];      // 문서 순서상 첫 번째 = 가는 날
    }
    return null;
  }

  // ---- 달력 건너뛰기(바로 시작) ------------------------------------------
  /* 실측 27.7초 중 절반 이상이 대한항공 페이지가 그려지기를 기다린 시간이다.
   * 우리 폴링을 조여봐야 몇 밀리초다. 남은 방법은 페이지를 덜 거치는 것 하나뿐이라,
   * 달력에서 날짜를 고르고 검색하는 두 단계와 그에 딸린 페이지 전환을 통째로
   * 건너뛰고 조회 페이지로 바로 들어간다. */

  /** 지금(또는 주어진 주소가) 항공편 조회 페이지인가. */
  function onDeparture(href) {
    return /\/booking\/select-award-(wait-)?flight\//.test(
      String(href == null ? location.href : href));
  }

  /* URL 에 박힌 날짜를 모두 찾는다. 왕복(RT)이면 가는 날/오는 날이 둘 다 들어 있어서,
   * 아무거나 바꾸면 오는 날을 망가뜨린다. 그래서 "몇 번째 날짜인지" 까지 돌려주고,
   * 바꿀 때도 그 자리만 고른다. */
  var URL_DATE = /(20\d{2})([-.\/]?)(\d{2})\2(\d{2})/g;

  /** URL 안의 날짜들. [{at, len, y, m, d, sep, mmdd}] */
  function urlDates(url) {
    var out = [], m;
    URL_DATE.lastIndex = 0;
    while ((m = URL_DATE.exec(String(url || '')))) {
      var mo = +m[3], d = +m[4];
      if (mo < 1 || mo > 12 || d < 1 || d > 31) continue;
      out.push({ at: m.index, len: m[0].length, y: +m[1], sep: m[2],
                 m: mo, d: d, mmdd: ('0' + mo).slice(-2) + '-' + ('0' + d).slice(-2) });
    }
    return out;
  }

  /** 오늘(KST) 이후로 가장 먼저 오는 그 월일의 연도. 마일리지 예매는 360일쯤
   *  앞을 보므로 "다음에 오는 그 날" 이 항상 맞다. */
  function nextYearFor(mmdd, nowMs) {
    var md = monthDay(mmdd);
    if (!md) return null;
    var p = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Seoul',
      year: 'numeric', month: '2-digit', day: '2-digit' })
      .formatToParts(new Date(nowMs == null ? Date.now() : nowMs))
      .reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    var y = +p.year, today = p.month + '-' + p.day;
    return md > today ? y : y + 1;
  }

  /** 저장해둔 조회 URL 의 "가는 날" 자리를 목표 날짜로 바꾼다.
   *  @param url   지난 번에 붙잡아둔 조회 페이지 주소
   *  @param was   붙잡을 당시의 가는 날 ("08-21") - 어느 자리를 바꿀지 고르는 기준
   *  @param want  이번에 갈 날 ("08-22")
   *  @returns {url, why} - url 이 없으면 why 에 이유가 담긴다 (달력 경로로 되돌아간다) */
  function retarget(url, was, want) {
    var a = monthDay(was), b = monthDay(want);
    if (!b) return { url: null, why: '목표 날짜를 해석하지 못했습니다' };
    if (!url) return { url: null, why: '저장된 조회 주소가 없습니다' };
    if (a === b) return { url: url, why: '' };          // 같은 날 - 손댈 것이 없다
    var ds = urlDates(url);
    if (!ds.length) return { url: null, why: '주소에 날짜가 없어 바꿀 수 없습니다' };
    var hit = null;
    for (var i = 0; i < ds.length; i++) if (ds[i].mmdd === a) { hit = ds[i]; break; }
    /* 어느 자리가 가는 날인지 못 고르면 바꾸지 않는다. 왕복이면 오는 날을 건드릴
     * 위험이 있고, 엉뚱한 날 예매는 3초 버는 것과 비교할 수 없다. */
    if (!hit) return { url: null, why: '주소에서 가는 날(' + a + ') 자리를 찾지 못했습니다' };
    var y = nextYearFor(b);
    var repl = y + hit.sep + b.slice(0, 2) + hit.sep + b.slice(3);
    return { url: url.slice(0, hit.at) + repl + url.slice(hit.at + hit.len), why: '' };
  }

  var U = {
    visible: visible, label: label, cssPath: cssPath, findEl: findEl, CLICKABLE: CLICKABLE,
    candidates: candidates, diagnose: diagnose, diagnoseText: diagnoseText, fireClick: fireClick,
    coverInfo: coverInfo,
    findLatestOpenDate: findLatestOpenDate, findOpenDate: findOpenDate,
    openDateCells: openDateCells, inChrome: inChrome, realTarget: realTarget,
    findCabin: findCabin, cabinListReady: cabinListReady, tabKey: tabKey,
    cabinSelection: cabinSelection,
    operatedByKE: operatedByKE, cabinOnlyCodeshare: cabinOnlyCodeshare,
    scrollToBottom: scrollToBottom, hittable: hittable,
    monthDay: monthDay, sameDate: sameDate, findContaining: findContaining,
    loggedOut: loggedOut,
    onDeparture: onDeparture, searchedDate: searchedDate,
    findPickerDate: findPickerDate, dateInputEl: dateInputEl,
    findStripDate: findStripDate, urlDates: urlDates, retarget: retarget,
    nextYearFor: nextYearFor,
    alreadyOn: alreadyOn
  };
  try { W.KE_UTIL = U; } catch (e) {}
  if (W !== window) { try { window.KE_UTIL = U; } catch (e) {} }
})();
