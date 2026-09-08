# Speed experiments, 2026-09-08

These are already-open-date economy experiments, not 09:00 Prestige competition.
Only a real payment window counts as a full rehearsal. No provider approval clicks.

## Baseline reanalysis

Run `20260908-103834-7bd39c1f`, relative to planned opening, local browser clock:
first award response +2.742 s; repeated award response +6.870 s;
fareInformation response +8.758 s; inputTravellers send +13.949 s,
response +20.071 s. NTP correction is about +0.030 s.
The macro log returns 3/17 -> 0/17 -> 2/17 when the currency step applies KRW.
Thus the duplicate calendar/award queries were not an unavoidable server floor.

## Experiments

| Run | Change | Result | Limits |
| --- | --- | --- | --- |
| 20260908-125544-cdd11c3f | Prepare KRW; reload departure page; lead 0 | dry/contact ready 14.25 s; no calendar query after fire; one award query at +4.581..5.989 s | Warm screen, 09-01 economy; not an order or full rehearsal; prior baseline used 2.5 s lead |
| 20260908-130903-ed8ce1d2 | Live date strip, 08-30 -> 09-01 | refused before booking | Old helper rejected cross-month targets |
| 20260908-131346-3f080ba8 | Cross-month API mapping | refused before booking | Substring selector included the parent UL; fixed exact LI/class selector |
| 20260908-131700-e6e0e265 | KRW + no reload, 08-30 -> 09-01 | dry/contact ready 11.79 s; award +0.538..2.226 s; fareInformation +2.743..4.473 s | Already-open 09-01 economy; no order; not a full rehearsal |
| 20260908-132316-4671a109 | Award API prefetch + normal departure reload | dry/contact ready 22.66 s; prefetch unused, aborted | Same request body, tracing header x-dtpc differed; no proven benefit, excluded from live order mode |
| 20260908-132652-64e3063d | Chrome restart -> daily -> prepare KRW -> fresh anchor 08-30 -> target 08-28 | **17/17, actual Hyundai payment window, 25.66 s; no final approval** | Already-open economy, manually launched daily with relative fire; no OS reboot or 9233 observer |

The recorder completed its six clicks in 13.08 s; the usable contact screen took
14.25 s. The latter is the comparison metric. A remaining ~4.6 s before the first
award request motivates a no-reload departure-date-strip experiment.
Currency preparation was applied without a calendar return in this particular
run (`calendarReturns=0`). The guard that checks fare selection after currency
changes remains active; it must not be removed to improve an apparent time.

## Changes under test

- `--prepare-krw`: before the deadline, apply KRW using the observed controls and
  verify the actual departure-page currency. Restore the previously shown date
  if changing currency returns to the calendar; do not require tomorrow's date
  to exist before opening.
- `--no-reload`: explicit experiment, start from another date on the existing departure page,
  use the site's date strip, require a subsequent API date confirmation. Reject
  same-date starts to avoid accepting stale seat data. Local test creates a
  disabled-to-enabled transition and verifies no document reload.
- `--refresh-date`: first click a different already-open date, then wait for its
  fresh API response and matching UI date before selecting the actual target.
  This avoids assuming that a stale pre-opening date strip updates itself.
  A newly opening date still needs actual 09:00 testing.
- `--mode hybrid-award`: dry-only extension of the same-session response bridge
  to the observed awardAvailability endpoint. Body and session headers must match.
  No booking or traveller endpoint is replayed.
- KE's served `/5064.10d33b7f878c872c.js` explicitly assigns
  `b.timestamp=(new Date).getTime().toString()` in the request interceptor.
  Refresh this clock field for prefetch; accept only a 13-digit UI timestamp
  within 5 seconds. Never relax session-header or date/body comparisons.
- Date preparation previously only clicked next month even when the target was
  earlier. Select the observed `-prev` or `-next` control based on the visible
  year/month. First patched run exposed a missing `re` import, then corrected it.

No faster-than-six-seconds claim and no seat-lock claim is supported yet.

## Validation

- Local currency preflight test passes (apply/redraw, unchanged KRW, wrong date timeout).
- Live-departure test passes (fresh anchor response, same-date refusal, unavailable
  -> available transition, no document reload, cross-month mapping, stale/mismatched list refusal).
- Hybrid tests: 5 pass, including real local HTTP prefetch-to-UI reuse, fresh timestamp,
  unchanged session, changed-date rejection, expired cache, and no pre-open fetch.
- Runtime tests: 11 pass. Probe JavaScript: 30 checks pass.
- The eight `t.sh --gate` test programs all passed; invoked directly on Windows
  without the shell script's blanket headless-Chrome kill. Logs: `dev-shots/gate-speed/`.

## Remaining latency

In the 11.79 s dry run, searchFamilyInfoList occupied +4.498..6.751 s,
searchMemberSubscriptions +8.401..9.746 s, validateMember +10.445..11.692 s.
These are network response intervals, not pure server CPU time. Membership or
eligibility responses must not be blindly cached to improve a benchmark.

## Full rehearsal result and next-day candidate

Run `20260908-132652-64e3063d`: checkpoint passed; macro exit 0; daily exit 0;
payment-window contract passed. Relative to planned opening, NTP-corrected:
anchor query +0.484..1.983 s; target query +2.560..4.137 s;
fareInformation +4.742..6.231 s; inputTravellers **+11.286..17.374 s**.
Payment window verified in **25.66 s** from actual fire. This is not final payment.

The earlier full baseline's order request was +13.979 s with NTP correction,
so this single full rehearsal sent it about 2.69 s earlier. Dates, launch lead,
and server response times differ; this is not a repeated controlled benchmark.
Six-second order submission has **not** been achieved.

The observed inputTravellers response has root `pnr`, boundList, traveller fare
information, ticket-time-limit fields, and warning messages. Only field names and
types were saved (`dev-shots/order-api-shape.json`); no real identifiers or PII.
The older orderId-only parser remains conservative and is not used as proof of
seat locking. Membership response fields include status, activity, subscription
status and validity dates; they were not cached or used to bypass validation.

`dev/astra_target.ps1` now defaults to the tested UI path with KRW preflight and
an anchor refresh; `-CalendarFallback` retains the earlier calendar path.
Tomorrow's plan: prepare 2027-08-30, at opening select 2027-09-01 to refresh the
strip, then select target 2027-09-04. The fresh server response is required before
continuing. New-date opening and Prestige competition remain unverified.
No scheduler was installed; actual-run ownership is still awaiting the user's
answer to the earlier question. Claude's files, profiles and tasks are unchanged.

The rehearsal wrapper now forwards the same fast-path options to daily.py.
For a subsequent authorized payment-window rehearsal (which creates an order),
choose a date without a pending duplicate reservation and use:
`dev/rehearse.py --route ICN --from FCO --minutes 6 --no-watch --start departure --prepare-krw --no-reload --date MM-DD --prepare-date MM-DD --refresh-date MM-DD`.
Use `--partial-dry` for a comparison that stops before creating an order.
