#!/usr/bin/env python3
"""Repeated prototype runs across the setups in setups.json (option 1 + 2 + 3).

RESUMABLE: a run whose logs/result_<run>.json already exists is skipped, so an interrupted
batch continues where it stopped (never restarts finished runs). Order is rep-major
(rep 1 of every setup and mechanism, then rep 2, ...), so partial batches stay balanced.
After every run, aggregate_repeats.py refreshes studio/data/proto/repeats.json.
Progress: logs/batch.log. Stop file: touch logs/STOP_BATCH to stop after the current run.
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
SETUPS = json.load(open(os.path.join(HERE, "setups.json")))
MODES = ["reactive", "mobike", "atsss", "ngd"]
UCC = "/traces/ucc5g/Download__Driving__B_2020.02.27_20.35.57.csv"
REPS = max(s["reps"] for s in SETUPS)
FIRST = [("rail", "ngd"), ("corkleo", "ngd")]          # exercise the new paths first


def log(msg):
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(os.path.join(LOGS, "batch.log"), "a") as f:
        f.write(line + "\n")


def healthy():
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout
    return "katim-vehicle" in out and "katim-hq" in out


def one(setup, mode, rep):
    run = f"{mode}_{setup['id']}_r{rep}"
    if os.path.exists(os.path.join(LOGS, f"result_{run}.json")):
        return "skip"
    env = dict(os.environ, TRACE=setup.get("trace") or UCC, START=str(setup["start"]), DUR="90",
               CLASS_A=setup["class_a"], CLASS_B=setup["class_b"])
    for k in ("timeline_a", "timeline_b"):
        if setup.get(k):
            env[k.upper()] = setup[k]
    for attempt in (1, 2):
        if not healthy():
            log("containers down — trying docker compose up -d")
            subprocess.run(["docker", "compose", "up", "-d"], cwd=HERE)
            time.sleep(20)
        t0 = time.time()
        p = subprocess.run(["./run_demo.sh", mode, run], cwd=HERE, env=env, capture_output=True, text=True)
        ok = p.returncode == 0 and os.path.exists(os.path.join(LOGS, f"result_{run}.json"))
        if ok:
            r = json.load(open(os.path.join(LOGS, f"result_{run}.json")))
            log(f"OK   {run:24s} {time.time()-t0:5.0f}s  dark {r['dark_s']:.2f}s gaps {r['dark_gaps']} "
                f"le400 {r['voice_le_400ms']:.3f} standby {r['standby_s']}")
            return "ok"
        log(f"FAIL {run} attempt {attempt}: rc={p.returncode} {p.stderr.strip()[-300:]}")
        time.sleep(10)
    return "fail"


plan = []
for rep in range(1, REPS + 1):
    order = [(s, m) for s in SETUPS for m in MODES if rep <= s["reps"]]
    if rep == 1:
        head = [(s, m) for (sid, m) in FIRST for s in SETUPS if s["id"] == sid]
        order = head + [x for x in order if (x[0]["id"], x[1]) not in FIRST]
    plan += [(s, m, rep) for (s, m) in order]

log(f"batch start: {len(plan)} runs planned ({', '.join(s['id'] for s in SETUPS)} × {MODES} × up to {REPS})")
done = fail = 0
for s, m, rep in plan:
    if os.path.exists(os.path.join(LOGS, "STOP_BATCH")):
        log("STOP_BATCH found — stopping cleanly")
        break
    st = one(s, m, rep)
    if st == "ok":
        done += 1
        subprocess.run([sys.executable, os.path.join(HERE, "aggregate_repeats.py")], capture_output=True)
    elif st == "fail":
        fail += 1
log(f"batch end: {done} new runs, {fail} failed")
subprocess.run([sys.executable, os.path.join(HERE, "aggregate_repeats.py")])
