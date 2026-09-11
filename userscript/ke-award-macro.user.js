// ==UserScript==
// @name         대한항공 마일리지 예매 보조 (KE Award Macro)
// @namespace    local.ke.award
// @version      1.82.0-dirty
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


// ---------- util ----------
try {
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

} catch (e) {
  console.error('[KE] util 로드 실패:', e);
}


// ---------- build ----------
try {
(function () {
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  var B = { version: '1.82.0-dirty', hash: '65c0a84-dirty' };
  try { W.KE_BUILD = B; } catch (e) {}
  if (W !== window) { try { window.KE_BUILD = B; } catch (e) {} }
})();
} catch (e) {
  console.error('[KE] build 로드 실패:', e);
}


// ---------- probe ----------
try {
/* 좌석이 언제 "매진" 이 되는지 알아내기 위한 계측.
 *
 * 사용자 관찰: 달력에서 검색하고 좌석 등급 화면으로 넘어가면 5초 만에 프레스티지가
 * 이미 매진인 날이 있다. 5초 안에 결제까지 끝낸 사람이 있을 리는 없으므로, 남는
 * 설명은 둘 중 하나다.
 *
 *   (가) 좌석을 고르고 넘어가는 순간 서버가 좌석을 잠근다(hold). 그러면 결승선은
 *        '결제하기' 가 아니라 '다음' 이고, 그 뒤 14.8초는 경쟁이 아니다.
 *   (나) 화면에 보인 잔여석이 애초에 캐시된 옛날 값이었다.
 *
 * 어느 쪽인지는 서버만 알지만, 우리가 볼 수 있는 것이 하나 있다: 조회 화면이 좌석
 * 수를 그릴 때 쓰는 API 응답이다. 잔여석 필드가 어떻게 생겼고 언제 0이 되는지를
 * 09:00 에 사람이 지켜볼 수는 없으므로 자동으로 남긴다.
 *
 * 아무것도 바꾸지 않는다 - 오가는 응답을 엿보고 기록만 한다. */
(function () {
  var W = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;
  if (W.KE_PROBE) return;

  var MAX = 40;            // 최근 몇 건까지 들고 있을지
  var CAP = 200000;        // 한 건당 글자 수 상한 (localStorage 가 아니라 메모리다)
  var hits = [];
  var stamp = 0;         // 기록이 늘 때마다 증가. 화면 갱신 여부를 값싸게 판단한다
  var availability = null, availabilitySeq = 0;

  function beginAvailability(url) {
    if (!/\/awardAvailability(?:[.?/#]|$)/i.test(String(url))) return 0;
    var seq = ++availabilitySeq;
    availability = {seq: seq, state: 'pending', startedAt: Date.now()};
    return seq;
  }
  function endAvailability(seq, status, body) {
    // 오래 걸린 이전 요청이 새 조회 상태를 덮지 못하게 한다.
    if (!seq || seq !== availabilitySeq) return;
    var state = status === 200 ? 'valid' : (status ? 'http-error' : 'network-error');
    var code = null;
    if (state === 'valid') {
      try {
        var d = JSON.parse(body);
        if (d && (d.error || d.errors || d.errorCode ||
            (d.code != null && !/^(0|0000|200|SUCCESS)$/i.test(String(d.code))))) {
          state = 'application-error';
          var rawCode = String(d.code || d.errorCode || '');
          if (/^[A-Z0-9_.-]{1,32}$/i.test(rawCode)) code = rawCode;
        } else if (!d || !Array.isArray(d.upsellBoundAvailList)) state = 'schema-error';
      } catch (e) { state = 'schema-error'; }
    }
    availability = {seq: seq, state: state, status: status, code: code,
                    startedAt: availability.startedAt, completedAt: Date.now()};
  }

  /* 좌석/운임 조회로 보이는 응답만 남긴다. 전부 남기면 로그인 토큰 같은 것까지
   * 딸려 들어와 내보내기가 위험해진다. */
  var WANTED = /(availab|award|bonus|flight|fare|segment|seat|payment)/i;
  /* 실측에서 걸러야 했던 것들. 구글 애널리틱스는 현재 주소를 파라미터로 실어 보내서
   * 'select-award-flight' 가 그 안에 들어가 WANTED 에 걸렸고, 화면 문구 사전은
   * '좌석' 이라는 낱말이 수백 번 나와 기록을 통째로 밀어냈다. */
  var NOISE = /(analytics|googletagmanager|doubleclick|\/collect\?|languageInfo|loading_)/i;
  var SEATY = /"(seatCount|cabinSeatCount|bookingClassSeatCount|remain\w*|avail\w*Seat\w*|numberOfSeats?|bookableSeats?)"\s*:/i;

  /* req = {method, body, headers}. 응답만으론 같은 조회를 다시 쏠 수 없어서 요청도 남긴다.
   * 계측기가 이걸 그대로 되쏘면 페이지 새로고침(~2초+렌더) 없이 재조회가 되어
   * 표본 간격이 6초에서 1초대로 줄어든다. 되쏘는 주체는 페이지 자신이라
   * 세션·쿠키·오리진이 원래 요청과 같다. */
  function note(kind, url, status, body, req) {
    try {
      if (!WANTED.test(String(url)) || NOISE.test(String(url))) return;
      var text = String(body == null ? '' : body);
      if (text.length > CAP) text = text.slice(0, CAP) + '…(잘림)';
      hits.push({ at: Date.now(), kind: kind, url: String(url).slice(0, 300),
                  status: status, seaty: SEATY.test(text), body: text,
                  req: req || null });
      if (hits.length > MAX) hits.shift();
      stamp++;
    } catch (e) {}
  }

  // ---- fetch ----
  try {
    var of = W.fetch;
    if (typeof of === 'function' && !of.__keProbe) {
      var nf = function (input, init) {
        var url = (input && input.url) || input;
        var aq = beginAvailability(url);
        var rq = null;
        try {
          var h = {};
          var hs = (init && init.headers) || (input && input.headers);
          if (hs) {
            if (typeof hs.forEach === 'function') hs.forEach(function (v, k) { h[k] = v; });
            else Object.keys(hs).forEach(function (k) { h[k] = hs[k]; });
          }
          rq = { method: (init && init.method) || (input && input.method) || 'GET',
                 body: (init && typeof init.body === 'string') ? init.body : null,
                 headers: h };
        } catch (e) {}
        return of.apply(this, arguments).then(function (res) {
          try {
            if (WANTED.test(String(url))) {
              res.clone().text().then(function (t) {
                endAvailability(aq, res.status, t); note('fetch', url, res.status, t, rq);
              }, function () { endAvailability(aq, 0, ''); });
            }
          } catch (e) {}
          return res;
        }, function (error) { endAvailability(aq, 0, ''); throw error; });
      };
      nf.__keProbe = true;
      W.fetch = nf;
    }
  } catch (e) {}

  // ---- XMLHttpRequest ----
  try {
    var XP = W.XMLHttpRequest && W.XMLHttpRequest.prototype;
    if (XP && !XP.__keProbe) {
      var oo = XP.open, os = XP.send, osh = XP.setRequestHeader;
      XP.open = function (m, u) {
        try { this.__keUrl = u; this.__keMethod = m; this.__keHeaders = {}; } catch (e) {}
        return oo.apply(this, arguments);
      };
      /* 헤더까지 잡아야 되쏠 수 있다. 조회 API 는 Content-Type 말고도
       * 세션/티켓 성격의 헤더를 요구할 수 있어서 원본 그대로 실어보낸다. */
      XP.setRequestHeader = function (k, v) {
        try { (this.__keHeaders = this.__keHeaders || {})[k] = v; } catch (e) {}
        return osh.apply(this, arguments);
      };
      XP.send = function (body) {
        var self = this;
        var aq = beginAvailability(self.__keUrl);
        try { self.__keBody = (typeof body === 'string') ? body : null; } catch (e) {}
        try {
          self.addEventListener('load', function () {
            var t = '';
            try { t = (self.responseType === '' || self.responseType === 'text') ? self.responseText : ''; } catch (e) {}
            if (self.responseType === 'json') t = JSON.stringify(self.response);
            endAvailability(aq, self.status, t);
            note('xhr', self.__keUrl, self.status, t,
                 { method: self.__keMethod || 'GET', body: self.__keBody || null,
                   headers: self.__keHeaders || {} });
          });
          ['error', 'abort', 'timeout'].forEach(function (event) {
            self.addEventListener(event, function () { endAvailability(aq, 0, ''); }, {once: true});
          });
        } catch (e) {}
        return os.apply(this, arguments);
      };
      XP.__keProbe = true;
    }
  } catch (e) {}

  /* ---- 실측으로 확인된 응답 모양 (2026-08-27) --------------------------
   *
   * api/ap/booking/avail/awardAvailability 안:
   *   commercialFareFamilyList: [
   *     {fareFamily:"KEBONUSEY", seatCount:"9", soldout:false, totalMileage:"35000"},
   *     {fareFamily:"KEBONUSPR", seatCount:"0", soldout:true,  totalMileage:"62500"}
   *   ]
   * KEBONUSPR 이 프레스티지다. 이게 매진 판정의 출처다 - 화면에는 없다.
   *
   * seatCount "9" 는 실제 9석이 아니라 "9석 이상" 이라는 항공사 표준 상한값이다.
   * 반대로 "0" 은 진짜 0이다. 우리가 알고 싶은 것은 09:00 직후에 1~2였다가 0이
   * 되는지(누가 채간 것), 아니면 처음부터 0인지(그날 그 등급이 아예 없던 것)다.
   * 둘은 대응이 전혀 다르다. */
  var FAMILY = { KEBONUSPR: '프레스티지', KEBONUSEY: '일반석',
                 KEBONUSPE: '프리미엄', KEBONUSFR: '일등석' };

  /** 기록에서 등급별 좌석 수 변화를 뽑아낸다. [{at, family, seatCount, soldout}] */
  function seatTimeline() {
    var out = [];
    hits.forEach(function (h) {
      var d;
      try { d = JSON.parse(h.body); } catch (e) { return; }
      var bounds = (d && d.upsellBoundAvailList) || [];
      bounds.forEach(function (b) {
        ((b && b.availFlightList) || []).forEach(function (f) {
          ((f && f.commercialFareFamilyList) || []).forEach(function (c) {
            out.push({
              at: h.at,
              flight: (f.flightInfoList && f.flightInfoList[0]
                       && (f.flightInfoList[0].carrierCode + f.flightInfoList[0].flightNumber)) || '',
              date: String(f.departureDate || '').slice(0, 8),
              family: c.fareFamily || '',
              name: FAMILY[c.fareFamily] || c.fareFamily || '',
              seatCount: c.seatCount,
              soldout: !!c.soldout,
              mileage: c.totalMileage
            });
          });
        });
      });
    });
    return out;
  }

  /** 지금 조회 화면이 어느 날을 보고 있는가. "MM-DD" 또는 null.
   *
   * 조회 페이지 주소에는 날짜가 없다(/departure 뿐). 그래서 화면만 보고는 지금
   * 몇 일자를 조회 중인지 확신할 수 없는데, 서버 응답에는 있다. 달력을 건너뛰고
   * 이 페이지에서 시작할 때 "엉뚱한 날 좌석을 누르는" 사고를 막는 유일한 근거다. */
  function shownDate(since) {
    /* since 를 주면 그 시각 이후에 온 응답만 본다.
     *
     * 이게 없으면 낡은 응답이 진실을 가린다: 날짜를 바꿔 눌렀는데 재조회가 안 되면
     * 예전 응답이 그대로 남아, 마치 그 날짜를 조회 중인 것처럼 보인다.
     * 실측(2026-08-27)에서 정확히 그랬다 - 머리말은 08-18 인데 목록은 08-21 이었다. */
    var tl = seatTimeline();
    for (var i = tl.length - 1; i >= 0; i--) {
      if (since && tl[i].at < since) continue;
      var d = tl[i].date;                       // YYYYMMDD
      if (d && d.length === 8) return d.slice(4, 6) + '-' + d.slice(6, 8);
    }
    return null;
  }

  /** 이 화면의 날짜 띠에 있는 날들. ["08-16", ...] - 목표 날짜가 여기 있어야 고를 수 있다. */
  function latestStripDates() {
    for (var i=hits.length-1;i>=0;i--) {
      if (hits[i].status !== 200 || !/awardAvailability/.test(hits[i].url)) continue;
      try {
        var data=JSON.parse(hits[i].body), dates=[];
        ((data && data.upsellBoundAvailList)||[]).forEach(function(b) {
          (b.upsellCalendarFareList||[]).forEach(function(c) {
            var d=String(c.date||'');
            if (/^\d{8}$/.test(d)) dates.push(d.slice(4,6)+'-'+d.slice(6,8));
          });
        });
        return dates;
      } catch(e) { return []; }
    }
    return [];
  }

  function stripDates() {
    var out = [];
    hits.forEach(function (h) {
      var d;
      try { d = JSON.parse(h.body); } catch (e) { return; }
      ((d && d.upsellBoundAvailList) || []).forEach(function (b) {
        ((b && b.upsellCalendarFareList) || []).forEach(function (c) {
          var s2 = String(c.date || '');
          if (s2.length !== 8) return;
          var mmdd = s2.slice(4, 6) + '-' + s2.slice(6, 8);
          if (out.indexOf(mmdd) < 0) out.push(mmdd);
        });
      });
    });
    return out;
  }

  /* 결제 수단은 화면에서 눈으로 찾을 필요가 없다. 서버가 목록으로 준다.
   * 돌아오는 편에 네이버페이가 없다는 것도 여기서 미리 알 수 있다. */
  function payTypes() {
    for (var i = hits.length - 1; i >= 0; i--) {
      if (!/GetAvailablePaymentType/i.test(hits[i].url)) continue;
      try {
        var d = JSON.parse(hits[i].body);
        return (d.paymentTypeList || []).map(function (t) { return t.paymentTypeCode; });
      } catch (e) { return null; }
    }
    return null;
  }

  /* ---- 조회 조건이 어디에 사는가 -----------------------------------------
   *
   * 실측(2026-08-27): 조회 페이지 주소는 그냥 /booking/select-award-flight/departure
   * 다. 물음표 뒤가 비어 있다. 그런데 이 화면은 노선·날짜·인원·등급을 알고 있고
   * awardAvailability 를 그 조건으로 부른다. 그 조건이 주소가 아니라면 어딘가에는
   * 있다 - sessionStorage, localStorage, 아니면 서버 세션이다.
   *
   * 앞의 둘이면 목표 날짜를 그 자리에 써넣고 조회 페이지로 바로 들어갈 수 있다.
   * 서버 세션이면 못 한다. 추측하지 말고 실제로 무엇이 들어 있는지 본다.
   *
   * 값은 그대로 두고 읽기만 한다. 다만 사람 정보나 토큰이 섞여 있을 수 있으므로
   * 내보낼 때는 날짜처럼 보이는 것만 남기고 나머지는 길이만 적는다. */
  var DATEISH = /(20\d{2})[-.\/]?(\d{2})[-.\/]?(\d{2})/;

  function scanStore(store, name) {
    var out = [];
    try {
      for (var i = 0; i < store.length; i++) {
        var k = store.key(i), v = '';
        try { v = String(store.getItem(k)); } catch (e) { continue; }
        if (k.indexOf('ke_award') === 0) continue;      // 우리 것은 뺀다
        var m = v.match(DATEISH);
        out.push({ store: name, key: k, len: v.length,
                   date: m ? m[0] : '', sample: m ? v.slice(Math.max(0, m.index - 60),
                                                             m.index + 60) : '' });
      }
    } catch (e) {}
    return out;
  }

  /** 지금 화면의 저장소에서 '날짜를 들고 있는 항목' 을 찾는다. */
  function storeHints() {
    var all = [];
    try { all = all.concat(scanStore(W.sessionStorage, 'session')); } catch (e) {}
    try { all = all.concat(scanStore(W.localStorage, 'local')); } catch (e) {}
    return all;
  }

  /** 사람이 읽을 한 줄 요약. 목표 등급이 몇 석인지가 핵심이다. */
  function summary() {
    if (!hits.length) return '조회 응답 기록 없음';
    var tl = seatTimeline();
    if (!tl.length) return hits.length + '건 기록 (좌석 수는 아직 못 봄)';
    var last = {};
    tl.forEach(function (r) { last[r.name] = r; });
    return Object.keys(last).map(function (k) {
      return k + ' ' + (last[k].soldout ? '매진' : last[k].seatCount + '석');
    }).join(' · ');
  }

  var CABIN_FAMILY = { '프레스티지': 'KEBONUSPR', '일반석': 'KEBONUSEY',
                       '일등석': 'KEBONUSFC', '프리미엄석': 'KEBONUSPY' };

  /* 고른 등급(예: '프레스티지')이 대한항공 운항편에서 매진인지 서버 응답으로 판정한다.
   *
   * 화면에는 "매진" 글자뿐이라 "아직 안 열림" 과 구분이 안 된다. 서버는 명확하다:
   * soldout:true 는 해당 응답에서 예약 불가라는 뜻이다. 이전 개방/판매는 입증하지 않는다.
   *
   * 코드셰어(에어프랑스 운항 KE5901 등)는 제외한다 - 우리는 대한항공만 탄다.
   * 날짜(mmdd, 예 "08-27")를 주면 그 날 응답만 본다. 낡은 다른 날 응답에 속지 않게.
   *
   * 반환: null(응답 없음) 또는
   *   {answered, listed, soldout, seats, eySeats, keFlights}
   *   listed=대한항공편에 그 등급이 목록에 있었나, soldout=있으면서 전부 매진인가,
   *   eySeats=같은 판단 대상에서 일반석 최대 좌석수(안내 문구용). */
  function keCabin(cabinName, mmdd) {
    var fam = CABIN_FAMILY[cabinName] || cabinName;
    var want = mmdd ? String(mmdd).replace(/[^0-9]/g, '') : '';   // "08-27" -> "0827"
    var res = null;
    /* 가장 최근 응답부터(뒤에서 앞으로) 훑어, 그 날짜를 담은 응답 하나를 쓴다. */
    for (var i = hits.length - 1; i >= 0 && !res; i--) {
      if (!/availab/i.test(hits[i].url)) continue;
      if (hits[i].status !== 200) continue;
      var d; try { d = JSON.parse(hits[i].body); } catch (e) { continue; }
      var bounds = (d && d.upsellBoundAvailList) || [];
      var listed = false, openSeats = 0, soldCount = 0, keCount = 0, ey = 0, dateSeen = false;
      bounds.forEach(function (b) {
        ((b && b.availFlightList) || []).forEach(function (f) {
          var info = (f.flightInfoList && f.flightInfoList[0]) || {};
          var isKE = info.operationCarrierCode === 'KE' && !info.codeShare;
          var md = String(f.departureDate || '').slice(4, 8);
          if (want && md !== want) return;         // 다른 날짜편은 건너뛴다
          dateSeen = true;
          if (!isKE) return;                        // 코드셰어(외항사 운항) 제외
          keCount++;
          ((f.commercialFareFamilyList) || []).forEach(function (c) {
            if (c.fareFamily === fam) {
              listed = true;
              if (c.soldout) soldCount++;
              else openSeats = Math.max(openSeats, parseInt(c.seatCount, 10) || 0);
            }
            if (c.fareFamily === 'KEBONUSEY' && !c.soldout) {
              ey = Math.max(ey, parseInt(c.seatCount, 10) || 0);
            }
          });
        });
      });
      if (!dateSeen && want) continue;              // 이 응답엔 그 날짜가 없다 - 더 옛 응답을 본다
      res = {
        responseAt: hits[i].at,
        answered: true,
        keFlights: keCount,
        listed: listed,
        soldout: listed && openSeats === 0 && soldCount > 0,
        seats: openSeats,
        eySeats: ey
      };
    }
    return res;
  }

  /* 가장 최근 조회 요청을 그대로 한 번 더 쏜다 (읽기 전용 재조회).
   *
   * 왜: 새로고침으로 재조회하면 페이지 로딩·렌더까지 다시 해서 표본 간격이 6초다.
   * 같은 요청만 되쏘면 API 시간(~1.7초)만 들어 1초대 간격이 가능하다.
   * 쏘는 주체가 페이지 자신이라 세션·쿠키·오리진이 원래 요청과 완전히 같다.
   *
   * 응답은 note() 를 다시 타므로 hits 에 쌓이고, keCabin 이 최신 값을 읽는다.
   * 예약이 아니라 조회만 되쏜다 - 상태를 바꾸지 않는다. */
  function reAsk() {
    var src = null;
    for (var i = hits.length - 1; i >= 0; i--) {
      if (/availab/i.test(hits[i].url) && hits[i].req) { src = hits[i]; break; }
    }
    if (!src) return 'no-request';
    try {
      var init = { method: (src.req.method || 'GET').toUpperCase(),
                   credentials: 'include', headers: src.req.headers || {} };
      if (init.method !== 'GET' && init.method !== 'HEAD') init.body = src.req.body;
      W.fetch(src.url, init);   // 응답은 fetch 래퍼가 알아서 기록한다
      return 'sent';
    } catch (e) {
      return 'err:' + String(e).slice(0, 40);
    }
  }

  W.KE_PROBE = {
    hits: function () { return hits; },
    stamp: function () { return stamp; },
    keCabin: keCabin,
    availabilityState: function () { return availability; },
    reAsk: reAsk,
    /* 좌석 조회 응답이 한 번이라도 왔는가.
     *
     * "고른 등급이 없다" 와 "페이지가 아직 안 떴다" 를 가르는 근거다. 화면만 보면
     * 둘 다 '없음' 으로 보여서, 안 떴는데 새로고침 -> 또 안 뜸 -> 무한반복이 된다
     * (실측 2026-08-28). 서버가 답을 준 뒤라면 목록이 비어 있어도 그건 사실이므로
     * 그때는 새로고침해서 다시 보는 것이 맞다. */
    answered: function () {
      for (var i = 0; i < hits.length; i++) {
        if (/availab/i.test(hits[i].url)) return true;
      }
      return false;
    },
    summary: summary,
    seatTimeline: seatTimeline,
    storeHints: storeHints,
    shownDate: shownDate, stripDates: stripDates, latestStripDates: latestStripDates,
    payTypes: payTypes,
    dump: function () {
      /* 원본 JSON 은 길어서 사람이 읽기 어렵다. 알고 싶은 것(등급별 좌석 수가
       * 언제 어떻게 바뀌었나)을 맨 위에 표로 뽑아두고 원본은 그 아래 붙인다. */
      var tl = seatTimeline();
      var head = tl.length
        ? ('== 등급별 좌석 수 ==' + '\n'
           + tl.map(function (r) {
               return new Date(r.at).toISOString().slice(11, 23) + '  ' + r.date + ' ' + r.flight
                    + '  ' + (r.name || r.family) + '  ' + r.seatCount + '석'
                    + (r.soldout ? ' (매진)' : '') + '  ' + r.mileage + '마일';
             }).join('\n'))
        : '== 등급별 좌석 수: 아직 못 봄 ==';
      var pt = payTypes();
      /* 달력 건너뛰기가 가능한지는 조회 조건이 어디 사는가에 달렸다.
       * 저장소에 날짜가 있으면 고쳐 넣고 바로 들어갈 수 있고, 없으면 못 한다. */
      var hints = storeHints();
      var dated = hints.filter(function (h) { return h.date; });
      head += '\n\n== 조회 조건이 어디 있나 (달력 건너뛰기용) ==\n'
            + (hints.length
               ? (dated.length
                  ? dated.map(function (h) {
                      return h.store + '  ' + h.key + '  (' + h.len + '자)  날짜=' + h.date
                           + '\n    …' + h.sample.replace(/[\s]+/g, ' ') + '…';
                    }).join('\n')
                  : '저장소에 ' + hints.length + '개 있지만 날짜를 든 것은 없음'
                    + ' -> 조회 조건은 서버 세션에 있을 가능성이 큼\n  ('
                    + hints.map(function (h) { return h.key; }).slice(0, 30).join(', ') + ')')
               : '저장소가 비어 있음 -> 조회 조건은 서버 세션에 있을 가능성이 큼');
      head += '\n\n== 쓸 수 있는 결제 수단 ==\n'
            + (pt ? pt.join(', ') + (pt.indexOf('NAVERPAY') < 0 ? '  <- 네이버페이 없음' : '')
                  : '아직 못 봄');
      return head + '\n\n== 원본 ==\n\n' + hits.map(function (h) {
        return '### ' + new Date(h.at).toISOString() + '  [' + h.kind + ' ' + h.status + ']'
             + (h.seaty ? '  <- 좌석 수 필드 있음' : '') + '\n' + h.url + '\n' + h.body;
      }).join('\n\n');
    },
    clear: function () { hits = []; stamp++; }
  };
  if (W !== window) { try { window.KE_PROBE = W.KE_PROBE; } catch (e) {} }
})();

} catch (e) {
  console.error('[KE] probe 로드 실패:', e);
}


// ---------- steps ----------
try {
(function () {
  var S = [{"dynamicDate":true,"idPrefix":"dep-fare-","sel":"#dep-fare-5-1","text":"(날짜: 매번 최신 오픈일 자동 감지 - 16 08월 16일 (월) , 성수기 일반석 은 녹화 당시 예시)","tag":"td","url":"/booking/calendar-fare-bonus","selectorOnly":false},{"sel":"#ac0e9a2f7f9ead9dbd368853f47deb65CalendarFareBonusMain > div.payment-widget.bottom-fixed-area:nth-of-type(4) > kds-sticky.ang-sticky > kds-sticky_1.--wds-ui.ui-sticky__host > kds-button.--wds-ui.ui-button__host > kds-button_1.--wds-ui.ui-button__host","text":"검색","tag":"kds-button_1","url":"/booking/calendar-fare-bonus","selectorOnly":false},{"dynamicCabin":true,"sel":"","text":"(좌석: 패널에서 고른 등급을 매번 다시 찾음 - 녹화 당시는 '항공편명 KE901 일반석 52,500 마일')","tag":"label","url":"/booking/select-award-flight/departure","selectorOnly":false},{"ensure":"KRW","sel":"#currencyBtn","text":"통화","tag":"button","url":"/booking/select-award-flight/departure","selectorOnly":false,"optionSel":"#filter-currency > div.filter__contents > div.filter__list > div.selection.filter__item:nth-of-type(2) > label","applySel":"#filter-currency > div.filter__contents > button.filter__apply:nth-of-type(1)","applyText":"적용","restartFrom":0},{"sel":"#payment-widget > kds-sticky_1.--wds-ui.ui-sticky__host > kds-button > kds-button_1.--wds-ui.ui-button__host","text":"다음","requireSelectedCabin":true,"tag":"kds-button_1","url":"/booking/select-award-flight/departure","selectorOnly":false},{"sel":"#submit-passenger-ADT-0","text":"확인","tag":"button","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#submit-contact","text":"확인","tag":"button","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btn-resv-agree-1","text":"동의","tag":"button","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btnScrollDown > kds-button_1.--wds-ui.ui-button__host","text":"아래로 스크롤","tag":"kds-button_1","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btnConfirm > kds-button_1.--wds-ui.ui-button__host","text":"확인","tag":"kds-button_1","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btn-resv-agree-3","text":"동의","tag":"button","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btnScrollDown > kds-button_1.--wds-ui.ui-button__host","text":"아래로 스크롤","tag":"kds-button_1","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btnConfirm > kds-button_1.--wds-ui.ui-button__host","text":"확인","tag":"kds-button_1","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"#btnAwardUseMileageApply","text":"적용","tag":"button","url":"/payment/gate/RT/NR","selectorOnly":false},{"sel":"ke-payment-interface-cont._folders.ng-star-inserted > ke-payment-interface-pres.ng-star-inserted > div.bundles.-bordered > div.-lined.ng-star-inserted:nth-of-type(2) > div.payment-method.ng-star-inserted:nth-of-type(1) > div.payment-method__item.ng-star-inserted:nth-of-type(3) > label.ng-star-inserted","text":"Npay","tag":"label","url":"/payment/gate/RT/NR","selectorOnly":false,"alt":[{"sel":"","text":"한국발행 신용/체크카드","selectorOnly":false}]},{"ensure":"현대카드","onlyIfPrev":"한국발행","sel":"#sel-korCardCompany","text":"한국발행 신용/체크카드 종류","tag":"select","url":"/payment/gate/RT/NR","selectorOnly":true},{"sel":"#btn-payment","text":"결제하기 새 창 열림","tag":"button","url":"/payment/gate/RT/NR","selectorOnly":false}];
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  try { W.KE_STEPS_BAKED = S; } catch (e) {}
  if (W !== window) { try { window.KE_STEPS_BAKED = S; } catch (e) {} }
  if (S.length) console.log('[KE] 내장 단계 ' + S.length + '개 (2026-08-21 08:57:27)');
})();
} catch (e) {
  console.error('[KE] steps 로드 실패:', e);
}


// ---------- recorder ----------
try {
/* ============================================================
 * recorder.js  --  예매 단계 녹화 / 재생
 *
 * 왜 필요한가
 *   실제 예매는 "확인 버튼 아무거나 누르기" 가 아니라 정해진 순서다:
 *     새로고침 -> 새 날짜 클릭 -> 검색 -> 좌석 -> 다음 -> 승객확인
 *     -> 위험품 팝업 아래로스크롤 x2 -> 확인 -> 동의(안 켜진 것만) -> 결제수단
 *   라벨 추측으로는 (a) 날짜처럼 매일 바뀌는 것 (b) 이미 켜진 동의를 다시 눌러
 *   꺼버리는 사고 를 막을 수 없다. 손으로 한 번 한 걸 그대로 재생하는 게 정확하다.
 *
 * 페이지 이동
 *   단계 중간에 페이지가 바뀌면 JS 상태가 날아간다. 진행 위치를 localStorage 에
 *   두고 새 문서에서 자동으로 이어서 재생한다.
 *
 * 단계를 건너뛰지 않는다
 *   예전에는 "현재 단계를 못 찾으면 뒤쪽 단계가 눌릴 만한지 보고 건너뛰는" 기능이
 *   있었다. 라벨 추측 클릭 엔진이 같은 버튼을 먼저 눌러버리던 시절의 보정책이었는데,
 *   그 엔진을 걷어낸 뒤로는 오판만 낳았다: #btnConfirm 처럼 모달이 닫혀 있어도 DOM 에
 *   남아 있는 요소를 보고 "동의는 이미 지나갔다" 고 판단해 동의 두 개를 통째로
 *   건너뛰었다(실측). 못 찾으면 기다렸다가 직전 단계를 다시 누르고, 그래도 안 되면
 *   멈춰서 사람을 부른다. 조용히 건너뛰는 것보다 멈추는 게 낫다.
 *
 * 콘솔
 *   KE_REC.record()  녹화 시작 / KE_REC.stop() 중지
 *   KE_REC.play()    재생      / KE_REC.pause() 중지
 *   KE_REC.list()    단계 목록 / KE_REC.clear() 삭제
 * ============================================================ */
(function () {
  'use strict';
  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  if (W.KE_REC || window.KE_REC) return;

  var U = W.KE_UTIL || window.KE_UTIL;
  // 탭마다 따로 저장한다 (노선별 탭을 동시에 돌릴 때 서로 덮어쓰지 않게)
  var LS = U.tabKey('ke_award_steps_v1');

  /* 단계가 "어느 화면의 것인가" 를 나타내는 키. 녹화할 때와 재생 중 비교할 때가
   * 반드시 같은 방식이어야 한다.
   * (location.pathname 에는 쿼리스트링이 안 들어간다. 녹화는 search 까지 넣고
   *  비교는 pathname 만 보면, 쿼리로 화면을 구분하는 사이트에서는 영원히 일치하지
   *  않아 '페이지 이동 대기' 로 멈춘다) */
  function hereUrl() { return location.pathname + location.search; }

  /* 이 라벨이 걸리면 재생을 멈추고 사람에게 넘긴다.
   * 마일리지와 현금이 실제로 빠져나가는 지점이라 자동으로 넘기지 않는다. */
  var PAY = /결제하기|결제및발권|발권하기|구매하기|purchase|paynow/;

  // "아래로 스크롤" 계열 단계. 클릭만으로는 불안해서 스크롤을 직접 한 번 더 밀어준다.
  var SCROLLY = /아래로|스크롤|scroll/i;

  var S = {
    steps: [],
    recording: false,
    playing: false,
    idx: 0,
    playAfterReload: false, // 새로고침이 끝난 뒤에 재생을 시작하라는 예약 (armForReload)
    playFrom: 0,          // 그 재생을 몇 번째 단계부터 시작할지 (달력 건너뛰기)
    startedAt: 0,         // 발사 시각(ms). 단계별/총 소요시간 표시용
    /* 끝난 시각. 이게 없으면 재생이 끝난 뒤에도 소요시간이 계속 올라가서, 33초에
     * 끝난 실행이 한 시간 뒤에 6346초로 보인다(실측 2026-08-28). 멈춘 시계여야
     * "이번에 몇 초 걸렸나" 를 나중에도 읽을 수 있다. */
    endedAt: 0,
    /* 재생이 끝났지만 사람이 봐야 하는 상태인가.
     * 예전에는 skipped/skippedList 로 "건너뛴 단계"를 셌는데, 건너뛰기 기능을
     * 없애면서 아무도 값을 올리지 않는 죽은 장치가 됐다. 그런데 hud 의 완료 판정은
     * 그 값을 계속 보고 있어서 "안전장치가 있는 것처럼 보이는" 상태였다.
     * 실제로 문제가 생긴 지점에서만 켜는 플래그로 바꾼다. */
    problem: false,
    lastOpen: null,       // 사이트가 window.open 을 부른 결과 {at, ok}. 결제창이
                          // 실제로 떴는지 확인하는 유일한 방법이다
    message: '',          // 패널 상태줄. 여기 선언이 없으면 load() 가 걸러내서
                          // 마지막 단계가 페이지를 이동시킨 경우 왜 멈췄는지가 사라진다
    expectDate: '',       // 목표 날짜(예: "08-27"). 넣으면 자동 감지한 최신 오픈일이
                          // 이것과 다를 때 클릭하지 않고 멈춘다 (엉뚱한 날 예매 방지)
    cabin: '일반석',       // 좌석 등급. 연습은 '일반석', 실전은 '프레스티지'
    /* 결제하기까지 자동으로 누른다 (사용자 요청으로 기본 켜짐).
     * 결제창에서 다시 본인 인증이 필요하므로 여기서 바로 돈이 빠지지는 않는다.
     * 패널 체크박스로 끌 수 있다.
     *
     * 주의: 실측에서 이 단계가 실행됐는데도 결제창이 뜨지 않은 적이 있다.
     * 브라우저가 스크립트로 만든 클릭(isTrusted=false)에는 사용자 조작 권한을
     * 주지 않아 새 창을 막는 경우가 있는데, 그러면 "눌렀다" 는 로그만 남는다.
     * 그래서 재생이 끝나면 결제창이 실제로 떴는지 확인하라고 알린다. */
    allowPay: false,
    times: [],            // 단계별 소요시간 [{n, label, ms}]. 어디서 시간을 쓰는지
                          // 추측하지 않고 재기 위한 것 - 페이지 이동을 넘어 유지된다
    stepStartedAt: 0,     // 지금 단계를 시작한 시각
    openWaitSince: 0,     // 목표 날짜가 열리기를 기다리기 시작한 시각(페이지 이동을 넘어 유지)
    soldOutSince: 0,      // 고른 등급이 '매진 확정' 으로 처음 보인 시각(페이지 이동을 넘어 유지)
    openReloads: 0,       // 날짜/좌석을 기다리며 새로고침한 횟수 (발사가 일렀는지 계측)
    navigation: [],       // 경로·단계만 저장. 쿼리/계정/요청 본문은 기록하지 않음
    openRetryMs: 1200,    // 목표 날짜가 없을 때 새로고침 간격 (서버 부담 하한)
    openWaitMaxMs: 180000,// 이만큼 기다려도 안 열리면 사람을 부른다
    /* 좌석이 매진(soldout:true)으로 확정돼도 몇 백ms 늦게 풀릴 수 있어 이만큼은
     * 다시 불러본다. 그동안 계속 매진이면 멈춘다. 예전엔 openWaitMaxMs(180초)를
     * 다 채워 3분을 헛돌았다 - 매진이면 사람이 바로 다음 수를 둬야 한다. */
    soldOutGraceMs: 4000,
    stepTimeoutMs: 20000, // 한 단계에서 요소를 못 찾고 버티는 한계
    optionalMs: 400,      // optional 단계 대기 (주 수단은 onlyIfPrev - 대기가 없다)
    gapMs: 80,            // 클릭 사이 최소 간격
    settleMs: 250,        // 이만큼 화면이 잠잠해야 다음 단계를 누른다
    /* 계속 바뀌기만 하면 이 시간 뒤에는 그냥 누른다.
     *
     * 2500 이었는데 실측(2026-08-28)에서 이 상한에 매번 걸렸다 - 대한항공 화면은
     * 잠잠해지는 순간이 아예 없어서, 기다린 값을 한 번도 못 건지고 매 단계 2.5초를
     * 그냥 버렸다. 27초 중 '화면 안정' 이 6~8초였다.
     *
     * 게다가 2.5초를 다 기다리고도 8단계(동의)에서는 클릭이 씹혀 직전 단계를 4번
     * 다시 눌렀다. 기다린 것이 헛클릭을 막지도 못했다는 뜻이다.
     *
     * 줄여도 '무엇을 누를까' 는 그대로다(셀렉터·라벨·모달 가림 확인). 바뀌는 것은
     * '언제 누를까' 뿐이고, 일찍 눌러 씹히면 재시도가 1.2초 뒤에 다시 누른다.
     * 최악이 재시도 한 번, 대개는 1.3초를 번다. */
    maxSettleMs: 1200,
    retryClickMs: 1200,   // 막혔을 때 직전 단계를 다시 눌러보는 간격
    /* 달력 건너뛰기(바로 시작)용. 조회 페이지를 지날 때마다 그 주소를 붙잡아둔다.
     * 주소 형식을 추측하지 않고 실제로 지나간 것을 쓰기 위한 것이다.
     * deepLinkDate 는 붙잡을 당시의 '가는 날' - 다음에 목표 날짜가 바뀌면
     * 주소의 어느 자리를 고쳐야 하는지 고르는 기준이 된다. */
    deepLink: '',
    baseLink: '',         // 달력 페이지 주소. 바로 시작이 튕겼을 때 되돌아갈 곳
    deepLinkDate: '',
    pickedDate: '',       // 달력에서 방금 고른 날 (MM-DD). deepLinkDate 의 재료
    /* 조회 화면에서 시작할 때, 화면 날짜를 목표 날짜로 바꿔야 하면 여기에 담긴다.
     * 좌석 단계로 넘어가기 전에 이걸 먼저 해결한다. 09:00 에 새로 열리는 날짜는
     * 미리 맞춰둘 수 없으므로(그 시각에야 예약 가능 창에 들어온다) 이 과정이 있어야
     * 조회 화면 모드가 09:00 경쟁에 쓸 수 있게 된다. */
    fixDate: '',          // 맞춰야 할 목표 날짜 (MM-DD)
    fixPhase: 0,          // 0=날짜 띠에서 누르기, 2=서버 응답으로 확인
    fixSince: 0,
    fixClickAt: 0,        // 날짜를 누른 시각. 이후에 온 응답만 근거로 삼는다
    fixOpens: 0,          // 날짜를 몇 번 눌러봤나 (멈출 때 이유에 쓴다)
    byCause: {},          // 원인별 누적 시간. '어디를 손대야 하는가' 를 바로 보여준다
    source: 'baked',      // 지금 단계가 어디서 왔는지: 'baked'(steps.json) | 'local'(직접 녹화)
    /* 녹화할 때의 창 너비. 대한항공 화면은 반응형이라 창이 좁아지면 모바일
     * 레이아웃으로 바뀌고, 그러면 셀렉터도 라벨도 달라진다 - 위험품 안내 모달은
     * 넓은 화면에서는 [아래로 스크롤] 버튼이 있는데 좁은 화면에서는 아예 없다.
     * 실측(2026-08-28): 창을 줄여놓고 돌렸더니 12단계에서 그 버튼을 못 찾고 멈췄다.
     * 녹화 당시 너비를 남겨두고, 지금 그보다 많이 좁으면 미리 알린다. */
    recordedWidth: 0,
    bakedSig: '',         // 적용한 내장본의 지문. 바뀌면 내장본으로 덮는다
    tuneSig: ''           // 적용한 타이밍 기본값의 지문. 바뀌면 새 값으로 덮는다
  };

  /* 타이밍 값들. 사람이 패널에서 고치는 설정이 아니라 코드가 정하는 상수다.
   *
   * 그런데 상태와 함께 localStorage 에 저장돼서, 코드에서 기본값을 고쳐도 이미
   * 쓰던 브라우저에는 영영 반영되지 않았다 - 저장된 옛날 값이 새 기본값을 덮는다.
   * 실측(2026-08-28): maxSettleMs 를 2500 -> 1200 으로 줄였는데 화면에는 여전히
   * '화면 안정 2.8s' 가 찍혔다. 바뀐 줄 알고 판단하면 엉뚱한 결론에 이른다.
   *
   * 기본값이 바뀌면 지문(tuneSig)이 달라지고, 그때 저장된 값을 새 기본값으로
   * 덮는다. 지문이 같으면 손대지 않으므로, 시험하려고 잠깐 바꿔둔 값은 유지된다. */
  var TUNING = ['stepTimeoutMs', 'optionalMs', 'gapMs', 'settleMs', 'maxSettleMs',
                'retryClickMs', 'openRetryMs', 'openWaitMaxMs', 'soldOutGraceMs'];

  function tuneSig() {
    var out = '';
    for (var i = 0; i < TUNING.length; i++) out += TUNING[i] + '=' + S[TUNING[i]] + ';';
    return out;
  }
  var TUNE_SIG = tuneSig();     // 코드가 정한 값들의 지문 (load 전에 잡는다)

  function load() {
    try {
      var raw = localStorage.getItem(LS);
      if (raw) {
        var d = JSON.parse(raw);
        var keep = {};
        for (var i = 0; i < TUNING.length; i++) keep[TUNING[i]] = S[TUNING[i]];
        for (var k in d) if (k in S) S[k] = d[k];
        if (d.tuneSig !== TUNE_SIG) {
          for (var j = 0; j < TUNING.length; j++) S[TUNING[j]] = keep[TUNING[j]];
          /* 여기서 log() 를 쓰면 안 된다 - listeners 가 아직 초기화 전이라
           * emit() 에서 터지고 모듈 전체가 죽는다(패널이 통째로 안 뜬다). */
          console.log('%c[KE_REC] 타이밍 기본값이 바뀌어 새 값으로 맞췄습니다: '
                      + TUNE_SIG, 'color:#a0f;font-weight:bold');
        }
      }
    } catch (e) {}
    S.tuneSig = TUNE_SIG;
  }
  function save() {
    try { localStorage.setItem(LS, JSON.stringify(S)); } catch (e) {}
  }
  load();

  /* 사이트는 결제창을 window.open 으로 띄운다. 스크립트가 만든 클릭에는 브라우저가
   * 사용자 조작 권한을 주지 않아 팝업이 차단될 수 있는데, 그러면 "눌렀다" 는 기록만
   * 남고 창은 안 뜬다. 열렸는지 알 방법이 없으므로 open 을 감싸서 결과를 남긴다. */
  (function wrapOpen() {
    try {
      var orig = W.open;
      if (typeof orig !== 'function' || orig.__keWrapped) return;
      var wrapped = function () {
        var w = orig.apply(this, arguments);
        try { S.lastOpen = { at: Date.now(), ok: !!w }; save(); } catch (e) {}
        return w;
      };
      wrapped.__keWrapped = true;
      W.open = wrapped;
    } catch (e) {}
  })();

  /* 빌드 시 steps.json 에서 구워 넣은 기본 단계.
   *
   * 우선순위: 검토를 거쳐 git 에 올린 steps.json 이 브라우저에 남은 녹화보다 세다.
   * 예전에는 반대였는데, 새 스크립트를 붙여넣어도 브라우저에 남아있던 옛날 녹화가
   * 계속 이겨서 정리 전 단계로 돌아가는 사고가 났다.
   *
   * 그렇다고 매번 덮으면 방금 녹화한 게 새로고침마다 날아간다. 그래서 내장본의
   * 지문을 같이 저장해두고, 지문이 바뀔 때(=새 빌드를 붙여넣었을 때)만 덮는다.
   * 지문이 같으면 이 브라우저에서 녹화/편집한 내용을 그대로 둔다. */
  function baked() { return (W.KE_STEPS_BAKED || window.KE_STEPS_BAKED || []); }

  function sigOf(steps) {
    var s = JSON.stringify(steps || []);
    var h = 5381;
    for (var i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
    return (steps || []).length + ':' + (h >>> 0).toString(36);
  }

  function adoptBaked(why) {
    S.steps = JSON.parse(JSON.stringify(baked()));
    S.bakedSig = sigOf(baked());
    S.source = 'baked';
    S.idx = 0;
    save();
    if (why) console.log('%c[KE_REC] 내장 단계 ' + S.steps.length + '개 적용 (' + why + ')',
                         'color:#a0f;font-weight:bold');
  }

  if (baked().length && S.bakedSig !== sigOf(baked())) {
    adoptBaked(S.steps.length ? '스크립트가 갱신되어 이전 녹화를 대체함' : '최초 적용');
  }

  var listeners = [];
  function emit() {
    for (var i = 0; i < listeners.length; i++) {
      try { listeners[i](S); } catch (e) {}
    }
  }
  /* 단계가 넘어갈 때마다 얼마나 걸렸는지 남긴다.
   * "30초 걸리는데 줄일 수 있나" 는 어디서 쓰는지 알아야 답할 수 있다. */
  /* 한 단계가 오래 걸렸을 때 "페이지가 느린 것" 과 "우리가 헛기다린 것" 은 대응이
   * 정반대다. 실측에서 8단계(동의)가 6.9초였는데 어느 쪽인지 구분할 수가 없었다.
   * 매 tick 마다 지금 무엇 때문에 못 누르는지를 적어 시간을 나눠 담는다.
   *
   * 화면 안정 = 앞 단계 클릭 뒤 화면이 잠잠해지기를 기다림 (settleMs/maxSettleMs)
   * 요소 없음 = 누를 것이 아직 화면에 안 나타남 (페이지가 느린 쪽)
   * 가림     = 나타났지만 무언가에 덮여 있음 */
  var phaseMs = {}, lastTickAt = 0;
  function phase(name, now) {
    /* 탭이 숨겨져 타이머가 늦춰지면 한 tick 이 몇 초로 벌어진다. 그걸 그대로 담으면
     * 원인 분석이 아니라 스로틀링 측정이 된다. 한 tick 몫만 담는다. */
    var d = lastTickAt ? now - lastTickAt : 0;
    if (d > 0 && d < 1000) phaseMs[name] = (phaseMs[name] || 0) + d;
    lastTickAt = now;
  }

  function markStep(n, label) {
    var t = Date.now();
    if (S.stepStartedAt) {
      if (!S.times) S.times = [];
      /* 원인별 합계도 같이 남긴다. 느린 단계 3개만 보면 "우리가 기다린 시간" 과
       * "페이지가 느린 시간" 이 전체에서 각각 얼마인지 알 수 없어서, 어디를 손대야
       * 하는지 매번 숫자를 다시 물어봐야 했다. */
      if (!S.byCause) S.byCause = {};
      for (var pk in phaseMs) S.byCause[pk] = (S.byCause[pk] || 0) + phaseMs[pk];
      var why = Object.keys(phaseMs)
        .filter(function (k) { return phaseMs[k] >= 250; })
        .sort(function (a, b) { return phaseMs[b] - phaseMs[a]; })
        .map(function (k) { return k + ' ' + (phaseMs[k] / 1000).toFixed(1) + 's'; });
      S.times.push({ n: n, label: String(label || '').slice(0, 22),
                     ms: t - S.stepStartedAt, why: why.join(', ') });
    }
    /* 다음 단계는 기준점을 새로 잡는다. 안 그러면 직전 단계의 마지막 tick 부터
     * 흐른 시간이 새 단계 몫으로 넘어온다. */
    phaseMs = {};
    lastTickAt = 0;
    S.stepStartedAt = t;
  }

  function timeReport() {
    var a = (S.times || []).slice();
    if (!a.length) return '';
    a.sort(function (x, y) { return y.ms - x.ms; });
    var slow = '  느린 단계: ' + a.slice(0, 3).map(function (x) {
      return x.n + '.' + x.label + ' ' + (x.ms / 1000).toFixed(1) + 's'
           + (x.why ? ' (' + x.why + ')' : '');
    }).join(', ');
    /* 전체 합계. 느린 단계 3개만으로는 "우리가 기다린 시간" 이 전체에서 얼마인지
     * 알 수 없다. 줄일 여지가 있는 쪽이 어디인지 이 줄 하나로 보인다. */
    var c = S.byCause || {}, keys = Object.keys(c).sort(function (x, y) { return c[y] - c[x]; });
    /* 발사가 일러서(선발사 과다) 날짜/좌석이 아직 없어 다시 불러왔다면 그 횟수를 붙인다.
     * 한 번이 약 3~4초라, 이게 0 이 아니면 선발사를 줄여야 한다는 직접적 신호다. */
    var re = S.openReloads ? '  ·  재고침 ' + S.openReloads + '회(발사 이름)' : '';
    if (!keys.length) return slow + re;
    return slow + '  |  전체: ' + keys.map(function (k) {
      return k + ' ' + (c[k] / 1000).toFixed(1) + 's';
    }).join(', ') + re;
  }

  function log(msg) {
    console.log('%c[KE_REC] ' + msg, 'color:#a0f;font-weight:bold');
    S.message = msg;
    emit();
  }

  /* 아래 블록은 log()/emit() 를 쓴다. 원래 파일 앞쪽에 있었는데, 그 자리에서는
   * listeners 가 아직 초기화 전(undefined)이라 log() 한 번에 모듈 전체가 죽었다.
   * (증상: 패널이 통째로 안 뜸) 로그를 쓰는 시작 코드는 여기 아래에 둔다. */
  /* armForReload() 로 예약해둔 재생을 여기서 시작한다.
   * 이 코드는 새 문서가 뜰 때마다 한 번 실행되므로, "새로고침이 끝난 뒤" 라는
   * 시점이 정확히 보장된다. 낡은 화면에서 1단계를 눌러버리고 그 결과가 새로고침에
   * 날아가는 사고를 막기 위한 것이다. */
  if (S.playAfterReload) {
    S.playAfterReload = false;
    S.playing = true;
    S.idx = S.playFrom || 0;
    S.playFrom = 0;
    /* 달력을 건너뛰고 조회 페이지로 바로 들어갔는데 엉뚱한 데 떨어졌다면(세션 만료,
     * 주소 형식 변경 등) 그 자리에서 단계를 눌러선 안 된다. 붙잡아둔 달력 주소로
     * 돌아가 처음부터 한다 - 오늘 실측만큼 걸릴 뿐, 놓치지는 않는다. */
    if (S.idx > 0 && !U.onDeparture()) {
      var back = S.baseLink;
      S.idx = 0;
      log('바로 시작이 조회 페이지로 가지 못했습니다 (' + location.pathname
          + ') - 달력으로 돌아가 처음부터 합니다');
      if (back) {
        /* 여기서 return 하면 이 파일 끝의 W.KE_REC 대입까지 건너뛰어 패널이 통째로
         * 죽는다. 재생만 멈추고 이동은 예약해둔다. */
        S.playAfterReload = true;
        S.playing = false;
        setTimeout(function () { location.href = back; }, 0);
      } else {
        S.problem = true;
        S.message = '바로 시작이 실패했고 돌아갈 달력 주소도 없습니다 - 직접 조회하세요';
      }
    }
    save();
  }

  /* 조회 페이지에 도착할 때마다 그 주소를 붙잡아둔다. 다음 번 '바로 시작' 이 이걸
   * 쓴다. 형식을 추측하지 않고 실제로 지나간 주소를 그대로 재사용한다. */
  /* 달력 페이지도 붙잡아둔다. 바로 시작이 튕겼을 때 여기로 되돌아와 처음부터 한다.
   * 되돌아갈 곳이 없으면 튕긴 순간 그냥 멈추는 수밖에 없다. */
  try {
    if (S.steps.length && S.steps[0].url
        && hereUrl().indexOf(S.steps[0].url) >= 0
        && location.href !== S.baseLink) {
      S.baseLink = location.href;
      save();
    }
  } catch (e) {}

  try {
    if (U.onDeparture() && location.search) {
      var pd = S.pickedDate || S.expectDate || '';
      if (location.href !== S.deepLink || (pd && pd !== S.deepLinkDate)) {
        S.deepLink = location.href;
        S.deepLinkDate = pd;
        save();
      }
    }
  } catch (e) {}


  // ---- 녹화 --------------------------------------------------------------
  function onClick(ev) {
    if (!S.recording) return;
    var el = ev.target;
    if (el.closest && el.closest('#ke-hud, #ke-editor, #ke-export')) return;  // 우리 UI 는 기록 안 함
    var t = el.closest ? (el.closest(U.CLICKABLE) || el) : el;

    var step = {
      sel: U.cssPath(t),
      text: U.label(t),
      tag: t.tagName.toLowerCase(),
      url: hereUrl(),
      // 날짜처럼 매일 바뀌는 라벨은 텍스트 폴백이 오히려 해롭다 -> 사용자가 끌 수 있게
      selectorOnly: false
    };
    S.steps.push(step);
    save();
    log('녹화 ' + S.steps.length + ': ' + (step.text || step.sel).slice(0, 30));
  }
  document.addEventListener('click', onClick, true);

  function record() {
    try { S.recordedWidth = window.innerWidth || 0; } catch (e) {}
    S.source = 'local';       // 이제부터는 이 브라우저에서 만든 것
    S.steps = [];
    S.recording = true;
    S.playing = false;
    S.idx = 0;
    save();
    log('녹화 시작 - 평소처럼 끝까지 진행하세요 (결제 직전까지)');
  }
  function stopRec() {
    S.recording = false;
    save();
    log('녹화 종료 - ' + S.steps.length + '단계');
  }

  // ---- 재생 --------------------------------------------------------------
  var waitingSince = 0;

  /* 기다린 시간을 '화면이 보이는 동안' 으로만 센다.
   *
   * 크롬은 가려지거나 최소화된 창의 타이머를 늦추는데, 늦춰지는 것은 우리 tick 만이
   * 아니라 그 페이지 자신이다. 모달이 뜨는 데 20초가 넘게 걸리기도 한다. 그걸 벽시계로
   * 재서 "요소를 못 찾음" 으로 끊으면, 화면을 다시 보는 순간 멀쩡히 있는 버튼을 두고
   * 이미 멈춰 있다. 실측(2026-08-28): 창을 작게/가려둔 채 두면 12단계(아래로 스크롤)
   * 에서 그렇게 멈췄고, 최대화하면 잘 됐다.
   *
   * 그래서 가려져 있던 시간은 인내심에서 빼고, 대신 얼마나 뺐는지 알려준다. */
  var waitedMs = 0, hiddenMs = 0, lastWaitAt = 0;

  function beganWaiting(now) {
    if (!waitingSince) { waitingSince = now; waitedMs = 0; hiddenMs = 0; lastWaitAt = now; return; }
    var d = now - lastWaitAt;
    lastWaitAt = now;
    if (d <= 0) return;
    /* 스로틀링으로 크게 벌어진 간격은 '버리지 말고 상한을 씌워' 담는다.
     *
     * 예전에는 2초를 넘으면 통째로 버렸다. 그런데 크롬이 가려진 창을 분 단위로
     * 늦추면 매 tick 이 2초를 넘어 전부 버려진다 - waitedMs 가 영영 안 쌓여
     * 제한시간에 걸리지 않고, pause/finish 가 안 불려 소리도 안 울린다.
     * 창을 최소화하면 매크로가 '재생 중' 인 채 소리 없이 영원히 멈춘다.
     * 09:00 에 이러면 화면을 볼 때까지 아무도 모른다. */
    if (d > 2000) d = 2000;
    if (document.hidden) hiddenMs += d; else waitedMs += d;
  }
  function stopWaiting() { waitingSince = 0; waitedMs = 0; hiddenMs = 0; lastWaitAt = 0; }
  function tooLong(limit) { return waitedMs > limit; }
  function hiddenNote() {
    var n = hiddenMs > 1000
      ? ' (창이 가려져 있던 ' + Math.round(hiddenMs / 1000) + '초는 빼고 셌습니다)'
      : '';
    /* 창이 좁으면 사이트가 모바일 화면으로 바뀌어 셀렉터도 라벨도 달라진다.
     * '못 찾음' 의 가장 흔한 원인이므로 그 자리에서 짚어준다. */
    try {
      var w = window.innerWidth || 0, need = S.recordedWidth || 1200;
      if (w && w < need * 0.85) {
        n += ' — 창이 좁습니다(' + w + 'px). 모바일 화면으로 바뀌면 단계를 못 찾습니다'
           + ' - 창을 ' + need + 'px 이상으로 넓히고 다시 하세요';
      }
    } catch (e) {}
    return n;
  }
  var lastClickAt = 0;

  /* 앞 단계의 결과가 화면에 반영되기 전에 다음 단계를 누르면 클릭이 그냥 무시된다.
   * 실측에서 승객정보 확인(6.12s) 0.2초 뒤에 연락처 확인(6.32s)을 눌렀고, 그 클릭이
   * 먹지 않아 이후 단계가 전부 막혔다. 사람이 녹화할 때는 이 사이가 몇 초였다.
   * 그래서 "화면이 잠잠해질 때까지" 기다렸다가 다음을 누른다.
   * 우리 패널은 시계를 50ms 마다 다시 그리므로 그 변화는 세지 않는다. */
  var lastMutAt = 0;
  /* 다음 단계 요소가 앞 클릭 직전에 이미 있었는가. 없다가 나타났으면 앞 클릭이
   * 먹었다는 증거가 되어 '화면 안정' 대기를 건너뛴다. 처음에는 알 수 없으므로
   * 안전한 쪽(있었다 = 기다린다)으로 둔다. */
  var nextWasPresent = true;
  var retries = 0;
  var blockedEl = null;   // 찾았지만 무언가에 가려 못 누르는 요소
  var lastLabel = '';     // 직전 단계에서 실제로 누른 요소의 라벨 (onlyIfPrev 판단용)
  var lastOpenReloadAt = 0;  // 목표 날짜를 기다리며 마지막으로 새로고침한 시각
  var scrollClicks = 0;   // 이번 스크롤 단계에서 몇 번 눌렀는지
  var ensurePhase = 0;    // ensure 진행 단계: 0 시작 / 1 목록 열림 / 2 적용 대기
  var OURS = '#ke-hud, #ke-editor, #ke-export';
  new MutationObserver(function (muts) {
    for (var i = 0; i < muts.length; i++) {
      var t = muts[i].target;
      var el = t && t.nodeType === 1 ? t : (t && t.parentElement);
      if (el && el.closest && el.closest(OURS)) continue;
      lastMutAt = Date.now();
      return;
    }
  }).observe(document, { childList: true, subtree: true, attributes: true });

  /* 다시 눌러도 되는 단계인가.
   * 확인/다음/검색 같은 제출 버튼은 두 번 눌러도 결과가 같지만, 동의/체크는 토글이라
   * 다시 누르면 꺼진다. steps.json 에서 단계별로 noRetry 로 못박을 수도 있다. */
  var TOGGLEY = /동의|체크|선택|agree|check/i;
  function retryable(step) {
    if (!step || step.noRetry || isPay(step)) return false;
    return !TOGGLEY.test(step.text || '');
  }

  /* 더 눌러봐야 소용없는 안내. 좌석/운임이 이미 남의 것이 됐거나 세션이 끊긴 경우다.
   *
   * 실측(2026-08-29 09:00, 프레스티지 1석): 7단계까지 17.1초에 갔는데 그 사이
   * 좌석이 나가서 "운임 및 좌석 상황이 변하여 예약을 완료할 수 없습니다" 팝업이 떴다.
   * 도구는 그걸 못 알아보고 8단계(동의)를 찾으며 직전 단계를 16번 다시 눌렀다 -
   * 21.6초를 버렸고, 그나마 누른 '확인' 은 그 에러 팝업의 확인 버튼이었다.
   * 이런 문구가 화면에 보이면 즉시 멈추고 사람을 부른다. */
  var FATAL = /운임\s*및\s*좌석\s*상황이\s*변하여|예약을\s*완료할\s*수\s*없습니다|좌석이\s*모두\s*예약|세션이\s*(종료|만료)/;

  /** 화면에 실제로 보이는 글에서만 찾는다 (innerText 는 숨겨진 것을 빼고 준다). */
  function fatalNotice() {
    var t;
    try { t = document.body ? document.body.innerText : ''; } catch (e) { return null; }
    var m = t && t.match(FATAL);
    return m ? m[0].replace(/\s+/g, ' ') : null;
  }

  /* 막혀 있으면 직전 단계를 다시 눌러본다.
   * 사이트가 앞 단계를 처리하는 중에 눌러 클릭이 그냥 무시되는 일이 실제로 있었다
   * (연락처 확인을 눌렀는데 화면이 그대로였고 다음 단계가 나타나지 않음).
   * 왜 무시됐는지는 밖에서 알 수 없으므로, 원인을 따지지 않고 다시 누른다. */
  function retryPrevClick(now) {
    // 횟수로 끊지 않는다. 단계 제한시간(stepTimeoutMs)까지 계속 눌러보고,
    // 그래도 안 되면 아래에서 멈추면서 사람을 부른다.
    if (S.idx === 0) return false;
    if (blockedEl) return false;   // 가려서 못 누르는 거면 다시 눌러봤자다
    if (now - lastClickAt < S.retryClickMs) return false;
    /* 좌석이 이미 나갔는데 다시 누르면 에러 팝업의 확인만 계속 누르게 된다.
     * 여기서 끊어야 21초를 버리지 않고 사람이 바로 다음 수를 둘 수 있다. */
    var bad = fatalNotice();
    if (bad) { finish('사이트 안내: "' + bad.slice(0, 60) + '" - 더 진행할 수 없습니다', true); return false; }
    var prev = S.steps[S.idx - 1];
    if (!retryable(prev)) return false;
    var el = locate(prev);
    if (!el) return false;
    retries++;
    lastClickAt = now;
    U.fireClick(el);
    log('단계 ' + (S.idx + 1) + ' 가 안 나타나 직전 단계를 다시 누름 (' + retries + '회째): '
        + String(prev.text || prev.sel).slice(0, 24));
    return true;
  }

  function isPay(step) {
    return PAY.test((step.text || '').replace(/[^0-9a-z가-힣]/gi, '').toLowerCase());
  }

  /** 발사(재생 시작)부터 지금까지 몇 초. 새로고침을 건너도 이어지도록 저장해둔다. */
  function elapsed() {
    if (!S.startedAt) return 0;
    /* 재생이 끝났으면 그때 시각으로 고정한다 - 계속 올라가는 숫자는 아무것도
     * 알려주지 않는다. */
    var end = (!S.playing && S.endedAt) ? S.endedAt : Date.now();
    return (end - S.startedAt) / 1000;
  }
  function secs(v) { return v.toFixed(2) + 's'; }

  /* 한 번의 실행을 시작할 때 지워야 하는 것들.
   *
   * play() 와 armForReload() 가 각자 조금씩 다르게 지우다가 같은 사고가 세 번 났다:
   * ▶ 재생 은 되는데 ▶ 대기 시작 만 안 되는 것이다. 마지막이 openWaitSince 였는데,
   * 지난 실행의 값이 남아 시작하자마자 "180초 동안 안 열렸습니다" 로 끝났다
   * (실제로는 0.64초. 실측 2026-08-28).
   *
   * 지우는 곳을 하나로 둔다. 새 상태를 추가할 때도 여기만 고치면 둘이 같이 간다. */
  function resetRunState() {
    S.openWaitSince = 0;
    S.soldOutSince = 0;
    S.openReloads = 0;
    S.blocks = [];        // 이번 실행에서 무엇이 버튼을 덮었나 (가림 진단)
    S.endedAt = 0;
    S.problem = false;
    S.message = '';
    S.navigation = [];
    S.fixSince = 0; S.fixPhase = 0; S.fixClickAt = 0; S.fixOpens = 0;
    S.times = [];
    S.byCause = {};
    S.stepStartedAt = Date.now();
    scrollClicks = 0;   // 중간에 멈췄다 다시 재생할 때 스크롤 상태가 남으면 안 된다
    lastLabel = '';
    ensurePhase = 0;
    retries = 0;
    lastOpenReloadAt = 0;
    /* 상태(S)에 없는 모듈 지역 변수도 같이 지운다. 앞 실행이 pause/finish 로 끝나면
     * markStep 이 안 불려 이것들이 그대로 남고, 다음 실행 1단계의 원인 분류에
     * 지난 실행의 대기 시간이 실린다. */
    phaseMs = {}; lastTickAt = 0;
    lastMutAt = 0; blockedEl = null;
    stopWaiting();
  }

  function play() {
    if (!S.steps.length) { log('녹화된 단계가 없습니다'); return; }
    S.recording = false;
    S.playing = true;
    if (!S.startedAt || S.idx === 0) S.startedAt = Date.now();
    resetRunState();
    save();
    log('재생 시작 (' + (S.idx + 1) + '/' + S.steps.length + ')');
  }
  /* 재생을 끝내며 결과를 알린다. 문제가 있으면 message 에 그 사실이 남아
   * hud 의 알림 판정이 ★완료★ 대신 ⚠멈춤⚠ 을 내도록 한다. */
  function finish(why, problem) {
    S.problem = !!problem;
    if (!S.endedAt) S.endedAt = Date.now();
    pause(why + timeReport());
  }

  function pause(why) {
    S.playing = false;
    S.playAfterReload = false;
    if (!S.endedAt) S.endedAt = Date.now();
    var took = elapsed();
    S.message = '재생 중지' + (why ? ' - ' + why : '') + (took ? '  [총 ' + secs(took) + ']' : '');
    save();
    log(S.message);
  }

  // 재조회를 예약한 문서는 더 이상 클릭/종료 판정을 하지 않는다.
  // 새 문서만 같은 단계에서 이어받으며 누적 개방 대기시간은 유지한다.
  function reloadForOpen(message) {
    S.playFrom = S.idx;
    S.playAfterReload = true;
    S.playing = false;
    S.message = message;
    save();
    log(message);
    setTimeout(function () { location.reload(); }, 0);
  }

  /* "새로고침한 다음 처음부터 재생" 예약. 지금 당장은 재생하지 않는다.
   * play() 를 먼저 부르면 tick 이 낡은 화면에서 1단계를 눌러버리고, 이어지는
   * 새로고침이 그 결과를 통째로 날린다 (정시 발사 때 날짜 선택이 사라지는 사고). */
  function armForReload(startIdx, fixDate) {
    if (!S.steps.length) { log('녹화된 단계가 없습니다'); return false; }
    S.recording = false;
    S.playing = false;
    S.idx = 0;
    S.playFrom = startIdx > 0 ? startIdx : 0;
    S.playAfterReload = true;
    S.startedAt = Date.now();   // 소요시간은 "발사 시점" 부터 센다 (새로고침 포함)
    resetRunState();            // play() 와 같은 것을 지운다 - 갈라지면 사고가 난다
    S.fixDate = fixDate || '';
    save();
    log(S.playFrom ? ('페이지 이동 후 ' + (S.playFrom + 1) + '단계부터 재생 예약됨')
                   : '새로고침 후 처음부터 재생 예약됨');
    return true;
  }
  /* 시작 단계를 받는다. 조회 화면 모드는 달력 단계를 건너뛰고 그 뒤부터 시작한다. */
  function reset(from, fixDate) {
    S.idx = from > 0 ? from : 0;
    if (arguments.length > 1) { S.fixDate = fixDate || ''; S.fixPhase = 0; S.fixSince = 0; }
    save();
    log(S.idx ? ((S.idx + 1) + '단계로') : '처음 단계로');
  }
  /* '삭제' 는 빈 상태로 두는 것보다 내장본으로 되돌리는 게 쓸모 있다.
   * 녹화가 꼬였을 때 되돌아갈 기준점이 생긴다. */
  function clear() {
    S.playing = false; S.recording = false; S.idx = 0;
    if (baked().length) { adoptBaked('삭제 -> 내장본 복원'); log('내장 단계 ' + S.steps.length + '개로 되돌렸습니다'); }
    else { S.steps = []; S.source = 'local'; save(); log('삭제됨'); }
  }

  /* 한 단계가 가리키는 요소를 찾는다.
   * dynamicDate: 특정 날짜 텍스트/셀렉터 대신 "지금 예약 가능한 것 중 가장 나중 날짜".
   *   마일리지는 매일 09:00 KST 에 하루치씩 새로 열려서, 녹화한 날짜는 다음날 못 쓴다.
   * dynamicCabin: 패널에서 고른 좌석 등급의 항공편 카드 (연습=일반석 / 실전=프레스티지). */
  function locate(step) {
    /* 목표 날짜를 정했으면 그 날짜를 찾는다. 안 정했으면 가장 나중 날짜(= 오늘
     * 새로 열린 날). 예전에는 늘 최신일만 찾아서, 목표가 최신일이 아니면
     * 영원히 새로고침만 했다. */
    if (step.dynamicDate) return U.findOpenDate(step.idPrefix, S.expectDate);
    if (step.dynamicCabin) return U.findCabin(S.cabin);
    var el = U.findEl(step.sel, step.text, { selectorOnly: step.selectorOnly });
    if (el) return el;
    /* alt: 화면에 따라 있을 수도 없을 수도 있는 선택지. 앞에서부터 찾아지는 것 하나만
     * 누른다. 결제수단이 그렇다 - 가는 편에는 네이버페이가 있는데 오는 편에는 없어서
     * 신용카드로 가야 한다. 둘 다 누르면 마지막 것으로 바뀌므로 하나만 골라야 한다. */
    if (step.alt) {
      for (var i = 0; i < step.alt.length; i++) {
        var a = step.alt[i];
        el = U.findEl(a.sel, a.text, { selectorOnly: a.selectorOnly });
        if (el) return el;
      }
    }
    return null;
  }

  /* 조회 화면의 날짜를 목표 날짜로 맞춘다. 끝났으면 true.
   *
   * 실측 구조(2026-08-27): 날짜칸(kds-dateinput)을 누르면 달력이 열리고,
   * #month202708 안의 td 가 각 날이다. 예약 가능한 날에만 -available 이 붙는다.
   *
   * 화면을 믿지 않는다. 마지막에 서버 응답(probe.shownDate)이 목표 날짜를 말해야
   * 통과시킨다. 그래서 칸을 조금 넓게 찾아도 엉뚱한 날 예매로 이어지지 않는다.
   * 확인이 안 되면 무엇이 안 됐는지 그대로 말하고 멈춘다. */
  function fixScreenDate(now) {
    var P = W.KE_PROBE || window.KE_PROBE;
    var want = S.fixDate;
    if (!S.fixSince) { S.fixSince = now; S.fixPhase = 0; }

    /* 날짜를 누른 뒤에는 "그 뒤에 온 응답" 만 근거로 삼는다. 낡은 응답을 그대로
     * 믿으면, 재조회가 안 됐는데도 맞은 줄 알고 엉뚱한 날 좌석을 누른다. */
    var seen = S.fixClickAt
      ? (P && P.shownDate && P.shownDate(S.fixClickAt)) || null
      : (P && P.shownDate && P.shownDate()) || U.searchedDate();
    if (seen === want) {                       // 이미 맞다 - 할 일이 없다
      S.fixDate = ''; S.fixSince = 0; S.fixPhase = 0;
      S.fixClickAt = 0;
      S.fixOpens = 0; save();
      log('조회 날짜가 ' + want + ' 로 맞춰졌습니다  [' + secs(elapsed()) + ']');
      return true;
    }
    if (now - S.fixSince > S.openWaitMaxMs) {
      finish('조회 화면을 ' + want + ' 로 바꾸지 못했습니다 ('
             + ((U.findStripDate(want) || {}).why
                || (S.fixOpens ? S.fixOpens + '번 눌렀는데 조회 결과가 안 바뀜'
                               : '날짜 띠에서 그 날을 누르지 못함'))
             + ') - 화면을 확인하세요', true);
      return false;
    }
    if (now - lastClickAt < S.gapMs) return false;

    if (S.fixPhase < 2) {
      /* 조회 결과 가운데의 날짜 띠를 누른다. 페이지 이동 없이 그 자리에서 다시
       * 조회된다.
       *
       * 위쪽 검색 위젯(날짜칸 → 달력 → [항공편 검색])으로 가는 길도 만들어봤는데
       * 실측(2026-08-28)에서 복불복이었다 - 될 때도 있고 달력 페이지로 되돌아갈
       * 때도 있었다. 되돌아가면 우리가 건너뛰려던 바로 그 페이지다. 그래서 버렸다. */
      var r = U.findStripDate(want);
      if (!r || !r.el) return false;           // 아직 안 그려졌다 - 기다린다
      if (!r.selectable) {
        /* 09:00 직전이면 그 날이 아직 안 열렸다. 화면이 새로 그려지면 같은 칸이
         * '선택 가능' 으로 바뀐다. 기다린다. */
        return false;
      }
      U.fireClick(r.el);
      lastClickAt = now;
      S.fixClickAt = now;
      S.fixOpens = (S.fixOpens || 0) + 1;
      S.fixPhase = 2; save();
      log(want + ' 을(를) 날짜 띠에서 눌렀습니다 - 조회가 갱신되기를 기다립니다');
      return false;
    }

    /* 2단계: 눌렀는데 서버가 아직 그 날짜를 말하지 않는다.
     *
     * 날짜 띠는 페이지 이동 없이 다시 조회하므로 보통은 잠시 기다리면 온다. 다만
     * 새로고침 직후에는 페이지가 클릭 핸들러를 아직 안 붙여 클릭이 그냥 사라질 수
     * 있다(실측: ▶ 재생 은 되는데 ▶ 대기 시작 은 안 됐다). 그래서 한 번만 누르고
     * 기다리지 않고, 갱신이 안 오면 다시 눌러본다. */
    if (now - S.fixClickAt > S.retryClickMs * 2) {
      S.fixPhase = 0;
      save();
      log('조회가 갱신되지 않아 ' + want + ' 을(를) 다시 누릅니다 ('
          + (S.fixOpens || 0) + '회째)');
    }
    return false;
  }


  function tick() {
    if (!S.playing) return;
    /* 문서가 아직 파싱 중이면 요소는 이미 DOM 에 있어도 그 페이지의 스크립트가
     * 클릭 핸들러를 아직 안 붙였을 수 있다. 그 틈에 누르면 예외도 없이 아무 일도
     * 안 일어난다. DOMContentLoaded 이후(interactive/complete)에만 진행한다. */
    if (document.readyState === 'loading') return;
    var now = Date.now();
    if (now - lastClickAt < S.gapMs) return;

    if (S.fixDate && !fixScreenDate(now)) return;

    var step = S.steps[S.idx];
    if (!step) { pause('전체 단계 완료'); return; }

    var nav = S.navigation || (S.navigation = []);
    if (!nav.length || nav[nav.length - 1].path !== location.pathname) {
      nav.push({at: now, path: location.pathname, step: S.idx + 1});
      if (nav.length > 20) nav.shift();
      save();
    }
    // 첫 단계에도 경로 검증이 필요하다. 세션 이탈 화면에서 날짜를 찾거나
    // 임의 검색 버튼을 누르지 않고, 잠깐의 라우팅 전환만 기다린다.
    if (step.dynamicDate && step.url && hereUrl().indexOf(step.url) < 0) {
      beganWaiting(now);
      phase('달력 경로 이탈', now);
      if (tooLong(Math.min(S.stepTimeoutMs, 2000))) {
        finish('달력 화면 이탈: 목표 날짜 선택 전 ' + location.pathname
               + ' 로 이동했습니다 - 로그인·사이트 안내·노선 상태를 확인하세요', true);
      }
      return;
    }
    // 성공 응답이 없는 상태를 매진으로 설명하거나 이전 결과를 클릭하지 않는다.
    if (step.dynamicCabin && U.onDeparture()) {
      var np = W.KE_PROBE || window.KE_PROBE;
      var net = np && np.availabilityState ? np.availabilityState() : null;
      if (net && net.state !== 'valid') {
        beganWaiting(now);
        phase('항공편 조회 응답 대기', now);
        if (net.state !== 'pending') {
          finish('항공편 조회 실패 (' + net.state + (net.status ? ', HTTP ' + net.status : '')
                 + (net.code ? ', 코드 ' + net.code : '')
                 + ') - 좌석 상태 판정 불가. 정상 조회부터 다시 확인하세요', true);
        } else if (tooLong(S.stepTimeoutMs)) {
          finish('항공편 조회 응답 대기 시간 초과 - 좌석 상태 판정 불가', true);
        }
        return;
      }
    }

    /* 페이지가 넘어가는 단계 바로 다음은, 새 화면이 뜬 뒤에 눌러야 한다.
     *
     * 실측(2026-08-28): 조회 화면에서 '다음'(5단계)을 누른 0.28초 뒤에 6단계 '확인'
     * 이 아직 넘어가지 않은 조회 화면에서 눌렸다. 그 클릭은 곧 이어진 페이지 이동에
     * 씻겨나갔고, 결제 화면에서는 6단계가 안 된 채 7단계를 기다려 영영 멈췄다.
     * 화면이 잠잠해지길 기다리는 것(settle)으로 우연히 가려져 있던 구멍인데,
     * 그 대기를 줄이자 드러났다.
     *
     * 단계마다 녹화된 url 이 있으니 추측할 필요가 없다. 앞 단계와 url 이 다르면
     * 페이지 이동이 예정된 것이고, 그 화면이 뜨기 전에는 누르지 않는다. */
    var prev = S.idx > 0 ? S.steps[S.idx - 1] : null;
    if (prev && step.url && prev.url && prev.url !== step.url
        && hereUrl().indexOf(step.url) < 0) {
      phase('페이지 이동 대기', now);
      beganWaiting(now);
      if (tooLong(S.stepTimeoutMs)) {
        pause('단계 ' + (S.idx + 1) + ' 은 ' + step.url + ' 화면의 단계인데'
              + ' 지금은 ' + hereUrl() + ' 입니다 - 화면을 확인하세요'
              + hiddenNote());
      }
      return;
    }

    if (isPay(step) && !S.allowPay) {
      pause('결제 단계입니다 - 직접 확인하고 누르세요');
      return;
    }

    if (step.requireSelectedCabin) {
      var selection = U.cabinSelection(S.cabin);
      if (!selection.ready) {
        phase('운임 선택 확인', now);
        beganWaiting(now);
        if (tooLong(S.stepTimeoutMs)) {
          finish('선택한 운임과 총액이 반영되지 않았습니다 - 다음 단계로 진행하지 않음', true);
          return;
        }
        if (!selection.checked && selection.el && U.hittable(selection.el)
            && now - lastClickAt >= S.retryClickMs) {
          U.fireClick(selection.el);
          lastClickAt = now;
          log('운임 선택이 해제되어 목표 등급을 다시 선택합니다');
        }
        return;
      }
    }

    /* 앞 단계 결과가 반영되기 전에 누르면 클릭이 무시된다. 화면이 잠잠해질 때까지
     * 기다린다. 계속 바뀌기만 하는 화면도 있으므로 상한을 둔다.
     *
     * 다만 기다림은 "앞 클릭이 먹었나" 를 시간으로 짐작하는 것일 뿐이다. 더 직접적인
     * 증거가 있으면 짐작할 필요가 없다: 이 단계의 요소가 앞 클릭 직전에는 없었는데
     * 지금 나타났고 누를 수 있다면, 그 등장 자체가 앞 클릭이 먹었다는 뜻이다.
     * 실측(2026-08-28): 25.7초 중 '화면 안정' 이 7.8초(30%)였다. 대한항공 화면은
     * 클릭 뒤 계속 다시 그려져서 잠잠해지는 250ms 를 못 건지고 상한만 채우는 일이 많다.
     * 앞 클릭 직전에 이미 있던 요소면 구분이 안 되므로 종전대로 기다린다. */
    if (S.idx > 0 && lastClickAt) {
      var appeared = false;
      if (!nextWasPresent) {
        var early = locate(step);
        appeared = !!(early && U.hittable(early));
      }
      if (!appeared) {
        var quiet = now - Math.max(lastMutAt, lastClickAt);
        if (quiet < S.settleMs && now - lastClickAt < S.maxSettleMs) { phase('화면 안정', now); return; }
      }
    }

    /* ensure 단계: "지금 값이 want 면 그대로 두고, 아니면 골라서 맞춘다".
     * 통화(KRW/USD)처럼 화면 상태에 따라 눌러야 할 수도 아닐 수도 있는 것에 쓴다.
     * 좌석 등급을 바꿀 때마다 통화가 되돌아가는 경우가 있어서, 매번 확인해야 한다.
     *   1) 컨트롤 라벨에 want 가 이미 있으면 -> 통과
     *   2) 없으면 컨트롤을 눌러 목록을 연다
     *   3) 목록에서 want 가 든 항목을 눌러 맞춘다 */
    /* ensure 단계: "지금 값이 want 면 그대로 두고, 아니면 골라서 맞춘다".
     * 통화(KRW/USD)나 카드 종류처럼 화면 상태에 따라 손대야 할 수도 아닐 수도 있는 것.
     *
     * 실제 대한항공 통화 선택은 3단계다: #currencyBtn 을 눌러 모달을 열고 -> KRW 라디오
     * 라벨을 고르고 -> [적용] 을 눌러야 반영된다. 적용을 빠뜨리면 모달만 열렸다 닫히고
     * 통화는 그대로다. 그래서 optionSel/applySel 을 단계에 적어둔다.
     * 네이티브 <select> 면 클릭으로는 목록이 안 열리므로 value 를 직접 바꾼다. */
    /* onlyIfPrev: 직전 단계에서 이걸 눌렀을 때만 진행한다.
     * 카드 종류는 카드 결제를 골랐을 때만 나타난다. 예전엔 "없으면 2.5초 기다렸다
     * 넘어감" 이었는데, 09:00 경쟁에서 의미 없이 2.5초를 버리는 짓이다.
     * 앞에서 무엇을 눌렀는지는 이미 알고 있으니 기다릴 이유가 없다. */
    if (step.onlyIfPrev && lastLabel.indexOf(step.onlyIfPrev) === -1) {
      S.idx++; retries = 0; stopWaiting(); save();
      log('재생 ' + S.idx + '/' + S.steps.length + ': 해당 없어 건너뜀 ('
          + step.onlyIfPrev + ' 을 안 골랐음)');
      return;
    }

    if (step.ensure) {
      var ctrl = (step.sel ? U.findEl(step.sel, '', { selectorOnly: true }) : null)
                 || U.findContaining(step.text);

    /* changed=true 는 실제로 값을 바꿨다는 뜻이다.
     * 통화를 바꾸면 사이트가 화면을 다시 그리면서 처음 페이지로 돌아간다. 그대로
     * 다음 단계로 가면 그 요소가 있을 리 없어 통째로 막힌다(실측). restartFrom 이
     * 있으면 그 단계부터 다시 밟는다 - 두 번째에는 이미 KRW 라 통과하므로 반복되지
     * 않는다. 이미 맞아서 아무것도 안 바꿨으면 되돌아갈 이유가 없다. */
      var doneEnsure = function (how, changed) {
        ensurePhase = 0;
        retries = 0; stopWaiting(); lastClickAt = now;
        if (changed && typeof step.restartFrom === 'number') {
          S.idx = step.restartFrom;
          save();
          log(how + ' - 화면이 되돌아가므로 ' + (step.restartFrom + 1) + '단계부터 다시 진행합니다');
          return;
        }
        markStep(S.idx + 1, step.ensure || step.text);
        S.idx++; save();
        log('재생 ' + S.idx + '/' + S.steps.length + ': ' + how + '  [' + secs(elapsed()) + ']');
      };

      /* 제한시간 시계를 여기서 한 번 돌린다.
       *
       * tooLong() 은 beganWaiting() 이 매 tick 불려야 시간이 쌓인다. 그런데 아래
       * 네 갈래(목록에 없음 / 컨트롤 못 찾음 / 항목 못 찾음 / [적용] 못 찾음) 중
       * 셋에 그게 빠져 있었다. waitedMs 가 0에서 안 올라가니 제한시간에 영영 안
       * 걸리고, pause/finish 가 안 불려 소리도 제목도 안 바뀐다 - 화면에는
       * "재생 중" 만 뜬 채 통화 단계에서 영원히 멈춘다.
       *
       * '가려진 시간은 빼고 센다' 로 바꾸면서 일부 경로에만 연결한 내 실수다.
       * 갈래마다 챙기지 말고 들어오는 길목에서 한 번 돌린다. */
      beganWaiting(now);

      // --- 네이티브 select ---
      var nsel = ctrl && (ctrl.tagName === 'SELECT'
                          ? ctrl : (ctrl.querySelector && ctrl.querySelector('select')));
      if (nsel) {
        var cur = nsel.options[nsel.selectedIndex];
        if (cur && cur.text.indexOf(step.ensure) !== -1) { doneEnsure('이미 ' + step.ensure); return; }
        for (var k2 = 0; k2 < nsel.options.length; k2++) {
          if (nsel.options[k2].text.indexOf(step.ensure) === -1) continue;
          nsel.selectedIndex = k2;
          try {
            nsel.dispatchEvent(new Event('input', { bubbles: true }));
            nsel.dispatchEvent(new Event('change', { bubbles: true }));
          } catch (e) {}
          doneEnsure(step.ensure + ' 로 맞춤 (select)', true);
          return;
        }
        if (tooLong(S.stepTimeoutMs)) {
          finish('목록에 ' + step.ensure + ' 가 없습니다 - 직접 선택하세요', true);
        }
        return;
      }

      // --- 0) 이미 맞는가 / 컨트롤 열기 ---
      if (ensurePhase === 0) {
        if (ctrl && U.label(ctrl).indexOf(step.ensure) !== -1) {
          doneEnsure('이미 ' + step.ensure + ' 이라 그대로 둠');
          return;
        }
        if (!ctrl) {
          /* optional: 이 화면에 아예 없을 수 있는 단계 (네이버페이로 결제하면
           * 카드 종류 드롭다운이 나타나지 않는다). 잠깐 기다려보고 없으면 넘어간다. */
          if (step.optional && waitedMs > (S.optionalMs || 400)) {
            S.idx++; retries = 0; stopWaiting(); save();
            log('재생 ' + S.idx + '/' + S.steps.length + ': 이 화면에 없어 건너뜀 - '
                + (step.text || step.sel).slice(0, 20));
            return;
          }
          if (tooLong(S.stepTimeoutMs)) {
            finish('단계 ' + (S.idx + 1) + ' 컨트롤을 못 찾음: ' + (step.text || step.sel), true);
          }
          return;
        }
        if (!U.hittable(ctrl)) return;
        lastClickAt = now;
        U.fireClick(ctrl);
        ensurePhase = 1;
        stopWaiting(); beganWaiting(now);
        log(step.ensure + ' 로 바꾸기 위해 목록을 엽니다');
        return;
      }

      // --- 1) 원하는 항목 고르기 ---
      if (ensurePhase === 1) {
        var opt = (step.optionSel ? U.findEl(step.optionSel, '', { selectorOnly: true }) : null)
                  || U.findContaining(step.ensure, ctrl);
        if (!opt) {
          if (tooLong(S.stepTimeoutMs)) {
            finish('목록에서 ' + step.ensure + ' 를 못 찾았습니다 - 직접 선택하세요', true);
          }
          return;
        }
        lastClickAt = now;
        U.fireClick(opt);
        /* 옛날에는 waitingSince 를 직접 넣었는데, 지금 제한시간은 waitedMs 를 본다.
         * 그대로 두면 아무 효과 없는 죽은 줄이다. 시계를 새로 시작한다. */
        if (step.applySel || step.applyText) {
          ensurePhase = 2; stopWaiting(); beganWaiting(now); return;
        }
        doneEnsure(step.ensure + ' 로 맞춤', true);
        return;
      }

      // --- 2) [적용] 눌러 반영 ---
      var ap = (step.applySel ? U.findEl(step.applySel, '', { selectorOnly: true }) : null)
               || U.findContaining(step.applyText || '적용');
      if (!ap) {
        if (tooLong(S.stepTimeoutMs)) {
          finish('[적용] 을 못 찾았습니다 - 직접 눌러주세요', true);
        }
        return;
      }
      lastClickAt = now;
      U.fireClick(ap);
      doneEnsure(step.ensure + ' 로 맞추고 적용', true);
      return;
    }

    var el = locate(step);

    /* 찾았어도 "지금 누를 수 있는" 상태여야 한다.
     * 모달이 떠 있으면 그 뒤 버튼도 크기·visibility 상으로는 멀쩡히 보이지만 실제
     * 클릭은 모달이 먹는다. 그대로 진행하면 화면은 모달에서 멈춰 있는데 단계만
     * 줄줄이 "성공" 으로 찍히고 결제까지 눌렀다고 보고한다(실측에서 그랬다).
     * 여기서 막아두면 최소한 거짓 완료는 없다. */
    /* "아래로 스크롤" 은 몇 번 눌러야 하는지 화면 길이에 따라 다르다. 녹화한 횟수
     * (2번)로 고정하면 모자랄 때 팝업이 안 내려가고, 남으면 다음 단계를 건너뛰게 된다.
     * 버튼이 사라질 때까지 누르는 게 사이트가 의도한 방식이다. */
    /* 스크롤이 끝났다는 신호는 사이트마다 다르다:
     *  - 대한항공: #btnScrollDown 이 숨고 #btnConfirm 이 나타난다 (요소가 사라짐)
     *  - 같은 버튼의 라벨만 '확인' 으로 바뀌는 형태도 있다
     * 둘 다 "더 이상 스크롤 버튼이 아니다" 로 판정한다. */
    /* 이미 동의된 항목을 다시 누르면 꺼진다. 녹화에 같은 동의가 두 번 들어 있어서
     * 실제로 그렇게 꺼졌고, 이후 모달이 안 떠 흐름이 통째로 막혔다. 켜져 있으면 넘어간다. */
    if (el && TOGGLEY.test(step.text || '') && U.alreadyOn(el)) {
      S.idx++;
      waitingSince = 0;   // 다음 단계는 제한시간을 새로 받아야 한다
      retries = 0;
      save();
      log('재생 ' + S.idx + '/' + S.steps.length + ': 이미 켜져 있어 누르지 않음 - '
          + String(step.text || step.sel).slice(0, 20) + '  [' + secs(elapsed()) + ']');
      return;
    }

    var scrollDone = SCROLLY.test(step.text || '') && scrollClicks > 0
                     && (!el || !SCROLLY.test(U.label(el)));
    if (scrollDone) {
      scrollClicks = 0;
      waitingSince = 0;   // 다음 단계는 제한시간을 새로 받아야 한다
      S.idx++;
      /* 녹화에는 스크롤이 여러 번 찍혀 있지만 위에서 버튼이 사라질 때까지 눌렀으므로
       * 뒤따르는 같은 스크롤 단계는 이미 소화된 것이다. 건너뜀으로 세면 멀쩡한 재생이
       * "확인 필요" 로 보고되므로, 조용히 함께 넘긴다. */
      var merged = 0;
      while (S.idx < S.steps.length && SCROLLY.test(S.steps[S.idx].text || '')
             && S.steps[S.idx].sel === step.sel) {
        S.idx++;
        merged++;
      }
      retries = 0;
      save();
      log('재생 ' + S.idx + '/' + S.steps.length + ': 스크롤 완료'
          + (merged ? ' (연속 스크롤 ' + (merged + 1) + '단계를 한 번에 처리)' : '')
          + '  [' + secs(elapsed()) + ']');
      return;
    }

    /* hittable 은 화면 밖이면 판정을 보류하고 통과시킨다. 그런데 fireClick 은 요소에
     * 이벤트를 직접 쏘므로, 그대로 두면 모달이 떠 있어도 화면 밖 버튼은 그냥 눌린다
     * (모달 가드가 통째로 우회된다). 스크롤해서 화면에 넣은 뒤 다시 판정한다. */
    if (el && !U.hittable(el, true)) {
      try { el.scrollIntoView({ block: 'center' }); } catch (e) {}
    }
    if (el && !U.hittable(el)) {
      /* 무엇이 덮었는지 남긴다. '가림'으로 버린 시간을 줄이려면 정체를 알아야 하는데,
       * 실전(2026-09-02)에서 7단계 가림 1.1초가 찍혔지만 원인을 알 수 없었다.
       * 단계마다 처음 막힌 순간 한 번만 기록한다(매 tick 훑으면 비싸다). */
      if (!blockedEl) {
        try {
          S.blocks = S.blocks || [];
          S.blocks.push({ step: S.idx + 1, at: Date.now(),
                          label: String(step.text || step.sel || '').slice(0, 20),
                          info: U.coverInfo(el) });
          if (S.blocks.length > 10) S.blocks.shift();
        } catch (e) {}
      }
      blockedEl = el;
      el = null;
    } else {
      blockedEl = null;
    }
    /* 목표 날짜를 정했으면 그 날짜 칸을 직접 찾아왔다(findOpenDate). 그래도 라벨을
     * 한 번 더 대조한다 - 엉뚱한 날짜로 마일리지가 빠지는 게 최악이다. */
    if (step.dynamicDate && el && S.expectDate) {
      var got = U.label(el);
      var same = U.sameDate(S.expectDate, got);
      if (same !== true) {
        pause('찾은 날짜가 목표와 다릅니다 - 목표: ' + S.expectDate
              + ' / 화면: ' + got.slice(0, 30));
        return;
      }
      S.openWaitSince = 0;
      S.times = []; S.stepStartedAt = Date.now();   // 열리기를 기다린 시간은 빼고 센다
    }

    /* 목표 날짜 칸이 화면에 없다. 두 경우를 반드시 구분해야 한다:
     *
     *   달력이 아직 안 그려졌다      -> '없다' 가 아니라 '아직 모른다'. 기다린다
     *   달력은 그려졌는데 그 날이 없다 -> 아직 안 열린 것. 새로고침해서 다시 본다
     *
     * 구분하지 않으면 페이지가 뜨기도 전에 새로고침해서 영영 안 뜬다 - 실측
     * (2026-08-28)에서 빈 화면인 채로 무한 새로고침만 했다.
     *
     * 달력이 그려졌다는 증거는 '고를 수 있는 날짜 칸이 하나라도 있다' 는 것이다.
     * 거기에 더해 잠시(retryClickMs) 기다려본다 - 두 달치를 나눠 그리는 중일 수도
     * 있어서, 첫 칸이 나오자마자 판정하면 성급하다. */
    if (step.dynamicDate && !el && S.expectDate && !blockedEl
        && waitedMs > S.retryClickMs
        && U.openDateCells(step.idPrefix).length) {
      if (!S.openWaitSince) S.openWaitSince = now;
      if (now - S.openWaitSince > S.openWaitMaxMs) {
        var seen = U.openDateCells(step.idPrefix).map(function (c) {
          return U.monthDay(U.label(c));
        }).filter(Boolean);
        finish('목표 날짜(' + S.expectDate + ')가 ' + Math.round(S.openWaitMaxMs / 1000)
               + '초 동안 안 열렸습니다 - 화면을 확인하세요 (달력에 있는 날: '
               + (seen.slice(-8).join(', ') || '없음') + ')', true);
        return;
      }
      if (now - lastOpenReloadAt < S.openRetryMs) return;
      lastOpenReloadAt = now;
      S.openReloads = (S.openReloads || 0) + 1;
      S.idx = 0;
      reloadForOpen('목표 날짜(' + S.expectDate + ')가 아직 달력에 없습니다 - 새로고침하고 다시 봅니다');
      return;
    }

    if (!el) {
      phase(blockedEl ? '가림' : '요소 없음', now);
      beganWaiting(now);
      /* optional: 이 화면에 아예 없을 수 있는 단계. 기다려보고 없으면 조용히 넘어간다. */
      if (step.optional && !blockedEl && waitedMs > (S.optionalMs || 400)) {
        markStep(S.idx + 1, step.text || step.sel);
        S.idx++; retries = 0; stopWaiting(); save();
        log('재생 ' + S.idx + '/' + S.steps.length + ': 이 화면에 없어 건너뜀 - '
            + (step.text || step.sel).slice(0, 20) + '  [' + secs(elapsed()) + ']');
        return;
      }
      /* 스크롤 단계인데 버튼을 못 찾는 경우: 버튼이 스크롤에 밀려 사라졌거나 라벨이
       * 바뀐 것일 수 있다. 그래도 팝업은 끝까지 내려야 [확인] 이 열리므로, 버튼과
       * 무관하게 스크롤 자체는 계속 밀어준다. */
      if (SCROLLY.test(step.text || '')) U.scrollToBottom();

      /* 조회 화면에서 좌석이 아직 안 보이는 것은 "그 등급이 없다" 가 아니라 "아직
       * 안 열렸다" 일 수 있다. 09:00 정각에 새로고침해도 서버가 좌석을 몇 백
       * 밀리초 늦게 푸는 경우가 그렇다. 멈춰서 기다리는 건 경쟁에서 최악이므로,
       * 달력에서 목표 날짜를 기다릴 때와 똑같이 다시 불러와서 본다.
       *
       * 단계 번호는 그대로 둔다 - 새로고침 뒤 이 단계부터 다시 본다. */
      /* "그 등급이 없다" 와 "페이지가 아직 안 떴다" 는 전혀 다르다. 구분하지 않으면
       * 안 떴는데 새로고침 -> 또 안 뜸 -> 무한반복이 된다(실측 2026-08-28).
       * 달력에서 겪은 것과 같은 사고인데 좌석 쪽은 안 고쳐져 있었다.
       *
       * 근거는 둘. 서버가 조회 응답을 줬거나(목록이 비어 있어도 그건 사실이다),
       * 화면에 운임 카드가 이미 그려졌거나. 둘 다 아니면 아직 안 뜬 것이다. */
      var answered = false;
      try {
        var P2 = W.KE_PROBE || window.KE_PROBE;
        answered = !!(P2 && P2.answered && P2.answered());
      } catch (e) {}
      if (step.dynamicCabin && U.onDeparture() && waitedMs > S.retryClickMs
          && (answered || U.cabinListReady())) {
        if (!S.openWaitSince) S.openWaitSince = now;
        /* 서버가 '이 등급 매진(soldout:true)' 이라고 명확히 말하면, 계속 새로고침해봐야
         * 좌석은 다시 안 판다. 다만 09:00 정각엔 몇 백ms 늦게 풀리는 경우가 있어
         * soldOutGraceMs 만큼은 지켜보고, 그동안 계속 매진이면 즉시 멈춘다.
         * (예전엔 openWaitMaxMs 180초를 다 채워 3분을 헛돌았다.) */
        var so = null, P3 = null;
        try {
          P3 = W.KE_PROBE || window.KE_PROBE;
          if (P3 && P3.keCabin) so = P3.keCabin(S.cabin, S.expectDate || S.fixDate);
        } catch (e) {}
        /* 응답은 왔는데 그 등급을 못 읽었다면, 화면이 '다른 날짜' 를 조회 중일 수 있다.
         * 그걸 "좌석이 아직 안 열렸다" 로 오해하면 영원히 새로고침만 한다
         * (실측 2026-09-02 조회모드: 목표 08-11 인데 화면은 08-06 을 보고 있었다).
         * 엉뚱한 날짜로 예매하는 것이 최악이므로, 조용히 도는 대신 분명히 멈춘다. */
        var wantD = S.expectDate || S.fixDate;
        if (!so && answered && wantD) {
          var shown = null;
          try { shown = P3 && P3.shownDate ? P3.shownDate() : null; } catch (e) {}
          if (shown && U.sameDate(wantD, shown) !== true) {
            finish('화면이 다른 날짜(' + shown + ')를 조회 중입니다 - 목표 '
                   + wantD + ' 로 맞춰주세요', true);
            return;
          }
        }
        if (so && so.soldout) {
          if (!S.soldOutSince) S.soldOutSince = now;
          if (now - S.soldOutSince > S.soldOutGraceMs) {
            finish('"' + S.cabin + '" 매진'
                   + (so.eySeats ? ' (일반석 ' + so.eySeats + '석 남음)' : '')
                   + ' - ' + secs(elapsed()) + ' 지점, 재고침 ' + (S.openReloads || 0) + '회', true);
            return;
          }
        } else if (S.soldOutSince) {
          S.soldOutSince = 0;   // 다시 열렸다(좌석이 돌아옴) - 매진 판정 취소
        }
        if (now - S.openWaitSince > S.openWaitMaxMs) {
          finish('"' + S.cabin + '" 좌석이 ' + Math.round(S.openWaitMaxMs / 1000)
                 + '초 동안 안 나왔습니다 - 화면을 확인하세요', true);
          return;
        }
        if (now - lastOpenReloadAt < S.openRetryMs) return;
        lastOpenReloadAt = now;
        S.openReloads = (S.openReloads || 0) + 1;
        reloadForOpen('조회 결과에 "' + S.cabin + '" 이(가) 없습니다 - 새로고침하고 다시 봅니다 ('
            + Math.round((now - S.openWaitSince) / 1000) + '초째)');
        return;
      }

      /* 다시 눌러볼 수 없는 상황(가려짐/토글 단계 등)이라도, 사이트가 "더는 안 된다"
       * 고 말하고 있으면 제한시간을 다 채울 이유가 없다. 매 tick 훑으면 비싸므로
       * 잠깐 기다린 뒤부터만 본다. */
      if (waitedMs > 1000) {
        var stop = fatalNotice();
        if (stop) {
          finish('사이트 안내: "' + stop.slice(0, 60) + '" - 더 진행할 수 없습니다', true);
          return;
        }
      }
      if (waitedMs > S.retryClickMs && retryPrevClick(now)) return;
      if (tooLong(S.stepTimeoutMs)) {
        // 스크린샷 한 장으로 원인 파악이 되도록 패널 상태줄에 진단 요약을 그대로 붙인다.
        var diag = blockedEl
          ? '무언가에 가려 누를 수 없습니다 (모달이 떠 있는지 확인하세요): '
            + String(U.label(blockedEl)).slice(0, 20)
          : step.dynamicDate
          ? '최신 오픈일 셀을 못 찾음 (id 접두어: ' + (step.idPrefix || 'dep-fare-') + ')'
          : step.dynamicCabin
            ? (U.cabinOnlyCodeshare && U.cabinOnlyCodeshare(S.cabin)
                 ? '"' + S.cabin + '" 은 코드셰어(외항사 운항)편에만 있습니다 - 대한항공 운항편에는 없어 건너뜁니다'
                 : '"' + S.cabin + '" 좌석이 이 화면에 없습니다 (그날 그 등급이 안 열렸을 수 있음)')
            : U.diagnoseText(step.sel, step.selectorOnly ? '' : step.text);
        pause('단계 ' + (S.idx + 1) + ' 요소를 못 찾음: ' + (step.text || step.sel || '').slice(0, 30)
              + ' [' + diag + ']' + hiddenNote());
      }
      return;
    }

    waitingSince = 0;
    lastClickAt = now;
    /* 무언가를 눌렀다는 것은 기다리던 것이 나타났다는 뜻이다. 누적된 대기 시간을
     * 다음 단계로 넘기면, 뒤에서 잠깐 못 찾은 것이 곧바로 제한시간 초과가 된다. */
    S.openWaitSince = 0;

    /* 다음 단계의 요소가 "지금 이미" 화면에 있는가를 눌러보기 직전에 기록해둔다.
     * 없다가 나타나면 그건 이 클릭이 먹혔다는 직접적인 증거라, 화면이 잠잠해지기를
     * 기다릴 필요가 없다 (아래 '화면 안정' 참고). 이미 있었다면 구분할 수 없으므로
     * 종전대로 기다린다. */
    try {
      var nx = S.steps[S.idx + 1];
      nextWasPresent = !!(nx && locate(nx));
    } catch (e) { nextWasPresent = true; }

    /* 결제 단계는 눌렀다고 끝난 게 아니다. 팝업이 차단되면 로그만 남고 창은 안 뜬다.
     * 누르기 직전의 open 기록을 잡아두고, 잠시 뒤 새 기록이 생겼는지로 판정한다. */
    var payBefore = isPay(step) ? ((S.lastOpen && S.lastOpen.at) || 0) : null;

    lastLabel = U.label(el);   // 다음 단계의 onlyIfPrev 판단에 쓴다
    /* 달력에서 무슨 날을 골랐는지 남긴다. 조회 페이지에 도착하면 이 값이 그 주소의
     * '가는 날' 이 되고, 다음 번 바로 시작이 어느 자리를 고칠지 여기서 정해진다. */
    if (step.dynamicDate) { S.pickedDate = U.monthDay(lastLabel) || ''; }
    U.fireClick(el);


    /* "아래로 스크롤" 은 버튼을 누르는 것만으로는 불안하다. 스크롤이 진행되면 버튼
     * 자체가 위로 밀리거나 화면 밖으로 나가서 클릭이 빗나가고, 팝업이 끝까지 안 내려가
     * [확인] 이 안 열린 채 멈춘다. 스크롤 영역을 직접 바닥까지 내려 확실히 한다. */
    if (SCROLLY.test(step.text || '')) {
      U.scrollToBottom();
      scrollClicks++;
      if (scrollClicks < 20) { save(); return; }   // 버튼이 사라질 때까지 같은 단계를 반복
      /* 20번을 눌렀는데도 버튼이 그대로면 끝까지 내려갔는지 확인하지 못한 것이다.
       * 그냥 넘어가되 완료로 보고하지는 않는다. */
      scrollClicks = 0;
      S.problem = true;
      log('스크롤을 20번 눌렀는데도 버튼이 남아 있습니다 - 팝업을 확인하세요');
    }

    markStep(S.idx + 1, step.text || step.sel);
    S.idx++;
    retries = 0;
    save();
    log('재생 ' + S.idx + '/' + S.steps.length + ': ' + (step.text || step.sel).slice(0, 30)
        + '  [' + secs(elapsed()) + ']');
    if (S.idx >= S.steps.length) {
      /* 결제 단계였다면 "눌렀다" 와 "결제창이 떴다" 는 전혀 다르다. 팝업이 차단되면
       * 로그만 남고 창은 안 뜬다. 여기서 바로 완료를 알리면 성공음이 울리고 제목이
       * ★완료★ 로 바뀌어, 정작 결제창이 없는데 사용자가 자리를 뜬다.
       * 창이 떴는지 확인한 뒤에 알린다. */
      if (payBefore !== null) {
        S.playing = false;      // 더 이상 tick 이 돌지 않게 하되, 알림은 판정 후에
        S.endedAt = Date.now();
        /* 결제창이 떴는지는 1.5초 뒤에 안다. 그런데 그 사이에 페이지가 넘어가면
         * 그 판정이 영영 안 온다 - 그러면 단계별 소요시간도 같이 사라진다.
         * 지금 아는 것만이라도 먼저 남긴다. 뒤에 판정이 오면 덮어쓴다. */
        S.message = '결제하기를 눌렀습니다 - 결제창 확인 중' + timeReport();
        save();
        setTimeout(function () {
          var o = S.lastOpen;
          if (o && o.ok && o.at > payBefore) finish('전체 단계 완료 - 결제창이 열렸습니다');
          else finish('결제하기를 눌렀지만 결제창이 열리지 않았습니다. '
                      + '팝업 차단일 수 있으니 결제 버튼을 직접 눌러주세요', true);
        }, 1500);
        return;
      }
      finish('전체 단계 완료');
    }
  }

  /* 이 크롬이 가려진 창을 늦추는가를 직접 잰다.
   *
   * "최소화하면 안 됩니다" 는 말로만 하면 믿기 어렵고, 사람마다 크롬 설정도 다르다.
   * 60ms 주기가 가려져 있는 동안 얼마나 벌어지는지 재서 사실을 보여준다.
   * 늦추지 않는 크롬(아래 플래그로 띄운 경우)이면 60ms 그대로 유지된다:
   *   --disable-background-timer-throttling
   *   --disable-backgrounding-occluded-windows
   *   --disable-renderer-backgrounding
   * Playwright 가 테스트할 때 쓰는 것도 이 플래그들이라, 테스트에서는 이 문제가
   * 아예 나타나지 않는다. 실제 크롬과 다른 지점이므로 사람 눈으로 확인해야 한다. */
  var throttle = { hiddenGapMs: 0, samples: 0, lastAt: 0 };
  function measureThrottle(now) {
    var d = throttle.lastAt ? now - throttle.lastAt : 0;
    throttle.lastAt = now;
    if (!document.hidden) return;
    if (d <= 0) return;
    throttle.samples++;
    if (d > throttle.hiddenGapMs) throttle.hiddenGapMs = d;
  }

  // 페이지가 바뀌어도 localStorage 의 idx 에서 이어서 재생된다
  setInterval(function () { measureThrottle(Date.now()); tick(); }, 60);
  new MutationObserver(tick).observe(document, { childList: true, subtree: true });

  /** ke_award/steps.json 에 그대로 붙여넣을 수 있는 형태로 뽑는다. */
  function exportJson() {
    return JSON.stringify({
      note: '패널 [내보내기] 로 뽑은 예매 단계. ke_award/steps.json 에 덮어쓰고 node build.mjs 하면 스크립트에 내장된다.',
      recordedAt: new Date().toISOString().slice(0, 19).replace('T', ' '),
      steps: S.steps
    }, null, 2);
  }

  /** 화면에 텍스트박스를 띄워 복사시킨다. 클립보드 API 가 막힌 환경도 있어서 폴백이 필요. */
  function showExport() {
    var json = exportJson();
    try { navigator.clipboard.writeText(json); } catch (e) {}
    var old = document.getElementById('ke-export');
    if (old) old.remove();
    var box = document.createElement('div');
    box.id = 'ke-export';
    box.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.6);' +
                        'display:flex;align-items:center;justify-content:center';
    box.innerHTML =
      '<div style="background:#fff;padding:16px;border-radius:8px;width:min(680px,90vw)">' +
      '<b style="font:13px sans-serif">ke_award/steps.json 에 덮어쓰고 <code>node build.mjs</code></b>' +
      '<textarea id="ke-export-ta" style="width:100%;height:50vh;margin-top:8px;font:11px Consolas,monospace"></textarea>' +
      '<button id="ke-export-close" style="margin-top:8px;padding:6px 12px">닫기</button></div>';
    document.documentElement.appendChild(box);
    var ta = box.querySelector('#ke-export-ta');
    ta.value = json;
    ta.select();
    box.querySelector('#ke-export-close').onclick = function () { box.remove(); };
    console.log(json);
    log('내보내기 - 클립보드에 복사했습니다 (' + S.steps.length + '단계)');
  }

  // ---- 단계 편집 ----------------------------------------------------------
  function removeStep(i) {
    if (i < 0 || i >= S.steps.length) return;
    S.source = 'local';
    var gone = S.steps.splice(i, 1)[0];
    if (S.idx > i) S.idx--;
    save(); log('삭제: ' + (gone.text || gone.sel).slice(0, 24));
  }
  function moveStep(i, dir) {
    var j = i + dir;
    if (i < 0 || i >= S.steps.length || j < 0 || j >= S.steps.length) return;
    var t = S.steps[i]; S.steps[i] = S.steps[j]; S.steps[j] = t;
    save(); emit();
  }
  function insertAt(i, step) {
    i = Math.max(0, Math.min(i, S.steps.length));
    S.source = 'local';
    S.steps.splice(i, 0, step);
    if (S.idx > i) S.idx++;
    save(); log('추가: ' + (step.text || step.sel).slice(0, 24) + ' (' + (i + 1) + '번째)');
  }
  function setStep(i, patch) {
    if (!S.steps[i]) return;
    S.source = 'local';
    for (var k in patch) S.steps[i][k] = patch[k];
    save(); emit();
  }
  function importJson(text) {
    var d = JSON.parse(text);
    var arr = Array.isArray(d) ? d : d.steps;
    if (!Array.isArray(arr)) throw new Error('steps 배열이 없습니다');
    for (var i = 0; i < arr.length; i++) {
      if (!arr[i] || (!arr[i].sel && !arr[i].text)) throw new Error((i + 1) + '번째에 sel/text 가 없습니다');
    }
    S.steps = arr; S.idx = 0; S.source = 'local'; save();
    log('불러오기 ' + arr.length + '단계');
  }

  /* 재생 중 같은 단계에서 이만큼(ms) 막히면 예상 밖 모달일 가능성이 높다고 보고
   * autoconfirm.js 가 다시 끼어들도록 풀어준다 (정상 진행 중엔 계속 손을 떼서
   * 같은 버튼을 두 엔진이 동시에 누르는 경합을 피한다). */
  function stalledMs() { return waitingSince ? (Date.now() - waitingSince) : 0; }

  var API = {
    record: record, stop: stopRec, play: play, pause: pause,
    armForReload: armForReload,
    /* 달력 건너뛰기는 '조회 페이지에서 하는 첫 단계' 로 들어간다. 단계마다 녹화된
     * url 이 있으므로 추측할 필요가 없다. 없으면 -1 (건너뛰기 불가). */
    departureStep: function () {
      for (var i = 0; i < S.steps.length; i++) {
        if (U.onDeparture(S.steps[i].url || '')) return i;
      }
      return -1;
    },
    reset: reset, clear: clear, state: S, save: save,
    exportJson: exportJson, showExport: showExport, importJson: importJson,
    removeStep: removeStep, moveStep: moveStep, insertAt: insertAt, setStep: setStep,
    stalledMs: stalledMs, elapsed: elapsed,
    loadBaked: function () {
      adoptBaked('수동 요청');
      log('내장 단계 ' + S.steps.length + '개를 불러왔습니다');
    },
    bakedCount: function () { return baked().length; },
    /* 가려진 동안 tick 간격이 얼마나 벌어졌나. 60ms 근처면 이 크롬은 안 늦춘다. */
    throttle: function () { return { gapMs: throttle.hiddenGapMs, samples: throttle.samples }; },
    list: function () { console.table(S.steps); return S.steps; },
    onChange: function (fn) { listeners.push(fn); }
  };
  try { W.KE_REC = API; } catch (e) {}
  if (W !== window) { try { window.KE_REC = API; } catch (e) {} }
  if (S.playing) log('이전 재생을 이어서 진행합니다 (' + (S.idx + 1) + '/' + S.steps.length + ')');
})();

} catch (e) {
  console.error('[KE] recorder 로드 실패:', e);
}


// ---------- editor ----------
try {
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

} catch (e) {
  console.error('[KE] editor 로드 실패:', e);
}


// ---------- hud ----------
try {
/* ============================================================
 * hud.js  --  화면 우상단 컨트롤 패널
 *   - KST 서버시각 동기화 (Date 헤더 edge detection)
 *   - 오픈시각 카운트다운 -> 정시에 "새로고침 + 녹화 재생" 발사
 *   - 녹화/재생/편집 컨트롤
 *   - 재생이 멈추는 순간(결제 직전 / 전체 완료) 소리 알림
 *
 * 실제 발사 동작은 recorder.js 의 녹화 재생 하나뿐이다.
 * (예전엔 "조회 버튼 지정 + 좌석 헌팅" 경로도 있었지만, 녹화 재생이 그 일을
 *  전부 대신하므로 걷어냈다. 관련 컨트롤/상태도 같이 제거됨)
 * 클릭은 전부 recorder.js 의 녹화 재생이 담당한다 (라벨 추측 클릭은 제거됨).
 * ============================================================ */
(function () {
  'use strict';

  var W = window;
  try { if (typeof unsafeWindow !== 'undefined' && unsafeWindow) W = unsafeWindow; } catch (e) {}
  function expose(k, v) {
    try { W[k] = v; } catch (e) {}
    if (W !== window) { try { window[k] = v; } catch (e) {} }
  }

  if (W.KE_HUD || window.KE_HUD) return;

  var U0 = W.KE_UTIL || window.KE_UTIL;
  // 탭마다 따로 저장한다 (노선별 탭이 서로의 오픈시각·무장 상태를 덮어쓰지 않게)
  var LS = U0 && U0.tabKey ? U0.tabKey('ke_award_hud_v1') : 'ke_award_hud_v1';
  var S = {
    targetKst: '',        // "2026-08-22 10:00:00"
    /* 어느 화면에 서 있다가 발사할 것인가. 둘은 서 있어야 할 페이지가 다르다.
     *   'calendar'  - 달력에서 시작. 새로고침 -> 새로 열린 날짜 클릭 -> 검색 -> 조회
     *   'departure' - 조회 화면에서 시작. 목표 날짜로 맞춰두고 그 자리에서 새로고침.
     *                 달력 한 장과 그에 딸린 전환이 빠진다 */
    startAt: 'calendar',
    /* 이만큼 먼저 발사한다. 새로고침을 걸어두고 화면이 뜨기를 기다리는 시간이므로,
     * "네트워크 지연 보정" 이 아니라 "페이지가 뜨는 데 걸리는 시간" 을 넣어야 한다.
     *
     * 실측(2026-08-29, 달력 모드 3회): 새로고침 시작부터 달력 데이터
     * (POST ap/booking/avail/calendarFareMatrix)가 오기까지 3.68 / 3.92 / 4.33초.
     * 그 앞 4초는 GNB·푸터·로그인·설문 같은 부수 호출이고 달력 요청은 맨 끝이다.
     *
     * 150 이면 09:00:00 에 새로고침이 시작돼 달력은 09:00:04 에야 온다 - 4초를 그냥
     * 늦게 출발하는 셈이었다.
     *
     * 3400 은 09:00:00.3~0.9 도착을 노렸는데, 실전(2026-09-01)에서 오버헤드가
     * 그날따라 짧았는지 조회가 오픈 직전에 나가 빈 날짜를 받고 한 번 더 새로고침했다
     * (약 3.4초 손해). 그래서 2500 으로 늦춘다: 08:59:57.5 출발 -> 09:00:01.2~1.8
     * 도착. 착지가 1초쯤 늦어도, 오픈 뒤라 재고침이 없어 실질적으로 더 빠르고 안정적이다.
     * 재고침이 났는지는 완료 리포트의 '재고침 N회' 로 확인한다. */
    leadMs: 2500,
    pos: null,            // 패널 위치 {left, top}. 사이트 UI 를 가리면 옮길 수 있게
                          // 드래그해서 옮긴 자리를 기억한다
    armed: false
  };
  try { Object.assign(S, JSON.parse(localStorage.getItem(LS) || '{}')); } catch (e) {}
  function save() { try { localStorage.setItem(LS, JSON.stringify(S)); } catch (e) {} }

  var offsetMs = 0;        // 서버시각 - 로컬시각
  var externalClock = null;
  var syncQuality = '미동기화';
  var timer = null;

  function REC() { return W.KE_REC || window.KE_REC; }

  // ---- 시각 --------------------------------------------------------------
  function nowSrv() { return Date.now() + offsetMs; }

  function fmtKst(ms) {
    ms = Math.floor(ms);
    return new Intl.DateTimeFormat('ko-KR', {
      timeZone: 'Asia/Seoul', hour12: false,
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).format(new Date(ms)) + '.' + String(ms % 1000).padStart(3, '0');
  }

  // 한국은 서머타임이 없으므로 KST = UTC+9 고정
  /* 입력칸에 손으로 치다 보면 '2026-08-24-09:00:00' 처럼 날짜와 시각 사이가 하이픈이
   * 되거나 공백이 여러 개 들어간다. 그 상태로 형식 오류만 띄우면 정작 9시에 발사가
   * 안 걸린다. 숫자만 뽑아 재조립해서 웬만한 표기는 다 받아준다. */
  /** 지금 KST 의 연/월/일을 얻는다. */
  /* 발사 시각 칸을 비워두면 오늘 날짜 + 09:00 으로 채워 넣는다.
   * 연월일이 눈에 보여야 헷갈리지 않는다는 요청. 동시에 연도를 손으로 칠 일이
   * 없어지므로 여행 연도(2027)를 넣어 1년 뒤로 예약되는 사고도 막힌다. */
  function defaultTarget() {
    /* 오늘 09:00 이 이미 지났으면 내일로. 안 그러면 무장하자마자
     * "이미 지난 시각" 으로 풀려서 왜 안 되는지 헷갈린다. */
    var t = nowSrv();
    var p = kstParts(t);
    if (+p.hour * 3600 + +p.minute * 60 + +p.second >= 9 * 3600) p = kstParts(t + 86400000);
    return p.year + '-' + p.month + '-' + p.day + ' 09:00:00';
  }

  function kstParts(ms) {
    var p = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Seoul', hourCycle: 'h23',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).formatToParts(new Date(ms)).reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    return p;
  }

  /* 오픈시각 입력을 해석한다.
   *
   * "09:00" 처럼 시각만 쳐도 된다 - 오늘 그 시각, 이미 지났으면 내일로 잡는다.
   * 연도까지 치게 두면 여행 날짜(2027)를 그대로 넣는 실수가 난다. 실제로 그래서
   * 1년 뒤로 예약된 채 9시에 아무 일도 안 일어났다. 매일 09:00 에 열리는 도구라
   * 연도를 칠 일이 애초에 없다.
   *
   * 날짜까지 주면 그대로 쓴다. 하이픈/슬래시/공백 표기는 모두 받는다. */
  function targetMs() {
    if (!S.targetKst) return NaN;
    var n = String(S.targetKst).match(/\d+/g);
    if (!n) return NaN;
    var pad = function (v) { return ('0' + v).slice(-2); };
    var now = kstParts(nowSrv());
    var y, mo, d, h, mi, se;

    if (n.length <= 3) {                       // 09 / 09:00 / 09:00:00  -> 오늘(또는 내일)
      y = now.year; mo = now.month; d = now.day;
      h = n[0]; mi = n[1] || '00'; se = n[2] || '00';
    } else if (n.length === 4) {               // 08-27 09:00
      y = now.year; mo = n[0]; d = n[1]; h = n[2]; mi = n[3]; se = '00';
    } else if (n[0].length === 4) {            // 2026-08-27 09:00[:00]
      y = n[0]; mo = n[1]; d = n[2]; h = n[3]; mi = n[4]; se = n[5] || '00';
    } else {                                   // 08-27 09:00:00
      y = now.year; mo = n[0]; d = n[1]; h = n[2]; mi = n[3]; se = n[4] || '00';
    }
    var t = Date.parse(y + '-' + pad(mo) + '-' + pad(d) + 'T'
                       + pad(h) + ':' + pad(mi) + ':' + pad(se) + '+09:00');
    if (isNaN(t)) return NaN;
    // 시각만 준 경우, 오늘 그 시각이 이미 지났으면 내일로
    if (n.length <= 3 && t <= nowSrv()) t += 86400000;
    return t;
  }

  /* Date 헤더는 1초 해상도라 그대로 쓰면 ±500ms 오차가 난다.
   * 짧은 간격으로 두드려 "초가 바뀌는 순간"을 잡으면 오차를 ~50ms 로 줄일 수 있다. */
  async function sync(log) {
    if (!/^https?:$/.test(location.protocol)) {
      syncQuality = '로컬 파일 - 로컬시각 사용';
      if (log) toast(syncQuality);
      return;                       // file:// 등에서는 fetch 가 무의미
    }
    var probes = 14, gap = 120, prevSec = null, edge = null, bestRtt = 1e9, coarse = 0;
    for (var i = 0; i < probes; i++) {
      var t0 = Date.now();
      var hdr;
      try {
        var res = await fetch(location.origin + '/favicon.ico?_t=' + t0,
                             { method: 'HEAD', cache: 'no-store' });
        hdr = res.headers.get('date');
      } catch (e) { hdr = null; }
      var t1 = Date.now();
      if (!hdr) continue;
      var srv = Date.parse(hdr), rtt = t1 - t0, mid = t0 + rtt / 2;
      if (rtt < bestRtt) { bestRtt = rtt; coarse = srv + 500 - mid; }
      if (prevSec !== null && srv !== prevSec) { edge = { srv: srv, at: mid }; break; }
      prevSec = srv;
      await new Promise(function (r) { setTimeout(r, gap); });
    }
    if (edge) {
      // edge.srv 초가 시작된 시점이 edge.at 직전(<=gap) 이다
      offsetMs = Math.round(edge.srv - (edge.at - gap / 2));
      syncQuality = '정밀 (오차 ~' + Math.round(gap / 2 + bestRtt / 2) + 'ms)';
    } else if (bestRtt < 1e9) {
      offsetMs = Math.round(coarse);
      syncQuality = '개략 (오차 ~500ms)';
    } else {
      syncQuality = '동기화 실패 - 로컬시각 사용';
    }
    if (externalClock) {
      offsetMs = externalClock.offsetMs;
      syncQuality = externalClock.label;
    }
    if (log) toast('시각 동기화: ' + syncQuality + ' / offset ' + offsetMs + 'ms');
    save();
  }

  // ---- 발사 --------------------------------------------------------------
  /* 정시 동작: "새로고침이 끝난 뒤에 처음부터 재생" 을 예약하고 새로고침한다.
   * 여기서 바로 play() 를 부르면 안 된다 - recorder 의 tick 이 낡은 화면에서 1단계
   * (그날 새로 열린 날짜)를 눌러버리고, 이어지는 새로고침이 그 선택을 통째로 날린다.
   * 예약은 localStorage 에 남아 새 문서가 뜰 때 recorder 가 스스로 집어간다. */
  /* 긴 글을 보여주는 상자. 콘솔은 복사가 불편하다는 지적이 있어서
   * (실제로 이 화면들은 콘솔 복사가 막혀 있다) 선택 가능한 textarea 로 띄운다. */
  function showText(title, text) {
    var old = document.getElementById('ke-text');
    if (old) old.remove();
    var box = document.createElement('div');
    box.id = 'ke-text';
    box.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:rgba(0,0,0,.6);'
                      + 'display:flex;align-items:center;justify-content:center';
    box.innerHTML =
      '<div style="background:#fff;padding:16px;border-radius:8px;width:min(760px,92vw)">'
      + '<b style="font:13px sans-serif"></b>'
      + '<textarea style="width:100%;height:55vh;margin-top:8px;font:11px Consolas,monospace"></textarea>'
      + '<button style="margin-top:8px;padding:6px 12px">닫기</button></div>';
    box.querySelector('b').textContent = title;
    var ta = box.querySelector('textarea');
    ta.value = text;
    box.querySelector('button').onclick = function () { box.remove(); };
    box.onclick = function (e) { if (e.target === box) box.remove(); };
    document.documentElement.appendChild(box);
    try { ta.focus(); ta.select(); } catch (e) {}
  }

  // ---- 달력 건너뛰기 -----------------------------------------------------
  /* 실측 27.7초 중 절반 이상이 페이지가 그려지기를 기다린 시간이었다. 폴링을 조여봐야
   * 몇 밀리초라, 남은 방법은 페이지를 덜 거치는 것뿐이다. 달력에서 날짜를 고르고
   * 검색하는 두 단계와 그에 딸린 페이지 전환을 통째로 건너뛴다.
   *
   * 주소 형식은 추측하지 않는다. 지난 번에 실제로 지나간 조회 페이지 주소를 붙잡아
   * 두고 그것을 다시 쓴다. 목표 날짜가 그때와 다르면 '가는 날' 자리만 고친다 -
   * 왕복이면 오는 날도 주소에 들어 있어서 아무거나 바꾸면 안 된다. */

  /** 지금 바로 시작이 가능한가. {inPlace|url, from, why}
   *
   * 원래는 조회 주소에 날짜를 박아 밖에서 뛰어들려고 했는데, 그 주소는
   * /booking/select-award-flight/departure 뿐이고 물음표 뒤가 비어 있다. 게다가
   * 응답에 jsessionId 와 pageTicket 이 있다 - 서버가 흐름 순서를 강제한다는 뜻이라,
   * 중간 페이지로 뛰어드는 것 자체를 막는다.
   *
   * 대신 이미 그 페이지에 서 있다가 그 자리에서 새로고침한다. 세션 안에 있으므로
   * 티켓도 그대로고, 주소에 날짜가 없어도 상관없다. 조회 페이지는 자기 화면에
   * 7일치 날짜 띠를 들고 있어서 새로 열린 날도 거기 있다. */
  function skipPlan(R) {
    var U2 = W.KE_UTIL || window.KE_UTIL;
    var P = W.KE_PROBE || window.KE_PROBE;
    if (!R || !U2) return { why: '준비 안 됨' };
    var st = R.state;
    if (!st.expectDate) {
      return { why: '목표 날짜를 넣어야 합니다 (달력을 건너뛰면 날짜를 확인할 수 없습니다)' };
    }
    var from = R.departureStep();
    if (from < 0) return { why: '조회 페이지에서 시작하는 단계가 없습니다' };
    if (!U2.onDeparture()) {
      return { why: '지금 조회 페이지가 아닙니다 - 조회 화면을 띄워두고 대기하세요' };
    }
    /* 이 화면이 어느 날을 조회 중인가. 근거가 둘이다:
     *   - 검색 위젯의 날짜 입력칸 (좌석이 없어도 있다)
     *   - 서버 응답의 departureDate (좌석이 있을 때만 나온다)
     * 09:00 직전에 목표 날짜로 맞춰두고 기다리는 상황에서는 좌석이 아직 없으므로
     * 앞의 것만 있다. 둘 다 있는데 서로 다르면 화면을 못 믿는다는 뜻이니 멈춘다. */
    var byApi = (P && P.shownDate && P.shownDate()) || null;
    var byUi = U2.searchedDate();
    if (byApi && byUi && byApi !== byUi) {
      return { why: '화면(' + byUi + ')과 서버 응답(' + byApi + ')의 날짜가 다릅니다'
                    + ' - 화면이 낡았을 수 있어 건너뛰지 않습니다' };
    }
    var seen = byUi || byApi;
    if (!seen) return { why: '이 화면이 어느 날짜인지 아직 확인되지 않았습니다' };

    /* 띠에 있어도 지금 보고 있는 날이 아니면 눌러서 바꿔야 하는데, 그 날짜 띠를
     * 누르는 단계가 아직 없다. 그대로 진행하면 엉뚱한 날 좌석을 누른다.
     * 3초 벌자고 낼 값이 아니므로, 그 단계가 생기기 전까지는 달력으로 간다. */
    /* 09:00 에 새로 열리는 날짜는 미리 맞춰둘 수 없다 - 그 시각에야 예약 가능 창에
     * 들어오기 때문이다. 그래서 화면 날짜가 목표와 다른 것이 정상이고, 새로고침한
     * 뒤 화면의 달력에서 목표 날짜를 눌러 맞춘다(fixDate).
     *
     * 화면을 믿지 않는다 - 맞췄는지는 서버 응답으로 확인한다. */
    if (seen !== st.expectDate) {
      return { inPlace: true, from: from, seen: seen, fix: st.expectDate, why: '' };
    }
    return { inPlace: true, from: from, seen: seen, why: '' };
  }

  /* 고른 모드대로 지금 서 있는지 알려준다. 09:00 에 엉뚱한 화면에 서 있었다는 걸
   * 그때 알면 늦다. 색으로도 구분해서 패널만 흘끗 봐도 알게 한다. */
  function startStatus(R) {
    var U2 = W.KE_UTIL || window.KE_UTIL;
    if (S.startAt !== 'departure') {
      var onCal = R && R.state.steps.length && R.state.steps[0].url
                  && location.pathname.indexOf(R.state.steps[0].url) >= 0;
      return onCal
        ? { ok: true, text: '준비됨 - 이 달력에서 새로고침, 1단계부터' }
        : { ok: false, text: '지금 달력 화면이 아닙니다 - 달력을 띄워두고 대기하세요' };
    }
    var p = skipPlan(R);
    return p.inPlace
      ? { ok: true, text: '준비됨 - 이 조회 화면(' + p.seen + ')에서 새로고침'
                          + (p.fix ? ' → 달력에서 ' + p.fix + ' 선택' : '')
                          + ' → ' + (p.from + 1) + '단계부터' }
      : { ok: false, text: p.why };
  }

  /* 이 모드에서 재생은 몇 단계부터 시작해야 하는가.
   *
   * 예전에는 이 판단이 fire() 안에만 있었다. 그래서 조회 화면 모드로 해놓고 ▶ 재생 을
   * 누르면 1단계(달력 날짜 클릭)부터 돌아, 달력에도 없는 셀을 20초 동안 찾다 멈췄다.
   * 사람이 시험해보는 가장 자연스러운 경로가 바로 그것이다. 한 곳에서 정한다. */
  function startPlan(R) {
    if (S.startAt !== 'departure') return { from: 0, why: '' };
    var p = skipPlan(R);
    /* 필요한 값을 골라 담지 말고 그대로 넘긴다. 예전에는 여기서 fix(맞춰야 할
     * 날짜)를 떨어뜨려, 패널은 "달력에서 08-22 선택" 이라고 하는데 정작 발사하면
     * 날짜를 안 바꾸고 어제 날짜로 좌석을 눌렀다. */
    return p.inPlace ? p : { from: 0, why: p.why };
  }

  function fire(reason) {
    var R = REC();
    var U2 = W.KE_UTIL || window.KE_UTIL;
    /* 로그아웃 상태로 쏘면 로그인 화면만 붙잡고 헛돈다. 매일 자동으로 돌릴 때
     * 밤새 세션이 풀리는 게 가장 흔한 실패라, 쏘기 전에 확인하고 크게 알린다. */
    if (U2 && U2.loggedOut && U2.loggedOut()) {
      S.armed = false;
      keepAwake(false);
      save();
      render();
      notify('로그인이 풀려 있습니다 - 로그인하고 다시 대기 시작하세요 (발사 취소)', false);
      return false;
    }
    if (!R || !R.state.steps.length) {
      toast('녹화된 단계가 없습니다 - 먼저 ● 녹화 하세요', true);
      return false;
    }
    /* 달력 건너뛰기가 켜져 있고 조건이 맞으면 새로고침 대신 조회 페이지로 바로 간다.
     * 조건이 안 맞으면 이유를 알리고 원래대로 달력부터 - 조용히 건너뛰지 않는다. */
    /* 조회 화면 모드인데 조건이 안 맞으면 조용히 넘어가지 않는다. 이유를 말하고
     * 달력 경로로 간다 - 오늘 실측만큼 걸릴 뿐, 놓치지는 않는다. */
    var plan = startPlan(R);
    if (!R.armForReload(plan.from, plan.fix)) return false;
    S.lastFire = {localAt: Date.now(), serverAt: nowSrv(), reason: reason,
      targetAt: targetMs(), leadMs: S.leadMs, startAt: S.startAt};
    /* 이후 흐름은 recorder 가 몬다. HUD 는 무장을 풀어 카운트다운을 멈춘다.
     *
     * 소리는 끄지 않는다. 예전에는 여기서 껐는데, 경쟁이 벌어지는 것은 발사 '뒤' 다.
     * 다만 이 문서가 새로고침되면 AudioContext 도 같이 죽고, 새 문서에서는 사용자
     * 조작 없이 다시 소리를 낼 수 없다(크롬 자동재생 정책). 그래서 재생 구간은
     * 결국 창을 최소화하면 느려진다 - 그 사실을 숨기지 말고 알린다. */
    S.armed = false;
    save();
    if (document.hidden) {
      /* 새로고침 뒤에는 소리를 다시 낼 수 없어 재생이 느려진다. 막을 수는 없어도
       * 왜 느린지는 알려야 한다 - 소리로도 부른다. */
      notify('창이 가려져 있습니다 - 재생이 느려집니다. 이 창을 보이게 두세요', false);
    }
    if (plan.inPlace) {
      /* 같은 주소로 새로고침한다. 페이지 한 장(달력)과 그에 딸린 전환이 통째로 빠진다. */
      toast('발사 (' + reason + ') - 조회 화면에서 그대로 새로고침 @ ' + fmtKst(nowSrv()));
      setTimeout(function () { location.reload(); }, 0);
      return true;
    }
    if (S.startAt === 'departure') {
      toast('조회 화면에서 시작할 수 없습니다 (' + plan.why + ') - 달력부터 진행합니다', true);
    }
    /* 달력 모드인데 달력 화면이 아니면, 그 자리에서 새로고침해봐야 1단계(달력 날짜)
     * 를 영영 못 찾는다. 지나가며 붙잡아둔 달력 주소가 있으면 그리로 간다. */
    var base = R.state.baseLink;
    if (S.startAt !== 'departure' && base
        && location.pathname.indexOf(R.state.steps[0].url || '') < 0) {
      toast('발사 (' + reason + ') - 달력으로 이동 후 재생 @ ' + fmtKst(nowSrv()));
      setTimeout(function () { location.href = base; }, 0);
      return true;
    }
    toast('발사 (' + reason + ') - 새로고침 후 재생 @ ' + fmtKst(nowSrv()));
    setTimeout(function () { location.reload(); }, 0);
    return true;
  }

  // ---- 알림 --------------------------------------------------------------
  /* 재생이 멈추는 순간 = 사람이 개입해야 하는 순간.
   * 끝까지 간 것과 중간에 막힌 것은 대응이 전혀 다르므로 소리와 제목을 다르게 낸다.
   * (같은 소리를 내면 막혀서 멈춘 걸 "완료" 로 오해하게 된다) */
  function beep(freqs, dur) {
    try {
      var ac = new (window.AudioContext || window.webkitAudioContext)();
      freqs.forEach(function (f, i) {
        var o = ac.createOscillator(), g = ac.createGain();
        o.connect(g); g.connect(ac.destination);
        o.frequency.value = f; g.gain.value = 0.2;
        o.start(ac.currentTime + i * dur); o.stop(ac.currentTime + i * dur + dur * 0.8);
      });
    } catch (e) {}
  }

  /* 무장 중에는 탭이 소리를 내게 해서 백그라운드 스로틀링을 피한다.
   *
   * 크롬은 숨겨진 탭의 타이머를 1초로 묶고, 5분 넘게 숨어 있으면 "집중 스로틀링" 으로
   * 1분에 한 번꼴까지 늦춘다. 11:15 발사인데 11:00 에 무장하고 다른 창을 보고 있으면
   * 최악의 경우 1분 늦는다 - 좌석 경쟁에서는 끝난 얘기다.
   * 소리를 내는 탭은 이 집중 스로틀링에서 면제된다. 완전한 무음은 "소리 없음" 으로
   * 취급될 수 있어 아주 작은 소리를 낸다(사실상 안 들린다).
   * 무장을 푸는 순간 멈춘다. */
  var keepCtx = null, keepOsc = null;
  function keepAwake(on) {
    try {
      if (on) {
        if (keepOsc) return;
        keepCtx = keepCtx || new (window.AudioContext || window.webkitAudioContext)();
        if (keepCtx.state === 'suspended') keepCtx.resume();
        keepOsc = keepCtx.createOscillator();
        var g = keepCtx.createGain();
        g.gain.value = 0.0008;          // 안 들리지만 "무음" 은 아닌 크기
        keepOsc.frequency.value = 40;   // 낮은 음 - 스피커에서 사실상 안 나온다
        keepOsc.connect(g); g.connect(keepCtx.destination);
        keepOsc.start();
      } else if (keepOsc) {
        try { keepOsc.stop(); } catch (e) {}
        keepOsc.disconnect();
        keepOsc = null;
      }
    } catch (e) { keepOsc = null; }
  }

  /* 재생이 끝났으면 더 이상 소리를 낼 이유가 없다. */
  function stopAwakeWhenIdle() {
    var R4 = REC();
    if (keepOsc && !S.armed && !(R4 && R4.state.playing)) keepAwake(false);
  }

  function notify(msg, ok) {
    setStatus(msg || '재생이 멈췄습니다');
    if (ok) beep([880, 1175, 1568], 0.16);        // 올라가는 3음 = 끝까지 갔음
    else    beep([440, 330, 247, 196], 0.22);     // 내려가는 4음 = 막혔으니 봐야 함
    var tag = ok ? '★완료★ ' : '⚠멈춤⚠ ';
    document.title = tag + document.title.replace(/^(★완료★|⚠멈춤⚠)\s*/, '');
  }

  // ---- UI ----------------------------------------------------------------
  var root, statusEl, clockEl, cdEl, toastEl;

  // 패널에는 항상 표시. 콘솔은 경고/오류만 (평상시 로그로 콘솔을 채우지 않는다)
  function toast(msg, warn) {
    if (!toastEl) return;
    toastEl.textContent = msg;
    toastEl.style.color = warn ? '#c00' : '#060';
    if (warn) console.warn('[KE_HUD] ' + msg);
  }
  function setStatus(msg) { if (statusEl) statusEl.textContent = msg; }

  var seenStamp = -1;
  function tick() {
    if (!clockEl) return;
    /* '바로 시작' 안내와 좌석 수는 서버 응답에서 온다. 응답은 우리가 그린 뒤에
     * 도착하므로, 새 기록이 생겼을 때 다시 그려야 화면이 사실과 맞는다. */
    try {
      var P = W.KE_PROBE || window.KE_PROBE;
      if (P && P.stamp() !== seenStamp) { seenStamp = P.stamp(); renderRec(); }
    } catch (e) {}
    var n = nowSrv();
    clockEl.textContent = fmtKst(n) + '  (' + syncQuality + ')';
    var T = targetMs();
    if (!isNaN(T)) {
      var d = T - S.leadMs - n;
      if (d > 0) {
        var s = Math.floor(d / 1000);
        /* 하루가 넘으면 시:분:초로 보여줘봐야 "8759:59:52" 같은 숫자라 눈에 안 들어온다.
         * 실제로 여행 날짜(2027)를 오픈시각에 넣어 1년 뒤로 예약된 걸 모르고 9시를
         * 놓친 적이 있다. 날짜 단위로 크게 띄워 바로 알아채게 한다. */
        if (d > 86400000) {
          cdEl.textContent = '⚠ ' + Math.floor(d / 86400000) + '일 뒤';
          cdEl.style.color = '#c60';
        } else {
          cdEl.textContent = 'T-' + String(Math.floor(s / 3600)).padStart(2, '0') + ':' +
            String(Math.floor(s / 60) % 60).padStart(2, '0') + ':' +
            String(s % 60).padStart(2, '0') + '.' + String(Math.floor(d) % 1000).padStart(3, '0');
          cdEl.style.color = d < 10000 ? '#c00' : '#333';
        }
      } else {
        /* 발사 시각이 지난 뒤.
         *
         * 무장도 재생도 아니면 올라가는 숫자는 아무 의미가 없다. 끝난 뒤에도 계속
         * 세면 "이번에 몇 초 걸렸나" 를 덮어버린다(실측: 26초에 끝났는데 화면에는
         * '발사 시각 81초 지남'). 대신 방금 실행이 몇 초 걸렸는지를 보여준다. */
        var R5 = REC();
        if (S.armed || (R5 && R5.state.playing)) {
          cdEl.textContent = 'T+' + Math.floor(-d / 1000) + 's';
        } else if (R5 && R5.state.startedAt && R5.state.endedAt) {
          cdEl.textContent = '지난 실행 '
            + ((R5.state.endedAt - R5.state.startedAt) / 1000).toFixed(1) + '초';
        } else {
          cdEl.textContent = '대기 중 아님';
        }
        cdEl.style.color = '#c00';
      }
      /* 안전망: 카운트다운 루프에서도 발사한다.
       * schedule() 은 setTimeout 을 최대 60초짜리로 걸어두는데, 탭이 백그라운드면
       * 크롬이 그걸 1분에 한 번꼴로 늦춘다. 그러면 정시를 한참 넘겨서야 깨어난다.
       * 여기서 한 번 더 보면, 타이머가 늦어도 tick 이 도는 순간 바로 쏜다.
       * fire() 는 무장을 풀고 새로고침하므로 두 번 쏘지 않는다. */
      if (S.armed && d <= 0) fire('정시');
    } else {
      cdEl.textContent = '발사 시각 미설정';
    }

    /* 크롬은 안 보이는 탭의 타이머를 늦춘다. 5분이 지나면 1분에 한 번까지 떨어진다
     * (intensive throttling). 소리를 내는 동안은 예외지만, 새로고침하면 그 소리가
     * 끊기고 새 문서에서는 사용자 조작 없이 다시 낼 수 없다.
     *
     * 그래서 '무장 중' 과 '재생 중' 의 사정이 다르다:
     *   무장 중  - 대기 시작을 누른 그 문서 그대로다. 소리가 살아 있어 정시에 쏜다
     *   재생 중  - 새로고침 뒤라 소리가 없다. 창을 최소화하면 사실상 멈춘다
     * 실측(2026-08-28): 최소화하면 멈췄다가 그 창으로 돌아가야 다시 움직였다. */
    var R3 = REC();
    var busy = R3 && R3.state.playing;
    stopAwakeWhenIdle();
    if ((S.armed || busy) && document.hidden) {
      setStatus(busy
        ? '⚠ 창이 가려져 있어 재생이 멈춰 있습니다 - 이 창을 보이게 두세요 (최소화 금지)'
        : keepOsc
          ? '탭이 백그라운드지만 소리로 스로틀링을 막는 중 - 그래도 앞에 두는 게 가장 확실합니다'
          : '⚠ 탭이 백그라운드입니다 - 타이머가 늦어질 수 있으니 이 탭을 앞에 두세요');
    }
  }

  function schedule() {
    if (timer) { clearTimeout(timer); timer = null; }
    var T = targetMs();
    /* 예약에 실패하면 무장을 반드시 풀어야 한다. 안 그러면 버튼은 '■ 정지' 인데
     * 타이머는 없는 상태가 되어, 사용자가 무장된 줄 알고 기다리다 놓친다. */
    if (isNaN(T)) {
      S.armed = false; keepAwake(false); save(); render();
      toast('발사 시각 형식 오류 (예: 09:00) - 무장 해제됨', true);
      setStatus('무장 해제됨 - 발사 시각을 확인하세요');
      return;
    }
    var wait = T - S.leadMs - nowSrv();
    if (wait < 0) {
      S.armed = false; keepAwake(false); save(); render();
      toast('이미 지난 시각입니다 - 무장 해제됨', true);
      setStatus('무장 해제됨 - 발사 시각이 이미 지났습니다');
      return;
    }
    // 남은 시간이 길면 쪼개서 재계산 (setTimeout 드리프트 보정)
    (function step() {
      var left = targetMs() - S.leadMs - nowSrv();
      if (left <= 0) { fire('정시'); return; }
      timer = setTimeout(step, left > 5000 ? Math.min(left - 3000, 60000) : Math.max(left - 20, 1));
    })();
    var left = T - S.leadMs - nowSrv();
    var far = left > 86400000 ? '  ⚠ ' + Math.floor(left / 86400000) + '일 뒤입니다 - 여기는 여행일이 아니라 발사 시각입니다' : '';
    setStatus('예약 대기 중 - ' + fmtKst(T - S.leadMs) + ' 발사 예정' + far);
  }

  /** targetKst 입력칸이 쓰는 "YYYY-MM-DD HH:MM:SS" (KST) 형식으로 변환 */
  function kstInput(ms) {
    var p = new Intl.DateTimeFormat('en-CA', {
      timeZone: 'Asia/Seoul', hourCycle: 'h23',
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).formatToParts(new Date(ms)).reduce(function (a, x) { a[x.type] = x.value; return a; }, {});
    return p.year + '-' + p.month + '-' + p.day + ' ' + p.hour + ':' + p.minute + ':' + p.second;
  }

  /* 연습: 오픈시각을 몇 초 뒤로 잡고 그대로 무장한다.
   * 카운트다운 -> 정시 발사 -> 새로고침 -> 재생까지 실전과 같은 경로를 탄다. */
  function rehearse(sec) {
    var R = REC();
    if (!R || !R.state.steps.length) {
      toast('녹화된 단계가 없습니다 - 먼저 ● 녹화 하세요', true);
      return;
    }
    /* "sec 초 뒤 발사" 는 오픈시각이 아니라 실제로 새로고침이 걸리는 시각을 말한다.
     * 발사는 오픈시각보다 선발사만큼 이르므로, 그만큼 뒤로 밀어 목표를 잡아야
     * 약속한 시각에 발사된다. (선발사가 3.4초인데 이걸 빼먹으면 '10초 뒤' 가
     * 6.6초 뒤가 되고, sec 이 선발사보다 작으면 아예 무장조차 안 된다) */
    // 입력칸은 초 단위라 그냥 넣으면 밀리초가 잘려 최대 1초 일찍 발사된다. 초 경계로 올림.
    S.targetKst = kstInput(Math.ceil((nowSrv() + sec * 1000 + S.leadMs) / 1000) * 1000);
    S.armed = true;
    save(); render(); schedule();
    toast('연습: ' + sec + '초 뒤 발사합니다');
  }

  /** 녹화/재생 상태를 패널에 반영 + 재생이 멈추는 순간 알림 */
  var wasPlaying = false;
  function renderRec() {
    if (!root) return;
    var R = REC();
    if (!R) return;
    var st = R.state;
    var lab = root.querySelector('#ke-step-label');
    var rec = root.querySelector('#ke-rec');
    var play = root.querySelector('#ke-play');
    var msg = root.querySelector('#ke-rec-msg');
    /* '바로 시작' 이 되는지는 목표 날짜와 저장된 주소에 달렸는데, 둘 다 recorder 쪽
     * 상태다. 여기서 같이 갱신하지 않으면 목표 날짜를 고쳐도 안내가 옛날 것으로
     * 남아, 안 되는 줄 알고 있다가 정작 되거나 그 반대가 된다. */
    /* 창이 좁으면 사이트가 모바일 화면으로 바뀐다. 그러면 녹화해둔 셀렉터와 라벨이
     * 통째로 달라져서, 멀쩡히 보이는 버튼을 두고 "요소를 못 찾음" 으로 멈춘다.
     * 09:00 에 그걸 알면 늦으니 미리 크게 알린다. */
    var wEl = root.querySelector('#ke-width');
    if (wEl) {
      var w = window.innerWidth || 0;
      var need = (R && R.state.recordedWidth) || 1200;
      wEl.textContent = (w && w < need * 0.85)
        ? '⚠ 창이 좁습니다 (' + w + 'px) - 모바일 화면으로 바뀌면 단계를 못 찾습니다.'
          + ' 창을 ' + need + 'px 이상으로 넓히세요'
        : '';
    }

    /* 이 크롬이 가려진 창을 늦추는지 직접 재서 보여준다. 말로만 "최소화하지 마세요"
     * 라고 하면 확인할 방법이 없다. */
    var tEl = root.querySelector('#ke-throttle');
    if (tEl && R && R.throttle) {
      var th = R.throttle();
      if (!th.samples) {
        tEl.textContent = '';
      } else if (th.gapMs > 400) {
        tEl.style.color = '#c00';
        tEl.textContent = '⚠ 이 크롬은 가려진 창을 늦춥니다 (재보니 ' + th.gapMs
                        + 'ms 까지 벌어짐) - 창을 보이게 두거나 스로틀링 끈 크롬을 쓰세요';
      } else {
        tEl.style.color = '#2a7';
        tEl.textContent = '✔ 가려져도 안 늦는 크롬입니다 (최대 ' + th.gapMs + 'ms)';
      }
    }

    var pr = root.querySelector('#ke-probe');
    var P = W.KE_PROBE || window.KE_PROBE;
    if (pr && P) {
      var txt = P.summary();
      pr.textContent = txt;
      /* 매진은 조용히 지나가면 안 된다. 그 등급이 애초에 0석이었는지 누가 채간
       * 것인지는 기록으로 따지되, 지금 0이라는 사실은 바로 보여야 한다. */
      pr.style.color = /매진/.test(txt) ? '#c00' : '#888';
    }
    var why = root.querySelector('#ke-skipcal-why');
    if (why) {
      var st2 = startStatus(R);
      why.textContent = (st2.ok ? '✔ ' : '✖ ') + st2.text;
      why.style.color = st2.ok ? '#2a7' : '#c00';
    }
    if (lab) {
      var el = R.elapsed ? R.elapsed() : 0;
      /* 어디서 온 단계인지 항상 보이게 한다. 브라우저에 남은 옛날 녹화가 도는데
       * 그걸 모르고 실전에 들어가는 게 제일 위험하다. */
      var src = st.source === 'local' ? '직접 녹화' : '내장';
      lab.textContent = st.steps.length + '단계 (' + src + ')'
        + (st.playing ? ' - 재생 중 ' + (st.idx + 1) + '/' + st.steps.length
                      : (st.idx ? ' (' + st.idx + '까지 진행됨)' : ''))
        + (el ? '  ' + el.toFixed(1) + 's' : '');
      lab.style.color = st.source === 'local' ? '#a0f' : '#0b4da2';
    }
    if (rec) {
      rec.textContent = st.recording ? '■ 녹화중지' : '● 녹화';
      rec.style.background = st.recording ? '#c33' : '#a0f';
    }
    if (play) {
      play.textContent = st.playing ? '■ 정지' : '▶ 재생';
      play.style.background = st.playing ? '#c33' : '#06c';
    }
    if (msg) msg.textContent = st.message || '';

    /* 재생 중이다가 멈췄으면 사람을 부른다.
     * "끝까지 감"(결제 단계 도달 / 전 단계 완료) 과 "중간에 막힘"(요소 못 찾음 /
     * 건너뜀 / 목표 날짜 불일치) 을 구분해서 알린다. */
    if (wasPlaying && !st.playing) {
      var m = st.message || '';
      // 사용자가 직접 정지한 건 알릴 필요 없다
      if (!/사용자 중지/.test(m)) {
        /* 건너뜀 여부는 메시지 문구가 아니라 숫자로 판정한다.
         * 문구 매칭에 기대면 '건너뛰었습니다' -> '건너뜀' 처럼 표현만 바뀌어도
         * 조용히 ★완료★ 로 잘못 보고하게 된다. */
        var ok = st.steps.length > 0 && st.idx >= st.steps.length
                 && !st.problem && !/못 찾|다릅니다/.test(m);
        notify(m, ok);
      }
    }
    wasPlaying = st.playing;
  }

  function render() {
    if (!root) return;
    renderRec();
    root.querySelector('#ke-target').value = S.targetKst;
    root.querySelector('#ke-lead').value = S.leadMs;
    var R2 = REC();
    if (R2) {
      root.querySelector('#ke-cabin').value = R2.state.cabin || '일반석';
      root.querySelector('#ke-expect').value = R2.state.expectDate || '';
      root.querySelector('#ke-allowpay').checked = !!R2.state.allowPay;
      root.querySelector('#ke-startat').value = S.startAt || 'calendar';
    }
    var arm = root.querySelector('#ke-arm');
    arm.textContent = S.armed ? '■ 정지' : '▶ 대기 시작';
    arm.style.background = S.armed ? '#c33' : '#2a7';
  }

  function build() {
    root = document.createElement('div');
    root.id = 'ke-hud';
    root.innerHTML =
      '<style>' +
      '#ke-hud{position:fixed;top:10px;right:10px;z-index:2147483647;width:270px;' +
      'font:12px/1.5 -apple-system,"Malgun Gothic",sans-serif;background:#fff;color:#222;' +
      'border:2px solid #0b4da2;border-radius:8px;box-shadow:0 4px 16px rgba(0,0,0,.25);padding:10px}' +
      '#ke-hud h4{margin:0 0 6px;font-size:13px;color:#0b4da2}' +
      '#ke-hud label{display:block;margin:5px 0 1px;font-size:11px;color:#666}' +
      '#ke-hud input{width:100%;box-sizing:border-box;padding:3px 5px;border:1px solid #bbb;border-radius:3px;font:inherit}' +
      '#ke-hud button{cursor:pointer;border:0;border-radius:4px;color:#fff;padding:5px 8px;font:inherit;margin-top:6px}' +
      '#ke-clock{font-family:Consolas,monospace;font-size:11px;color:#0b4da2}' +
      '#ke-cd{font-family:Consolas,monospace;font-size:19px;font-weight:700;text-align:center;margin:4px 0;white-space:nowrap;font-variant-numeric:tabular-nums}' +
      '#ke-status{font-size:11px;color:#444;min-height:15px;margin-top:4px}' +
      '#ke-toast{font-size:11px;min-height:15px}' +
      '#ke-hud .row{display:flex;gap:5px}#ke-hud .row>*{flex:1}' +
      '</style>' +
      '<h4>KE 마일리지 예매 보조 <span id="ke-ver"></span>' +
      '<span style="float:right;cursor:pointer" id="ke-min">_</span></h4>' +
      '<div id="ke-body">' +
      '<div id="ke-clock">--</div><div id="ke-cd">--</div>' +
      '<label><b>발사 시각</b> - 매크로가 움직일 시각 (매일 09:00)</label>' +
      '<input id="ke-target" placeholder="09:00">' +
      '<label>선발사(ms) <span style="color:#999">- 달력 기본 2500 · 09시 기준 08:59:57.500</span></label>' +
      '<input id="ke-lead" type="number">' +
      '<button id="ke-sync" style="background:#666;width:100%">시각 동기</button>' +
      '<hr style="border:0;border-top:1px solid #ddd;margin:8px 0">' +
      '<label>좌석 등급</label>' +
      '<select id="ke-cabin" style="width:100%;box-sizing:border-box;padding:3px 5px;' +
      'border:1px solid #bbb;border-radius:3px;font:inherit">' +
      '<option value="일반석">일반석 (연습용)</option>' +
      '<option value="프레스티지">프레스티지 (실전)</option>' +
      '<option value="프리미엄">프리미엄</option>' +
      '<option value="일등석">일등석</option>' +
      '</select>' +
      '<label><b>목표 날짜</b> - 달력에서 고를 여행일 <span style="color:#999">(비우면 검사 안 함)</span></label>' +
      '<input id="ke-expect" placeholder="08-21">' +
      '<label style="display:flex;align-items:center;gap:5px;margin-top:6px;color:#c00">' +
      '<input type="checkbox" id="ke-allowpay" style="width:auto">' +
      '결제하기까지 자동 (결제창 열림)</label>' +
      '<label><b>시작 화면</b> - 발사할 때 이 화면에 서 있어야 합니다</label>' +
      '<select id="ke-startat">' +
      '<option value="calendar">달력 (기본, 검증됨) - 새로 열린 날짜를 찾아서 조회</option>' +
      '<option value="departure">조회 화면 (실험 중) - 달력 한 장을 건너뜀</option>' +
      '</select>' +
      '<div id="ke-skipcal-why" style="margin:2px 0 0 2px"></div>' +
      '<div id="ke-width" style="color:#c00;margin:2px 0 0 2px"></div>' +
      '<div id="ke-throttle" style="margin:2px 0 0 2px"></div>' +
      '<div style="display:flex;align-items:center;gap:6px;margin-top:4px">' +
      '<span id="ke-probe" style="color:#888;flex:1"></span>' +
      '<button id="ke-probe-dump" style="padding:2px 6px;font-size:11px">조회 응답</button></div>' +
      '<hr style="border:0;border-top:1px solid #ddd;margin:8px 0">' +
      '<label>예매 단계: <b id="ke-step-label">0단계</b></label>' +
      '<div class="row">' +
      '<button id="ke-rec" style="background:#a0f">● 녹화</button>' +
      '<button id="ke-play" style="background:#06c">▶ 재생</button>' +
      '<button id="ke-clear" style="background:#888">삭제</button></div>' +
      '<button id="ke-edit" style="background:#06c;width:100%">단계 편집</button>' +
      '<button id="ke-export" style="background:#555;width:100%">내보내기 (steps.json 용)</button>' +
      '<div id="ke-rec-msg" style="font-size:11px;color:#606"></div>' +
      '<hr style="border:0;border-top:1px solid #ddd;margin:8px 0">' +
      '<button id="ke-rehearse" style="background:#c80;width:100%">연습 (10초 뒤 발사)</button>' +
      '<button id="ke-arm" style="background:#2a7;width:100%">▶ 대기 시작</button>' +
      '<div id="ke-status"></div><div id="ke-toast"></div>' +
      '<div style="font-size:10px;color:#888;margin-top:4px">제목을 끌어 옮길 수 있음 · Alt+P 로 셀렉터 집기</div>' +
      '</div>';
    if (S.pos) {
      root.style.left = S.pos.left + 'px';
      root.style.top = S.pos.top + 'px';
      root.style.right = 'auto';
    }
    makeDraggable(root, root.querySelector('h4'));

    var B = W.KE_BUILD || window.KE_BUILD;
    var ver = root.querySelector('#ke-ver');
    /* 어떤 빌드가 로드됐는지 한눈에 보이게 한다. 버전을 안 올려서 이전 스크립트로
     * 계속 테스트한 적이 있다 - 그때 이게 있었으면 바로 알았다. */
    if (ver && B) {
      ver.textContent = 'v' + B.version;
      ver.style.cssText = 'font-size:10px;font-weight:400;color:'
        + (/dirty|nogit/.test(B.version) ? '#c60' : '#888');
      ver.title = 'build ' + B.hash;
    }
    document.documentElement.appendChild(root);

    statusEl = root.querySelector('#ke-status');
    clockEl = root.querySelector('#ke-clock');
    cdEl = root.querySelector('#ke-cd');
    toastEl = root.querySelector('#ke-toast');

    root.querySelector('#ke-min').onclick = function () {
      var b = root.querySelector('#ke-body');
      b.style.display = b.style.display === 'none' ? '' : 'none';
    };
    root.querySelector('#ke-sync').onclick = function () { sync(true); };
    root.querySelector('#ke-rehearse').onclick = function () { rehearse(10); };

    var R = REC();
    if (R) {
      root.querySelector('#ke-rec').onclick = function () {
        R.state.recording ? R.stop() : R.record();
        renderRec();
      };
      root.querySelector('#ke-play').onclick = function () {
        if (R.state.playing) { R.pause('사용자 중지'); renderRec(); return; }
        var plan = startPlan(R);
        if (S.startAt === 'departure' && !plan.inPlace) {
          /* 여기서 그냥 1단계부터 돌리면 달력에도 없는 셀을 20초 동안 찾다 멈춘다.
           * 왜 못 하는지 말해주는 편이 훨씬 낫다. */
          toast('조회 화면에서 시작할 수 없습니다: ' + plan.why, true);
          renderRec();
          return;
        }
        // 처음부터 다시 할지, 끊긴 데서 이어갈지
        if (R.state.idx > plan.from && R.state.idx < R.state.steps.length) {
          if (confirm(R.state.idx + '단계까지 진행돼 있습니다.\n확인=이어서, 취소=처음부터')) {
            /* 이어서 */
          } else { R.reset(plan.from, plan.fix); }
        } else { R.reset(plan.from, plan.fix); }
        R.play();
        if (plan.from > 0) toast((plan.from + 1) + '단계부터 재생합니다 (조회 화면 모드)');
        renderRec();
      };
      root.querySelector('#ke-edit').onclick = function () {
        var E = W.KE_EDIT || window.KE_EDIT;
        if (E) E.open(); else toast('편집기를 불러오지 못했습니다', true);
      };
      root.querySelector('#ke-export').onclick = function () { R.showExport(); };
      root.querySelector('#ke-clear').onclick = function () {
        if (confirm('녹화된 단계를 모두 지울까요?')) { R.clear(); renderRec(); }
      };
      root.querySelector('#ke-cabin').onchange = function (e) {
        R.state.cabin = e.target.value; R.save();
        toast('좌석 등급: ' + e.target.value);
      };
      root.querySelector('#ke-expect').onchange = function (e) {
        R.state.expectDate = e.target.value.trim(); R.save();
        /* save() 는 localStorage 에 쓰기만 하고 패널에 알리지 않는다. '바로 시작'
         * 안내가 목표 날짜에 달려 있으므로 여기서 직접 다시 그린다. */
        renderRec();
        toast(R.state.expectDate
          ? '목표 날짜 ' + R.state.expectDate + ' - 다르면 멈춥니다'
          : '목표 날짜 검사 끔');
      };
      /* 기본은 켜짐 (사용자 요청). 켜면 결제하기까지 눌러 결제창(네이버페이 등)을 띄운다.
       * 결제창에서 다시 본인 인증이 필요하므로 여기서 바로 돈이 빠지지는 않지만,
       * 되돌리기 어려운 지점이라 매번 눈에 보이게 체크하도록 둔다. */
      root.querySelector('#ke-allowpay').onchange = function (e) {
        R.state.allowPay = !!e.target.checked; R.save();
        toast(R.state.allowPay
          ? '결제하기까지 자동 - 결제창이 열립니다'
          : '결제 직전에서 멈춥니다 (기본)', R.state.allowPay);
      };
      /* 좌석이 언제 매진으로 바뀌는지는 서버만 알지만, 화면이 잔여석을 그릴 때 쓰는
       * 응답은 우리도 볼 수 있다. 09:00 에 사람이 개발자도구를 붙잡고 있을 수는
       * 없으므로 자동으로 기록해두고 여기서 꺼내 본다. */
      root.querySelector('#ke-probe-dump').onclick = function () {
        var P = W.KE_PROBE || window.KE_PROBE;
        showText('조회 응답 기록 (매진 판정이 어디서 오는지 확인용)',
                 P ? (P.dump() || '아직 기록된 조회 응답이 없습니다') : 'probe 없음');
      };
      root.querySelector('#ke-startat').onchange = function (e) {
        S.startAt = e.target.value; save(); render();
        toast(startStatus(R).text, !startStatus(R).ok);
      };
      R.onChange(renderRec);
      renderRec();
    }
    root.querySelector('#ke-target').onchange = function (e) { S.targetKst = e.target.value; save(); };
    root.querySelector('#ke-lead').onchange = function (e) { S.leadMs = +e.target.value || 0; save(); };
    root.querySelector('#ke-arm').onclick = function () {
      S.armed = !S.armed; save(); render();
      if (S.armed) { keepAwake(true); schedule(); }
      else { if (timer) clearTimeout(timer); timer = null; keepAwake(false); setStatus('정지됨'); }
    };

    if (!S.targetKst) { S.targetKst = defaultTarget(); save(); }
    render();
    setInterval(tick, 50);
    sync(false);

    /* 무장은 localStorage 에 남지만 타이머는 문서마다 새로 만들어야 한다.
     * 이게 없으면 08시에 무장해두고 09시 전에 페이지가 한 번이라도 다시 뜨는 순간
     * (수동 새로고침 / 세션 갱신 / SPA 풀 로드) 타이머만 조용히 사라진다.
     * 버튼은 '■ 정지' 로, 카운트다운은 계속 도는 채로 - 정시에 아무 일도 안 일어난다. */
    if (S.armed) { keepAwake(true); schedule(); }
  }

  /* document-start 에 주입되면 body 가 아직 없다. 게다가 SPA 가 화면을 갈아끼우면서
   * 패널을 통째로 걷어낼 수 있으므로, 사라졌으면 다시 붙인다. */
  function mount() {
    if (!document.documentElement) return;
    if (root && root.isConnected) return;
    if (root) { document.documentElement.appendChild(root); return; }  // 떨어져 나간 것 복구
    try {
      build();
    } catch (e) {
      console.error('[KE_HUD] 패널 생성 실패', e);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  }
  mount();
  setInterval(mount, 1000);   // 사라지면 1초 안에 복구

  /* 패널을 드래그해서 옮긴다. 기본 자리가 사이트 버튼을 가리는 경우가 있는데,
   * 그러면 화면을 눈으로 확인하기 어려워 도구를 안 쓰게 된다. */
  function makeDraggable(el, handle) {
    var dx = 0, dy = 0, dragging = false;
    handle.style.cursor = 'move';
    handle.addEventListener('mousedown', function (e) {
      if (e.target.id === 'ke-min') return;
      dragging = true;
      var r = el.getBoundingClientRect();
      dx = e.clientX - r.left; dy = e.clientY - r.top;
      e.preventDefault();
    });
    document.addEventListener('mousemove', function (e) {
      if (!dragging) return;
      var left = Math.max(0, Math.min(window.innerWidth - 60, e.clientX - dx));
      var top = Math.max(0, Math.min(window.innerHeight - 30, e.clientY - dy));
      el.style.left = left + 'px';
      el.style.top = top + 'px';
      el.style.right = 'auto';
    });
    document.addEventListener('mouseup', function () {
      if (!dragging) return;
      dragging = false;
      var r = el.getBoundingClientRect();
      S.pos = { left: Math.round(r.left), top: Math.round(r.top) };
      save();
    });
  }

  /* Alt+P: 다음에 클릭하는 요소의 셀렉터를 집는다 (실제로 눌리지는 않는다).
   * 모달이 떠 있으면 패널을 만질 수 없어서 셀렉터를 알아낼 방법이 없었다.
   * 키보드는 모달과 무관하게 먹으므로 그 상황에서도 쓸 수 있다. */
  var picking = false;
  function startPick() {
    picking = true;
    document.body.style.cursor = 'crosshair';
    setStatus('Alt+P: 셀렉터를 집을 요소를 클릭하세요 (ESC 취소)');
    toast('클릭할 요소를 고르세요 - 실제로 눌리지는 않습니다');
  }
  function stopPick() { picking = false; try { document.body.style.cursor = ''; } catch (e) {} }

  function showPicked(el) {
    var U2 = W.KE_UTIL || window.KE_UTIL;
    var sel = U2.cssPath(el), lab = U2.label(el);
    var text = sel + String.fromCharCode(10) + lab;
    try { navigator.clipboard.writeText(sel); } catch (e) {}
    var box = document.createElement('div');
    box.id = 'ke-picked';
    box.style.cssText = 'position:fixed;left:50%;top:20px;transform:translateX(-50%);'
      + 'z-index:2147483647;background:#0b4da2;color:#fff;padding:12px 14px;border-radius:8px;'
      + 'font:12px/1.5 monospace;max-width:90vw;box-shadow:0 4px 16px rgba(0,0,0,.4)';
    box.innerHTML = '<b style="font-family:sans-serif">집은 셀렉터 (클립보드에 복사됨)</b>'
      + '<textarea readonly style="width:min(700px,86vw);height:56px;margin-top:8px;'
      + 'font:11px monospace"></textarea>'
      + '<div style="text-align:right"><button style="padding:4px 10px">닫기</button></div>';
    document.documentElement.appendChild(box);
    var ta = box.querySelector('textarea');
    ta.value = text;
    ta.select();
    box.querySelector('button').onclick = function () { box.remove(); };
    console.log('%c[KE_HUD] 집은 셀렉터: ' + sel, 'color:#0b4da2;font-weight:bold');
    setStatus('집은 셀렉터: ' + sel.slice(0, 60));
  }

  document.addEventListener('click', function (ev) {
    if (!picking) return;
    if (ev.target.closest && ev.target.closest('#ke-hud, #ke-picked')) return;
    ev.preventDefault();
    ev.stopPropagation();
    var U2 = W.KE_UTIL || window.KE_UTIL;
    var el = ev.target.closest(U2.CLICKABLE) || ev.target;
    stopPick();
    showPicked(el);
  }, true);

  document.addEventListener('keydown', function (e) {
    if (e.altKey && (e.key === 'p' || e.key === 'P')) { e.preventDefault(); startPick(); }
    else if (e.key === 'Escape' && picking) { stopPick(); toast('집기 취소'); }
  }, true);

  expose('KE_HUD', { sync: sync, fire: fire, state: S, mount: mount, save: save, schedule: schedule,
    useMeasuredClock: function (ms, uncertaintyMs) {
      if (!Number.isFinite(ms) || !Number.isFinite(uncertaintyMs) || uncertaintyMs < 0) return false;
      externalClock = {offsetMs:ms,label:'NTP 측정 (왕복 기준 불확실성 ±'+Math.ceil(uncertaintyMs)+'ms)'};
      offsetMs=ms;syncQuality=externalClock.label;
      if(S.armed)schedule();
      return true;
    },
    render: renderRec, startPlan: function () { return startPlan(REC()); },
                     targetMs: targetMs,
                     rehearse: rehearse,
                     offset: function () { return offsetMs; } });
  console.log('%c[KE_HUD] v1.1.0 loaded', 'color:#0b4da2;font-weight:bold');
})();

} catch (e) {
  console.error('[KE] hud 로드 실패:', e);
}
