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
