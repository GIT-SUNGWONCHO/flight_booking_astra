"""A7 전송 계층: 페이지 안에서 같은 세션으로 API를 직접 호출한다.

왜 페이지 안인가
  로그인 쿠키·오리진·사이트가 붙이는 헤더를 그대로 쓴다. 파이썬에서 따로 쏘면
  헤더 계약을 추측해야 하고, 수집 기록에는 헤더가 남지 않는다(2026-09-12 확인).

왜 가로채서 재생인가
  수집기는 개인정보 보호를 위해 허용목록 밖 필드를 저장하지 않는다. 그래서
  `inputTravellers` 의 승객·연락처 본문을 우리가 재구성할 수 없다. 대신 사이트가
  실제로 보내는 요청을 **메모리에서** 가로채 그 헤더·본문을 재사용한다.
  가로챈 값은 파일에 쓰지 않는다. 이 모듈은 저장 기능이 없다.

이 모듈은 스스로 주문하지 않는다. 무엇을 언제 보낼지는 호출자가 정한다.
"""
from __future__ import annotations
from dataclasses import dataclass, field

# 관측된 대상 경로만 가로챈다. 그 밖의 요청은 기록하지 않는다.
WATCHED = (
    '/api/ap/booking/avail/awardAvailability',
    '/api/ap/booking/avail/fareInformation',
    '/api/ap/booking/traveller/inputTravellers',
)

# window.fetch 와 XMLHttpRequest 를 감싸 대상 경로만 메모리에 남긴다.
# 응답 본문은 잡지 않는다 - 필요한 것은 '어떻게 보내는가'다.
HOOK = """(paths) => {
  if (window.__KE_TX) return {already: true, count: window.__KE_TX.seen.length};
  const origFetch = window.fetch;
  // 재전송은 이 원본 fetch 로만 보낸다. 후킹된 fetch 로 보내면 우리 재전송이
  // 다시 캡처되어 사이트가 보낸 원본을 덮어쓴다.
  const TX = {seen: [], paths: paths, origFetch: origFetch};
  const match = (u) => { try { const x = new URL(u, location.href);
    if (x.origin !== location.origin) return null;   // 다른 오리진에는 토큰을 보내지 않는다
    return paths.find(p => x.pathname === p) ? x : null; } catch (e) { return null; } };
  const put = (url, method, headers, body, complete) => {
    const path = url.pathname;
    const i = TX.seen.findIndex(s => s.path === path);
    const rec = {path: path, url: url.href, origin: url.origin, method: method,
                 headers: headers, body: body, complete: complete, at: Date.now() / 1000};
    if (i >= 0) TX.seen[i] = rec; else TX.seen.push(rec);
    return rec;
  };
  window.fetch = function (input, init) {
    try {
      const isReq = (typeof Request !== 'undefined') && (input instanceof Request);
      const raw = isReq ? input.url : input;
      const url = match(raw);
      if (url) {
        const h = {};
        const src = (init && init.headers) || (isReq ? input.headers : null);
        if (src) { if (typeof src.forEach === 'function') src.forEach((v, k) => h[k] = v);
                   else Object.keys(src).forEach(k => h[k] = src[k]); }
        const m = ((init && init.method) || (isReq ? input.method : null) || 'GET').toUpperCase();
        const initBody = (init && typeof init.body === 'string') ? init.body : null;
        if (initBody !== null || m === 'GET') {
          put(url, m, h, initBody, true);
        } else if (isReq) {
          // Request 객체에 본문이 담긴 형태. 원본을 소비하지 않도록 clone 으로 읽고,
          // 읽기 전에는 불완전으로 두어 재전송을 막는다.
          const rec = put(url, m, h, null, false);
          try { input.clone().text().then(t => { rec.body = t; rec.complete = true; })
                  .catch(() => {}); } catch (e) {}
        } else {
          put(url, m, h, null, false);
        }
      }
    } catch (e) {}
    return origFetch.apply(this, arguments);
  };
  const OX = window.XMLHttpRequest;
  if (OX) {
    const op = OX.prototype.open, sd = OX.prototype.send, sh = OX.prototype.setRequestHeader;
    OX.prototype.open = function (m, u) { this.__ke = {m: m, u: u, h: {}}; return op.apply(this, arguments); };
    OX.prototype.setRequestHeader = function (k, v) {
      if (this.__ke) this.__ke.h[k] = v; return sh.apply(this, arguments); };
    OX.prototype.send = function (b) {
      try { if (this.__ke) { const url = match(this.__ke.u);
        const m = (this.__ke.m || 'GET').toUpperCase();
        if (url) put(url, m, this.__ke.h, (typeof b === 'string') ? b : null,
                     (typeof b === 'string') || m === 'GET'); } } catch (e) {}
      return sd.apply(this, arguments); };
  }
  window.__KE_TX = TX;
  return {already: false, count: 0};
}"""

# 가로챈 헤더·본문으로 같은 세션에서 다시 보낸다. 응답은 상태·본문·시각만 돌려준다.
SEND = """(req) => {
  const t0 = performance.now();
  const started = Date.now() / 1000;
  const f = (window.__KE_TX && window.__KE_TX.origFetch) || window.fetch;
  return f(req.url, {
    method: req.method || 'POST',
    headers: req.headers || {},
    body: req.body === null ? undefined : req.body,
    credentials: 'include',
    cache: 'no-store',
  }).then(r => r.text().then(text => ({
    ok: true, status: r.status, body: text,
    startedAt: started, elapsedMs: performance.now() - t0,
  }))).catch(e => ({ok: false, error: String(e),
    startedAt: started, elapsedMs: performance.now() - t0}));
}"""


