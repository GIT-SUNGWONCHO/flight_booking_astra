"""Experimental calendar-only browser fetch -> unchanged UI response bridge.

No invented request fields, no order endpoint, no cross-session response cache.
The captured request stays in memory. A single fresh response may satisfy the
identical request made by the normal UI after reload. Any mismatch falls back.
Routing can disable browser cache, so this mode requires measured live benefit.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit

CALENDAR_PATH = "/api/ap/booking/avail/calendarFareMatrix"
AWARD_PATH = "/api/ap/booking/avail/awardAvailability"


def request_matches(source, url, method, body, headers, timestamp_now=None):
    if source["url"] != url or method != "POST" or source["req"]["method"].upper() != method:
        return False
    if source["req"].get("body") != body:
        return False
    actual = {k.lower(): v for k, v in headers.items()}
    for k, v in source['req'].get('headers', {}).items():
        got = actual.get(k.lower())
        if k.lower() == 'timestamp' and timestamp_now is not None:
            # Observed KE /5064.10d33b7f878c872c.js assigns
            # b.timestamp=(new Date).getTime().toString() in its interceptor.
            # Only this numeric clock field is volatile; session/auth stay exact.
            if not (str(v).isdigit() and len(str(v)) == 13 and
                    str(got).isdigit() and len(str(got)) == 13 and
                    abs(int(got)-timestamp_now) <= 5000):
                return False
        elif got != v:
            return False
    return True


class CalendarPrefetch:
    def __init__(self, page, open_epoch_ms, ttl_ms=5000, endpoint=CALENDAR_PATH,
                 refresh_timestamp=False):
        if endpoint not in (CALENDAR_PATH, AWARD_PATH):
            raise ValueError('Only observed availability endpoints are allowed')
        self.endpoint = endpoint
        self.refresh_timestamp = refresh_timestamp
        self.page = page
        self.helper = None
        self.source = None
        self.open_epoch_ms = open_epoch_ms
        self.ttl_ms = ttl_ms
        self.report = {"mode": "hybrid-calendar", "prepared": False, "used": False,
                       "fallback": None, "orderRequests": 0}
        self.report.update(endpoint=endpoint, refreshTimestamp=refresh_timestamp)

    def prepare(self):
        source = self.page.evaluate("""endpoint => {
          const hs = window.KE_PROBE ? KE_PROBE.hits() : [];
          return [...hs].reverse().find(h => {
            const u = new URL(h.url, location.href);
            return u.origin === location.origin && u.pathname === endpoint
              && h.req && h.req.method.toUpperCase() === 'POST' && typeof h.req.body === 'string';
          }) || null;
        }""", self.endpoint)
        if not source:
            self.report["fallback"] = "no-captured-calendar-request"
            return False
        from urllib.parse import urljoin
        source["url"] = urljoin(self.page.url, source["url"])
        origin = urlsplit(self.page.url)
        if urlsplit(source["url"]).path != self.endpoint:
            raise ValueError("Only the observed calendar endpoint is permitted")
        # Same-origin, inert helper document; no KE app or reservation state runs here.
        helper = self.page.context.new_page()
        self.helper = helper
        helper_url = f"{origin.scheme}://{origin.netloc}/__astra_calendar_helper__"
        helper.route(helper_url, lambda route: route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>Astra calendar helper</title>"))
        helper.goto(helper_url, wait_until="domcontentloaded", timeout=15000)
        helper.evaluate("""({source, openAt, refreshTimestamp}) => {
          window.__astraResult = null;
          window.__astraStarted = false;
          const start = async () => {
            window.__astraStarted = true;
            const started = Date.now();
            try {
              const headers={...source.req.headers};
              if(refreshTimestamp) for(const k of Object.keys(headers)) {
                if(k.toLowerCase()==='timestamp') {
                  if(!/^\\d{13}$/.test(headers[k])) throw new Error('Invalid timestamp');
                  headers[k]=String(started);
                }
              }
              const response = await fetch(source.url, {method:'POST', credentials:'include',
                headers, body:source.req.body, cache:'no-store',
                signal:AbortSignal.timeout(6000)});
              const body = await response.text();
              window.__astraResult = {status:response.status, body, started,
                received:Date.now(), contentType:response.headers.get('content-type') || ''};
            } catch (e) {
              window.__astraResult = {error:e.name, started, received:Date.now()};
            }
          };
          window.__astraTimer = setTimeout(start, Math.max(0, openAt-Date.now()));
        }""", {"source": source, "openAt": self.open_epoch_ms,
                 "refreshTimestamp": self.refresh_timestamp})
        self.source = source
        self.page.route("**" + self.endpoint, self._route)
        self.report["prepared"] = True
        return True

    def _route(self, route):
        req = route.request
        actual_headers = req.all_headers()
        try:
            now = self.helper.evaluate('Date.now()') if self.refresh_timestamp else None
        except Exception:
            self.report['fallback'] = 'helper-unavailable'
            route.continue_()
            return
        if self.report["used"] or not request_matches(self.source, req.url, req.method, req.post_data, actual_headers, now):
            self.report["fallback"] = "request-mismatch-or-already-used"
            self.report['mismatch'] = {
                'url': self.source['url'] != req.url,
                'body': self.source['req'].get('body') != req.post_data,
                'headers': [k for k, v in self.source['req'].get('headers', {}).items()
                            if actual_headers.get(k.lower()) != v],
            }
            route.continue_()
            return
        try:
            # Never hold the UI waiting for speculative work. A pending or failed
            # prefetch falls straight through to the site's original request.
            result = self.helper.evaluate("() => ({result:window.__astraResult, now:Date.now()})")
            cached = result["result"]
            valid = (cached and cached.get("status") == 200
                     and cached.get("started", 0) >= self.open_epoch_ms
                     and 0 <= result["now"] - cached["started"] <= self.ttl_ms
                     and "json" in cached.get("contentType", "").lower())
            if valid:
                payload = json.loads(cached["body"])
                valid = isinstance(payload, (dict, list)) and bool(payload)
                if self.endpoint == AWARD_PATH:
                    valid = (isinstance(payload, dict) and
                             isinstance(payload.get('upsellBoundAvailList'), list) and
                             not payload.get('error') and not payload.get('errorCode'))
            if not valid:
                self.report["fallback"] = "pending-failed-or-stale"
                route.continue_()
                return
            route.fulfill(status=200, content_type=cached["contentType"], body=cached["body"])
            self.report.update(used=True, receivedAt=cached["received"],
                               fetchedAt=cached["started"], fallback=None)
        except Exception:
            self.report["fallback"] = "bridge-error"
            route.continue_()

    def close(self):
        if self.source:
            self.page.unroute("**" + self.endpoint, self._route)
        if self.helper:
            try:
                self.report['prefetch'] = self.helper.evaluate("""() => {
                  const r=window.__astraResult;
                  return r ? {status:r.status||null,error:r.error||null,
                    started:r.started,received:r.received,durationMs:r.received-r.started} : {pending:true};
                }""")
            except Exception:
                self.report['prefetch'] = {'unavailable': True}
            self.helper.close()
