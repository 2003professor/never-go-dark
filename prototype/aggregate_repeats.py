#!/usr/bin/env python3
"""Aggregate repeated prototype runs into results/prototype-repeats.json (read by the console).

Runs are named  <mode>_<setup>_r<k>  (e.g. ngd_corkgeo_r3) and scored by analyze.py into
logs/result_<run>.json. Setups:
  corkgeo  bearer A = real UCC 5G Cork drive (the film's run), bearer B = GEO MODEL
  corkleo  bearer A = the same real UCC 5G drive, bearer B = REAL Starlink LEO (road, uplink measured)
  rail     REAL SIMULTANEOUS capture: two cellular subflows on a high-speed train (HSR-MPTCP);
           bearer A = the carrier that stalls, bearer B = the other carrier
The single v2 batch (logs/result_<mode>_final.json) is reported separately as "reference_run"
for corkgeo: it is the run the film, PDF and console replay show. Nothing here is modelled or
smoothed: every number is one measured run, and the summary is plain mean / sample SD / min / max.
"""
import datetime as dt
import glob
import json
import os
import re
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(HERE, "logs")
OUT = os.path.join(HERE, "..", "results", "prototype-repeats.json")
MODES = ["reactive", "mobike", "atsss", "ngd"]
MECH_LABEL = {
    "reactive": "Documented 9001-R design (emulated, reactive)",
    "mobike": "MOBIKE-style roaming (RFC 4555)",
    "atsss": "ATSSS-style active-standby (TS 23.501)",
    "ngd": "Never Go Dark (shipped policy, unmodified)",
}
SETUPS = json.load(open(os.path.join(HERE, "setups.json")))


def load(path):
    r = json.load(open(path))
    return {
        "run": r["run"], "dark_s": r["dark_s"], "dark_gaps": r["dark_gaps"],
        "longest_dark_s": r["longest_dark_s"], "voice_le_400ms": r["voice_le_400ms"],
        "owd_p95_ms": r["owd_p95_ms"], "standby_s": r["standby_s"],
        "bearer_b_mb": round(r["bearer_b_tx_bytes"] / 1e6, 2), "dark_windows_s": r["dark_windows_s"],
        "voice_sent": r["voice_sent"], "voice_received": r["voice_received"],
    }


def summ(runs, key):
    xs = [r[key] for r in runs if r.get(key) is not None and r[key] == r[key]]
    if not xs:
        return None
    return {"mean": round(st.mean(xs), 3), "sd": round(st.stdev(xs), 3) if len(xs) > 1 else None,
            "min": round(min(xs), 3), "max": round(max(xs), 3)}


setups = []
for s in SETUPS:
    mechs = {}
    for m in MODES:
        allf = glob.glob(os.path.join(LOGS, f"result_{m}_{s['id']}_r*.json"))
        files = sorted((p for p in allf if re.search(r"_r(\d+)\.json$", p)),
                       key=lambda p: int(re.search(r"_r(\d+)\.json$", p).group(1)))
        runs = [load(p) for p in files]
        # runs set aside by a pre-registered rule (see logs/batch.log): kept and reported, not in the stats
        excluded = []
        for p in sorted(set(allf) - set(files)):
            why = re.search(r"_r\d+\.([a-z-]+)\.json$", p)
            excluded.append({**load(p), "excluded_because": {
                "render-overlap": "overlapped the film render window; re-run under the pre-registered rule",
                "coldstart": "first run after ~26 h idle: its only gap was at run start (WireGuard cold start), not at the collapse; re-run",
            }.get(why.group(1) if why else "", "set aside")})
        mechs[m] = {
            "label": MECH_LABEL[m], "n": len(runs), "runs": runs, "excluded_runs": excluded,
            "summary": {k: summ(runs, k) for k in ("dark_s", "dark_gaps", "longest_dark_s",
                                                   "voice_le_400ms", "standby_s", "bearer_b_mb")},
        }
    ref = {}
    if s["id"] == "corkgeo":
        for m in MODES:
            p = os.path.join(LOGS, f"result_{m}_final.json")
            if os.path.exists(p):
                ref[m] = load(p)
    n_min = min(v["n"] for v in mechs.values())
    status = "complete" if n_min >= s["reps"] else ("running" if any(v["n"] for v in mechs.values()) else "pending")
    setups.append({**{k: s[k] for k in ("id", "label", "bearer_a", "bearer_b", "window", "reps", "why")},
                   "status": status, "mechanisms": mechs, "reference_run": ref or None})

doc = {
    "_about": __doc__.strip().splitlines()[0],
    "generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "voice_gap_definition": ">= 10 consecutive lost 20 ms voice frames (>= 200 ms) = one voice-dark gap",
    "policy_file": "simulator/policies/policy.py (unmodified, read-only)",
    "setups": setups,
}
os.makedirs(os.path.dirname(OUT), exist_ok=True)
json.dump(doc, open(OUT, "w"), indent=1)
for s in setups:
    line = " · ".join(f"{m}: n={s['mechanisms'][m]['n']}" + (
        f" dark {s['mechanisms'][m]['summary']['dark_s']['mean']:.2f}±{(s['mechanisms'][m]['summary']['dark_s']['sd'] or 0):.2f}s"
        if s['mechanisms'][m]['summary']['dark_s'] else "") for m in MODES)
    print(f"{s['id']:8s} [{s['status']}] {line}")
print("wrote", os.path.relpath(OUT, HERE))
