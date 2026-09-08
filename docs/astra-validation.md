# Astra validation record — 2026-09-08

Acceptance was changed by the user: a rehearsal must display the actual payment
window. Order creation before it is permitted; final payment approval is not.
Earlier dry passes only prove partial execution.

## Evidence retained under ignored dev-shots/runs

| Run | Mode | Result |
|---|---|---|
| 20260908-095009-1662caa0 | UI dry | 6/6, contact ready, 26.17 s; partial |
| 20260908-095551-8c76de7c | Hybrid calendar dry | bridge prepared but not used; request mismatch; contact readiness failed |
| 20260908-100402-b2f1c2a8 | Restarted Chrome, UI dry | partial pass, 26.09 s |
| 20260908-101831-07e91c9c | Payment-window | readiness false negative; no fire; Windows venv launcher PID differed from worker PID |
| 20260908-102554-12717b43 | Payment-window | stopped at 5/17; fare selection had cleared, total was zero; no order request |
| 20260908-103834-7bd39c1f | Payment-window | 17/17; Hyundai Card window appeared; original detector rejected the issuer entry page; corrected detector verified the still-open real window |
| 20260908-105334-fcaf19ca | Hybrid calendar dry | 6/6, 24.55 s partial; bridge unused; identical URL/body, differing timestamp header |

The PID check now uses a unique inherited worker token plus liveness, timestamp,
run ID and target. A regression covers distinct launcher/worker PIDs and rejects
a heartbeat from another worker.

The Next step now requires the target fare radio to be checked and the mileage
total to be positive. A local browser fixture reproduces delayed totals and
proves that an unpriced selection cannot proceed.

The hybrid experiment is not qualified for actual orders. Its first live run
did not reuse a response, so its elapsed time does not demonstrate a hybrid
speed benefit. Captured session headers are never weakened to force a match.
The second live partial run used all_headers() and identified timestamp as the
remaining mismatched header. This is not a qualified production optimization.
The complete-header API choice follows the [Playwright request documentation](https://playwright.dev/python/docs/api/class-request#request-all-headers).

No result here proves 09:00 new-date opening, Prestige competition, a guarantee
that an order locks a seat, or final payment. No Astra scheduler is armed.
The explicit next-day candidate is dev/astra_target.ps1; it starts only within
the configured morning window and never rolls a missed date to tomorrow.

## Actual payment-window evidence

The issuer is ansimclick.hyundaicard.com. The observed first screen offers app
card and PIN authentication; merchant and amount are not displayed at this
stage. The verifier now recognizes this specific issuer screen, while rejecting
lookalike hosts. Neither authentication choice was clicked.

Browser timing relative to the clock-adjusted planned lead-fire instant:
inputTravellers send +16.479 s; response completed +22.601 s (HTTP 200);
issuer document completed +28.752 s. The current top-level orderId parser found
no identifier; this must not be relabeled as a verified seat hold.

The failed original report remains unchanged. Supplementary live verification
is in payment_window_verified.json in the same run folder. Configuration changes
made before fire are recorded in configuration-amendment.json.

## Subsequent speed work

See [speed experiments](astra-speed-2026-09-08.md) for the later full automatic
payment-window pass, the failed hybrid comparison, and the next-day candidate.
The earlier failure reports and timing bases above are preserved.
