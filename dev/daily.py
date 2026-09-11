"""Supervise Astra's isolated dry workers, with run-scoped logs and hard deadlines."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta
from runtime import ROOT, KST, new_run, run_id, output_dir, atomic_json, resolve_time, check_report, build_hash, measure_clock, runtime_hash


def checkpoint(processes, identity, folder, date, fire):
    failures = []
    for name, process in processes.items():
        if process.poll() is not None:
            failures.append(f"{name}: 이미 종료 (code={process.returncode})")
            continue
        try:
            state = json.loads((folder / (name + "_status.json")).read_text(encoding="utf-8"))
            age = (datetime.now(KST) - datetime.fromisoformat(state["at"])).total_seconds()
            # Windows venv launchers spawn the actual Python child with a new
            # PID. A unique inherited token ties its heartbeat to this worker.
            token = getattr(process, 'worker_token', None)
            valid = (check_report(state, identity) and token and state.get('workerToken') == token
                     and state.get("state") == "ready" and 0 <= age < 15
                     and state.get("date") == date and state.get("fireAt") == fire)
            if not valid:
                failures.append(f"{name}: READY 상태/시각/대상/heartbeat 불일치")
        except (OSError, ValueError, KeyError, TypeError):
            failures.append(f"{name}: READY 증거 없음")
    return failures


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default="09:00")
    ap.add_argument("--setup-at", default="")
    ap.add_argument("--route", default="ICN")
    ap.add_argument("--from", dest="origin", default="FCO")
    ap.add_argument("--date", default="")
    ap.add_argument("--cabin", default="프레스티지")
    ap.add_argument("--start", default="calendar", choices=["calendar", "departure"])
    ap.add_argument('--prepare-krw', action='store_true')
    ap.add_argument('--no-reload', action='store_true')
    ap.add_argument('--prepare-date', default='')
    ap.add_argument('--refresh-date', default='')
    ap.add_argument('--park-date', default='')
    ap.add_argument("--mode", default="dry", choices=["dry", "payment-window"])
    ap.add_argument("--ready-by", default="")
    ap.add_argument("--no-watch", action="store_true")
    ap.add_argument("--no-macro", action="store_true")
    ap.add_argument("--port2", type=int, default=9233, choices=[9233])
    a = ap.parse_args()
    if a.no_watch and a.no_macro:
        ap.error("실행할 worker가 없습니다")
    if (a.no_reload or a.prepare_krw or a.prepare_date) and a.start != 'departure':
        ap.error('Fast preparation options require --start departure')
    if "KE_RUN_ID" not in os.environ:
        new_run()
    identity, folder = run_id(), output_dir()
    folder.mkdir(parents=True, exist_ok=True)
    fire = resolve_time(a.at)
    target = a.date or (fire.date() + timedelta(days=360)).strftime("%m-%d")
    clock = measure_clock()
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    manifest = {"runId": identity, "fireAt": fire.isoformat(), "date": target,
                "route": a.route, "origin": a.origin, "cabin": a.cabin,
                "dry": a.mode == "dry", "mode": a.mode, "python": sys.version, "buildHash": build_hash(), "clock": clock,
                "runtimeHash": runtime_hash(), "start": a.start,
                'prepareKrw': a.prepare_krw, 'noReload': a.no_reload, 'prepareDate': a.prepare_date,
                'refreshDate': a.refresh_date, 'parkDate': a.park_date}
    atomic_json(folder / "manifest.json", manifest)
    print(f"run={identity}; 목표 {a.origin}->{a.route} {target}; {a.mode}; 발사 {fire.isoformat()}", flush=True)
    processes, files = {}, {}
    results = {}
    readiness_failures = []
    failed_workers = set()
    try:
        for name, enabled in (("watch", not a.no_watch), ("macro", not a.no_macro)):
            if not enabled:
                continue
            script = "watch_seats.py" if name == "watch" else "autorun.py"
            cmd = [sys.executable, str(ROOT / "dev" / script), "--at", fire.isoformat(),
                   "--date", target, "--route", a.route, "--from", a.origin]
            if name == "watch":
                cmd += ["--port", str(a.port2)]
                if a.setup_at:
                    cmd += ["--setup-at", resolve_time(a.setup_at).isoformat()]
            else:
                cmd += ["--dry" if a.mode == "dry" else "--payment-window", "--cabin", a.cabin, "--start", a.start]
                if a.prepare_krw: cmd += ['--prepare-krw']
                if a.no_reload: cmd += ['--no-reload', '--lead', '0']
                if a.prepare_date: cmd += ['--prepare-date', a.prepare_date]
                if a.refresh_date: cmd += ['--refresh-date', a.refresh_date]
                if a.park_date: cmd += ['--park-date', a.park_date]
            try:
                files[name] = (folder / (name + ".log")).open("w", encoding="utf-8")
                token = uuid.uuid4().hex
                processes[name] = subprocess.Popen(cmd, stdout=files[name], stderr=subprocess.STDOUT,
                                                   env={**env, 'KE_WORKER_TOKEN': token})
                processes[name].worker_token = token
            except OSError as exc:
                failed_workers.add(name)
                readiness_failures.append(f"{name}: 실행 실패 ({type(exc).__name__})")
                results[name] = {"exitCode": None, "ok": False, "result": None,
                                 "why": f"실행 실패: {type(exc).__name__}"}
                print(f"{name}: 실행 실패; 다른 프로그램은 계속 실행", flush=True)
        check_at = fire - timedelta(seconds=60)
        if a.ready_by:
            now = datetime.now(KST)
            hh, mm = map(int, a.ready_by.split(":"))
            check_at = max(now, now.replace(hour=hh, minute=mm, second=0, microsecond=0))
        end = fire + timedelta(seconds=210)
        while any(p.poll() is None for p in processes.values()):
            now = datetime.now(KST)
            if check_at and now >= check_at:
                failures = list(readiness_failures)
                for name, p in processes.items():
                    own_failures = checkpoint({name: p}, identity, folder, target, fire.isoformat())
                    if own_failures:
                        failures.extend(own_failures)
                        failed_workers.add(name)
                        if p.poll() is None:
                            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
                atomic_json(folder / "checkpoint.json", {"runId": identity, "ok": not failures and not failed_workers,
                            "failures": failures, "failedWorkers": sorted(failed_workers)})
                print("준비 확인: " + ("; ".join(failures) if failures else "통과"), flush=True)
                check_at = None
                if failures:
                    readiness_failures = failures
            if now >= end:
                for p in processes.values():
                    if p.poll() is None:
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
                break
            time.sleep(0.2)
        for name, p in processes.items():
            code = p.wait(timeout=10)
            filename = "watch_seats.json" if name == "watch" else "autorun_report.json"
            try:
                result = json.loads((folder / filename).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                result = {}
            valid = check_report(result, identity)
            results[name] = {"exitCode": code, "ok": name not in failed_workers and code == 0 and valid and result.get("ok") is True,
                             "result": result if valid else None}
            print(f"{name}: code={code}; result={'current' if valid else 'missing/stale'}", flush=True)
    finally:
        for p in processes.values():
            if p.poll() is None:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True)
        for f in files.values():
            f.close()
    ok = bool(results) and not readiness_failures and all(v["ok"] for v in results.values())
    atomic_json(folder / "daily_report.json", {**manifest, "ok": ok, "workers": results,
                                               "readinessFailures": readiness_failures,
                                               "independentWorkers": True})
    print(f"결과: {folder}; ok={ok}", flush=True)
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main())
