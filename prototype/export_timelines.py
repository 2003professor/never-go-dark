#!/usr/bin/env python3
"""Export per-second bearer timelines for the prototype from the SIMULATOR'S OWN bearer models.

Why: the prototype must see exactly the links the simulator sees. Instead of re-implementing
trace semantics (zero-order hold, horizons, Starlink RTT attachment, concatenation of measured
seconds), we sample katim-sim's bearer objects once per second and write a CSV that
scripts/linkdrive.py replays with tc (underlay delay/loss + gateway capacity).

Writes prototype/timelines/<setup>_<a|b>.csv and prototype/setups.json:
  corkgeo  A = UCC 5G Cork drive (linkdrive's original replay, unchanged)  B = GEO MODEL (unchanged)
  corkleo  A = the same UCC 5G drive (original replay)                   B = REAL Starlink LEO timeline
  rail     A = HSR-MPTCP strong carrier U4 timeline   B = the SIMULTANEOUS weak carrier M3 timeline
Bearer timelines are fixed across repetitions; repetitions measure run-to-run variance of the
real system (WireGuard handshakes, scheduling, queueing), not trace sampling.
"""
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SIM = os.path.join(HERE, "..", "katim-sim")
sys.path.insert(0, SIM)
from sim.bearers import discover_traces, build_leo_concatenated, TraceReplayBearer, trace_is_heldout  # noqa: E402
from sim.scenarios import degradation_events, _hsr_groups, _hash_int, _starlink_long_enough  # noqa: E402

DUR = 90
TL = os.path.join(HERE, "timelines")
os.makedirs(TL, exist_ok=True)
traces = discover_traces()


def export(bearer, t0, name, default_owd_ms):
    path = os.path.join(TL, name)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["k", "trace_t", "usable", "cap_ul_mbps", "cap_dl_mbps", "owd_ms", "loss_pct", "rtt_src"])
        for k in range(DUR + 6):
            s = bearer.sample(t0 + k)
            ul = s.cap_mbps if s.usable else 0.0
            dl = (s.cap_dl_mbps if s.cap_dl_mbps is not None else (s.dl_mbps or 0.0)) if s.usable else 0.0
            owd = (s.rtt_ms / 2.0) if s.rtt_ms else default_owd_ms
            loss = 100.0 * (s.loss or 0.0)
            w.writerow([k, round(t0 + k, 3), int(bool(s.usable)), round(ul, 4), round(dl, 4),
                        round(owd, 1), round(loss, 3), s.rtt_src])
    return path


setups = []
UCC = "/traces/ucc5g/Download__Driving__B_2020.02.27_20.35.57.csv"
cork_a = {"dataset": "UCC 5G/4G drive-test (University College Cork)", "trace": "Download__Driving__B_2020.02.27_20.35.57",
          "window_s": [280, 370], "provenance": "REAL", "class": "5G"}

# --- corkgeo: unchanged reference setup ---------------------------------------------------
setups.append({
    "id": "corkgeo", "label": "Cork 5G drive · GEO satellite backup (model)", "reps": 5,
    "trace": UCC, "start": 280, "window": [280, 370],
    "bearer_a": cork_a,
    "bearer_b": {"dataset": "GEO model (SPEC §3.6: 600 ms RTT, 18/3 Mbit/s, 0.1 % loss)", "provenance": "MODEL", "class": "GEO"},
    "class_a": "5G", "class_b": "GEO", "timeline_a": None, "timeline_b": None,
    "why": "The film's run, repeated: how much of 0.44 s vs 3.90 s is run-to-run variance?",
})

# --- corkleo: same Cork drive, REAL Starlink backup ------------------------------------------
star = traces["starlink_road"]
leo_ids = sorted(t for t in star if trace_is_heldout(t) and _starlink_long_enough(star[t])) or \
          sorted(t for t in star if _starlink_long_enough(star[t]))
leo_id = leo_ids[0]
leo = build_leo_concatenated(star[leo_id], leo_id, seed=400)
export(leo, 0.0, "corkleo_b.csv", 20.0)
setups.append({
    "id": "corkleo", "label": "Cork 5G drive · Starlink LEO backup (measured)", "reps": 5,
    "trace": UCC, "start": 280, "window": [280, 370],
    "bearer_a": cork_a,
    "bearer_b": {"dataset": "Starlink on the road (measured; seconds concatenated, unmeasured gaps removed; RTT attached per SPEC §13.8)",
                 "trace": leo_id, "window_s": [0, DUR], "provenance": "REAL (stitched to the Cork drive by time only)", "class": "LEO"},
    "class_a": "5G", "class_b": "LEO", "timeline_a": None, "timeline_b": "/app/timelines/corkleo_b.csv",
    "why": "Replace the GEO model with a measured satellite link.",
})