def install(page):
    """현재 문서에만 가로채기를 건다. 페이지가 이동하면 사라진다."""
    return page.evaluate(HOOK, list(WATCHED))


def arm(context):
    """모든 새 문서에 가로채기를 건다.

    사이트 흐름은 달력→항공편→결제게이트로 문서를 옮긴다. evaluate 로 건 후킹은
    이동하면 없어지므로, 문서가 생길 때마다 실행되도록 초기 스크립트로 설치한다.
    """
    script = "(() => { const paths = %s; (%s)(paths); })()" % (
        list(WATCHED), HOOK)
    context.add_init_script(script)
    return {'armed': True, 'paths': list(WATCHED)}


def captured(page):
    """가로챈 요청 목록. 본문·헤더 원문은 돌려주지 않고 있는지만 알린다."""
    return page.evaluate("""() => (window.__KE_TX ? window.__KE_TX.seen : []).map(s => ({
        path: s.path, method: s.method, at: s.at, complete: s.complete === true,
        headerNames: Object.keys(s.headers || {}).sort(),
        bodyBytes: s.body === null ? 0 : s.body.length,
      }))""")


def send_captured(page, path, *, body=None):
    """가로챈 요청을 같은 세션에서 다시 보낸다.

    body 를 주면 그 본문으로 바꿔 보낸다(날짜 교체 등). 주지 않으면 원본 그대로다.
    반환은 상태 코드·본문 문자열·소요 시간이며 이 모듈은 판정하지 않는다.
    """
    return page.evaluate("""(arg) => {
      const tx = window.__KE_TX; if (!tx) return {ok: false, error: 'hook-missing'};
      const rec = tx.seen.find(s => s.path === arg.path);
      if (!rec) return {ok: false, error: 'not-captured'};
      if (!rec.complete) return {ok: false, error: 'incomplete-capture'};
      if (rec.origin !== location.origin) return {ok: false, error: 'origin-changed'};
      const req = {url: rec.url, method: rec.method, headers: rec.headers,
                   body: arg.body === null ? rec.body : arg.body};
      return (%s)(req);
    }""" % SEND, {'path': path, 'body': body})


@dataclass(frozen=True)
class Capture:
    """가로챈 요청 하나를 파이썬 메모리에 보관한 것. 파일·로그에 쓰지 않는다.

    페이지의 `window.__KE_TX` 는 문서가 바뀌면 사라진다(9/13 08:44 실측). 캡처를
    프로세스 메모리로 옮겨 두면 달력 문서로 돌아간 뒤에도 같은 오리진에서 보낼 수 있다.
    프로세스가 끝나면 사라진다. 헤더 timestamp 등의 서버 유효기간은 확인하지 않았다.
    """
    path: str
    origin: str
    method: str
    at: float
    url: str = field(repr=False)
    headers: dict = field(repr=False)
    body: str | None = field(repr=False)


def begin_generation(page):
    """새 준비 세대를 시작한다. 현재 문서의 캡처를 비우고 페이지 시계 기준 시작 시각을 돌려준다.

    SPA 문서에서는 이전 준비의 캡처가 남아 새 캡처와 섞일 수 있다(검토 165a57e4 P1).
    """
    return page.evaluate("""() => { const t = Date.now() / 1000;
        if (window.__KE_TX) window.__KE_TX.seen.length = 0; return t; }""")


def snapshot(page, paths=WATCHED, *, since=None):
    """현재 문서의 **완전한** 캡처만 메모리로 옮긴다. 불완전·다른 경로·since 이전 캡처는 버린다."""
    rows = page.evaluate("""(paths) => (window.__KE_TX ? window.__KE_TX.seen : [])
        .filter(s => paths.includes(s.path) && s.complete === true)
        .map(s => ({path: s.path, origin: s.origin, method: s.method, at: s.at,
                    url: s.url, headers: s.headers || {}, body: s.body}))""", list(paths))
    out = {}
    for row in rows or []:
        if (not isinstance(row, dict) or row.get('path') not in paths
                or not all(isinstance(row.get(k), str) and row.get(k)
                           for k in ('origin', 'method', 'url'))
                or type(row.get('at')) not in (int, float)
                or type(row.get('headers')) is not dict
                or not (row.get('body') is None or isinstance(row.get('body'), str))
                or (since is not None and row['at'] < since)):
            continue
        out[row['path']] = Capture(row['path'], row['origin'], row['method'], float(row['at']),
                                   row['url'], dict(row['headers']), row['body'])
    return out


def send_request(page, capture, *, body=None):
    """보관한 캡처를 **현재 문서**에서 같은 세션으로 보낸다.

    캡처한 오리진과 현재 문서 오리진이 다르면 보내지 않는다(토큰 유출 방지).
    body 를 주면 그 본문으로 바꿔 보낸다. 반환은 상태·본문·시각이며 판정하지 않는다.
    """
    if type(capture) is not Capture:
        return {'ok': False, 'error': 'not-a-capture'}
    req = {'url': capture.url, 'method': capture.method, 'headers': capture.headers,
           'body': capture.body if body is None else body}
    return page.evaluate("""(arg) => {
      if (location.origin !== arg.origin) return {ok: false, error: 'origin-changed'};
      return (%s)(arg.req);
    }""" % SEND, {'origin': capture.origin, 'req': req})
