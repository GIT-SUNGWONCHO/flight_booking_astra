/* 조회 응답 계측(probe) 검증.
 *
 * 목적은 "프레스티지가 5초 만에 매진되는 기준이 무엇인가" 를 추측이 아니라 기록으로
 * 답하는 것이다. 09:00 에 사람이 개발자도구를 붙잡고 있을 수는 없으니 자동으로 남긴다.
 *
 * 여기서 못 박는 것:
 *   - 오가는 응답을 바꾸지 않는다 (페이지가 받는 값이 그대로여야 한다)
 *   - 좌석/운임 조회로 보이는 것만 남긴다 (로그인 토큰 같은 것까지 쓸어담지 않는다)
 *   - 잔여석 필드가 있는 응답을 표시한다 (그게 매진 판정의 출처 후보다)
 *
 * 실행:  node test/test_probe.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = path.join(__dirname, '..');
global.window = global;

// 저장소 흉내. probe 가 '조회 조건이 어디 사는가' 를 훑을 때 쓴다.
function fakeStore(obj) {
  const d = { ...obj };
  return {
    get length() { return Object.keys(d).length; },
    key(i) { return Object.keys(d)[i]; },
    getItem(k) { return d[k]; },
    setItem(k, v) { d[k] = v; },
  };
}
global.sessionStorage = fakeStore({
  // 실측: 조회 페이지 주소에는 날짜가 없다(/departure 뿐). 그래서 조건이 어디
  // 사는지가 달력 건너뛰기의 성립 여부를 가른다.
  'booking.cond': '{"dep":"ICN","arr":"FCO","departureDate":"20270821"}',
  'theme': 'light',
});
global.localStorage = fakeStore({ 'ke_award_steps_v1': '{"steps":[]}' });

// 가짜 fetch/XHR 을 먼저 깔아두고 probe 가 그것을 감싸게 한다
const served = new Map();
global.fetch = function (url) {
  const body = served.get(String(url)) || '{}';
  return Promise.resolve({
    status: 200,
    clone() { return { text: () => Promise.resolve(body) }; },
    text: () => Promise.resolve(body),
    __body: body,
  });
};
new Function(fs.readFileSync(path.join(ROOT, 'ke_award', 'probe.js'), 'utf8'))();
const P = global.KE_PROBE;

let fails = [];
const check = (ok, label, detail = '') => {
  console.log(`${ok ? 'ok  ' : 'FAIL'}  ${label}${!ok && detail ? '  <- ' + detail : ''}`);
  if (!ok) fails.push(label);
};

const AVAIL = '/api/booking/award/availability?dep=ICN&arr=FCO';
const LOGIN = '/api/member/login';
served.set(AVAIL, JSON.stringify({
  flights: [{ no: 'KE927', cabins: [{ code: 'PR', remainingSeats: 2 }, { code: 'EY', remainingSeats: 9 }] }],
}));
served.set(LOGIN, JSON.stringify({ token: 'SECRET-DO-NOT-KEEP', name: 'CHO' }));

(async () => {
  check(typeof global.fetch === 'function' && global.fetch.__keProbe === true,
        'fetch 를 감쌌다');

  const res = await global.fetch(AVAIL);
  const text = await res.text();
  check(JSON.parse(text).flights[0].cabins[0].remainingSeats === 2,
        '페이지가 받는 응답을 바꾸지 않는다 (엿보기만 한다)');

  await global.fetch(LOGIN);
  await new Promise((r) => setTimeout(r, 30));   // clone().text() 는 비동기다

  const hits = P.hits();
  check(hits.length === 1, `좌석 조회만 남긴다 (실제 ${hits.length}건)`,
        JSON.stringify(hits.map((h) => h.url)));
  check(!P.dump().includes('SECRET-DO-NOT-KEEP'),
        '로그인 응답 같은 것은 남기지 않는다');
  check(hits[0] && hits[0].seaty === true,
        '잔여석 필드가 있는 응답을 표시한다 (매진 판정의 출처 후보)');
  // 모양이 다른 응답은 '못 봤다' 고 말해야 한다. 아는 척하면 매진을 못 본 채로 지나간다.
  check(P.summary().includes('아직 못 봄'),
        '해석하지 못한 모양은 아는 척하지 않는다', P.summary());
  check(P.dump().includes('remainingSeats'), '내보내면 실제 값이 보인다');

  P.clear();
  check(P.hits().length === 0, '지울 수 있다');

  // ---- 실측 응답으로 판정 필드를 읽어낸다 (2026-08-27 캡처) ----
  const REAL_AVAIL = 'https://www.koreanair.com/api/ap/booking/avail/awardAvailability';
  const REAL_PAY = 'https://www.koreanair.com/api/pp/payment/GetAvailablePaymentType';
  served.set(REAL_AVAIL, fs.readFileSync(path.join(ROOT, 'test/fixture/api/awardAvailability.json'), 'utf8'));
  served.set(REAL_PAY, fs.readFileSync(path.join(ROOT, 'test/fixture/api/paymentType.json'), 'utf8'));
  await global.fetch(REAL_AVAIL);
  await global.fetch(REAL_PAY);
  await new Promise((r) => setTimeout(r, 30));

  const tl = P.seatTimeline();
  const pr = tl.find((r) => r.family === 'KEBONUSPR');
  const ey = tl.find((r) => r.family === 'KEBONUSEY');
  check(!!pr && !!ey, `등급 둘을 읽는다 (실제 ${tl.length}건)`);
  check(pr && pr.name === '프레스티지', 'KEBONUSPR 이 프레스티지다', pr && pr.name);
  check(pr && pr.seatCount === '0' && pr.soldout === true,
        '프레스티지 매진을 그대로 읽는다', JSON.stringify(pr));
  check(ey && ey.seatCount === '9' && ey.soldout === false,
        '일반석 9석을 그대로 읽는다', JSON.stringify(ey));
  check(pr && pr.flight === 'KE931' && pr.date === '20270821',
        '어느 편 어느 날인지 함께 남긴다', JSON.stringify(pr && [pr.flight, pr.date]));
  check(P.summary().includes('프레스티지 매진') && P.summary().includes('일반석 9석'),
        '요약 한 줄로 등급별 상태를 보여준다', P.summary());
  check(P.dump().startsWith('== 등급별 좌석 수 =='),
        '내보내기 맨 위에 표가 먼저 온다', P.dump().slice(0, 40));

  // ---- keCabin: 대한항공 운항편 기준 매진 판정 (2026-09-01 실전 모양) ----
  // KE901(대한항공 운항)은 프레스티지 매진·일반석 9석, KE5901(에어프랑스 운항 코드셰어)은 전부 매진.
  // (clear 하지 않는다 - 뒤의 결제수단 테스트가 쓰는 응답을 지우면 안 된다. 날짜(0827)로 최신 응답을 고른다.)
  const RACE = 'https://www.koreanair.com/api/ap/booking/avail/awardAvailability?t=race';
  served.set(RACE, JSON.stringify({ upsellBoundAvailList: [{ availFlightList: [
    { departureDate: '20270827112000',
      flightInfoList: [{ carrierCode: 'KE', flightNumber: '901', operationCarrierCode: 'KE', codeShare: false }],
      commercialFareFamilyList: [
        { fareFamily: 'KEBONUSEY', seatCount: '9', soldout: false },
        { fareFamily: 'KEBONUSPR', seatCount: '0', soldout: true },
        { fareFamily: 'KEBONUSFC', seatCount: '0', soldout: true }] },
    { departureDate: '20270827114000',
      flightInfoList: [{ carrierCode: 'KE', flightNumber: '5901', operationCarrierCode: 'AF', codeShare: true }],
      commercialFareFamilyList: [
        { fareFamily: 'KEBONUSEY', seatCount: '0', soldout: true },
        { fareFamily: 'KEBONUSPR', seatCount: '0', soldout: true }] },
  ] }] }));
  await global.fetch(RACE);
  await new Promise((r) => setTimeout(r, 30));

  const prc = P.keCabin('프레스티지', '08-27');
  check(prc && prc.soldout === true, '대한항공 프레스티지 매진을 판정한다', JSON.stringify(prc));
  check(prc && prc.eySeats === 9, '안내용 일반석 좌석수를 함께 준다', JSON.stringify(prc));
  check(prc && prc.keFlights === 1, '코드셰어(KE5901/에어프랑스)는 대한항공으로 세지 않는다', JSON.stringify(prc));
  const eyc = P.keCabin('일반석', '08-27');
  check(eyc && eyc.soldout === false && eyc.seats === 9,
        '일반석은 9석이라 매진으로 보지 않는다', JSON.stringify(eyc));
  check(P.keCabin('프레스티지', '08-28') === null,
        '응답에 없는 날짜는 매진이라 단정하지 않는다(null)', JSON.stringify(P.keCabin('프레스티지', '08-28')));

  const pt = P.payTypes();
  check(Array.isArray(pt) && pt.includes('NAVERPAY'),
        '쓸 수 있는 결제 수단을 목록으로 읽는다 (네이버페이 있음)', JSON.stringify(pt));
  check(P.dump().includes('== 쓸 수 있는 결제 수단 =='), '내보내기에 결제 수단도 나온다');

  // 노이즈: 구글 애널리틱스는 현재 주소를 파라미터로 실어보내 'award' 가 걸린다
  P.clear();
  const GA = 'https://analytics.google.com/g/collect?v=2&dl=' +
             encodeURIComponent('https://www.koreanair.com/booking/select-award-flight/departure');
  served.set(GA, 'GIF89a');
  await global.fetch(GA);
  await new Promise((r) => setTimeout(r, 30));
  check(P.hits().length === 0,
        '애널리틱스는 주소에 award 가 들어 있어도 기록하지 않는다',
        JSON.stringify(P.hits().map((h) => h.url)));

  // ---- 조회 조건이 저장소에 있는가 (달력 건너뛰기의 성립 조건) ----
  const hints = P.storeHints();
  const dated = hints.filter((h) => h.date);
  check(dated.length === 1 && dated[0].key === 'booking.cond',
        '저장소에서 날짜를 든 항목을 찾아낸다', JSON.stringify(hints));
  check(dated[0] && dated[0].date === '20270821', '그 날짜를 그대로 읽는다',
        dated[0] && dated[0].date);
  check(!hints.some((h) => h.key.indexOf('ke_award') === 0),
        '우리가 쓴 항목은 결과에 넣지 않는다');
  check(!P.dump().includes('"steps"'),
        '날짜 없는 항목의 내용은 내보내지 않는다 (키 이름과 길이만)');
  check(P.dump().includes('== 조회 조건이 어디 있나'), '내보내기에 그 결과가 들어간다');

  const stripUrl='/api/ap/booking/avail/awardAvailability';
  served.set(stripUrl,JSON.stringify({upsellBoundAvailList:[{upsellCalendarFareList:[{date:'20270831'},{date:'20270901'}]}]}));
  await global.fetch(stripUrl); await new Promise(r=>setTimeout(r,30));
  check(JSON.stringify(P.latestStripDates())==='["08-31","09-01"]','최신 응답으로 월 경계 날짜 순서를 읽는다');
  served.set(stripUrl,JSON.stringify({upsellBoundAvailList:[]}));
  await global.fetch(stripUrl); await new Promise(r=>setTimeout(r,30));
  check(P.latestStripDates().length===0,'최신 날짜 목록이 비면 옛 목록으로 추측하지 않는다');
  console.log();
  console.log(fails.length ? 'FAILED: ' + fails.join(', ') : '조회 응답 계측 테스트 통과');
  process.exit(fails.length ? 1 : 0);
})();
