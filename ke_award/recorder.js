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
