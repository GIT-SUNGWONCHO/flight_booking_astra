# Parallel ownership and release target

- Claude retains the sibling flight_booking checkout, ports 9222/9223 and ke_* tasks.
- Astra uses this worktree, branch codex/hybrid-booking, ports 9232/9233 and local logs.
- Baseline HEAD: debee2aedb8fab5ec4342867bbad9d190a049972.
- The uncommitted setup.py was copied as the starting point; its original patch is
  saved under ignored dev-shots/baseline. Original files were not modified.
- Credentials are local .env only. Browser profiles were not copied.
- Never run taskkill by Chrome image name. Each runner may stop only its owned PID.
- The user confirmed that parallel tabs work. Parallel profile use is allowed;
  avoid account logout, session deletion and duplicate order creation.
- Updated acceptance: rehearsals must open the actual payment window. Preceding
  order/temporary hold creation is authorized; final payment approval is excluded.

Confirmed target: execution 2026-09-09 09:00 KST; departure 2027-09-04;
FCO -> ICN, Prestige, one adult, spouse's SKYPASS account.

No Astra scheduled booking task has been installed. Existing Claude schedules
remain unchanged. The winning implementation must be selected before arming
a single actual reservation run. Hybrid remains dry-only until its live
correctness and speed benefit are established.

Runtime results: dev-shots/runs/<runId>/ (manifest, statuses, network, final reports).
Python environment: .venv; dependency pins: requirements.lock.txt.

Use dev/astra_browsers.ps1, not legacy launch scripts. Rehearsal entry:
`.venv/Scripts/python.exe dev/rehearse.py --no-watch --route ICN --from FCO`.
This restarts only the Astra booking profile, then runs daily -> autorun in
economy payment-window mode. It verifies visible merchant and amount on the
new payment window. It never approves the payment inside that window.
`--partial-dry` is available for limited tests and cannot qualify a full rehearsal.
Neither kind of daytime rehearsal proves 09:00 new-date opening or winning a seat.
# Chrome identification

The Astra launcher saves Chrome profile names as `ASTRA · 9232` and
`ASTRA · 9233`. Web tabs also show the port in their title and a small
non-clickable badge at the bottom left. Use `dev/astra_browsers.ps1` on Windows.
