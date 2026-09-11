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