# --- rail: REAL SIMULTANEOUS two-carrier capture on a high-speed train -----------------------
hsr = traces["hsr_mptcp"]
VOICE_FLOOR = 0.15   # Mbit/s: below this the voice class (64 kbit/s + keepalives + overhead) starves
GOOD_B = 0.5         # Mbit/s: a standby that can genuinely carry the call
best = None
for split in ("heldout", "train"):        # held-out first; train only if it has a clearly better collapse
    for gid, (strong, weak) in _hsr_groups(hsr, split).items():
        bS = TraceReplayBearer(hsr[strong], "hsr_mptcp", strong, rtt_seed=_hash_int("rttA", strong) & 0xFFFF)
        bW = TraceReplayBearer(hsr[weak], "hsr_mptcp", weak, rtt_seed=_hash_int("rttB", weak) & 0xFFFF)
        dur = min(bS.duration_s, bW.duration_s)
        if dur < DUR + 6:
            continue
        for ev in degradation_events(bS):
            te = float(ev[0] if isinstance(ev, (tuple, list)) else ev)
            start = min(max(0.0, te - 40.0), dur - DUR - 6)
            sa = [bS.sample(start + k) for k in range(DUR)]
            sb = [bW.sample(start + k) for k in range(DUR)]
            starve = [k for k in range(DUR) if (not sa[k].usable) or sa[k].cap_mbps < VOICE_FLOOR]
            covered = sum(1 for k in starve if sb[k].usable and sb[k].cap_mbps >= GOOD_B)
            # want: a real collapse on A (several starving seconds) that B could have carried,
            # with A healthy for a while first (so there is something to predict)
            lead_ok = sum(1 for k in range(min(starve, default=0)) if sa[k].usable and sa[k].cap_mbps >= GOOD_B)
            score = (min(covered, 20), min(lead_ok, 15), len(starve), split == "heldout")
            if best is None or score > best[0]:
                best = (score, gid, strong, weak, bS, bW, start, te, split)
(score, gid, strong, weak, bS, bW, start, te, split) = best
export(bS, start, "rail_a.csv", 40.0)
export(bW, start, "rail_b.csv", 40.0)
setups.append({
    "id": "rail", "label": "High-speed rail · two carriers captured simultaneously", "reps": 5,
    "trace": None, "start": round(start, 1), "window": [round(start, 1), round(start + DUR, 1)],
    "bearer_a": {"dataset": "HSR-MPTCP (high-speed rail, MPTCP subflows)", "trace": strong, "window_s": [round(start, 1), round(start + DUR, 1)],
                 "provenance": "REAL SIMULTANEOUS", "class": "cellular-subflow"},
    "bearer_b": {"dataset": "HSR-MPTCP, the other carrier on the same train at the same seconds", "trace": weak,
                 "window_s": [round(start, 1), round(start + DUR, 1)], "provenance": "REAL SIMULTANEOUS", "class": "cellular-subflow"},
    "class_a": "cellular-subflow", "class_b": "cellular-subflow",
    "timeline_a": "/app/timelines/rail_a.csv", "timeline_b": "/app/timelines/rail_b.csv",
    "why": "A second real collapse, with both links measured at the same time (no stitching).",
    "selection": {"group": gid, "split": split, "degradation_event_trace_s": round(te, 1),
                  "rule": f"max seconds where A starves (<{VOICE_FLOOR} Mbit/s or unusable) while B carries >= {GOOD_B} Mbit/s, then healthy lead before the collapse",
                  "a_starving_s_covered_by_b": score[0], "healthy_lead_s": score[1], "a_starving_s": score[2]},
})

json.dump(setups, open(os.path.join(HERE, "setups.json"), "w"), indent=1)
for s in setups:
    print(s["id"], "|", s["label"], "| A:", s["bearer_a"].get("trace"), "| B:", s["bearer_b"].get("trace", s["bearer_b"]["dataset"][:30]),
          "| window", s["window"], ("| " + json.dumps(s["selection"])) if "selection" in s else "")
for name in ("corkleo_b.csv", "rail_a.csv", "rail_b.csv"):
    rows = list(csv.DictReader(open(os.path.join(TL, name))))
    us = sum(int(r["usable"]) for r in rows[:DUR])
    caps = [float(r["cap_ul_mbps"]) for r in rows[:DUR]]
    owds = [float(r["owd_ms"]) for r in rows[:DUR]]
    print(f"  {name}: usable {us}/{DUR} s · UL cap median {sorted(caps)[len(caps)//2]:.2f} Mbit/s · OWD median {sorted(owds)[len(owds)//2]:.0f} ms · rtt_src {sorted(set(r['rtt_src'] for r in rows))}")
