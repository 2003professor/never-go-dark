"""Sensitivity sweeps (SPEC §10.3 `--sweeps`, Appendix A) — frozen.

Every [I] knob introduced by the §13 fixes is a named field in
``sim.params.Params`` with a sweep listed here. This runner re-evaluates the
baselines on TRAIN over each sweep point and reports, for each, the overall
outage-per-transition of the five mechanisms and the mechanism ORDERING, so
the "survives the plausible range" claim (SPEC §0.1) is checkable rather than
asserted. It never touches held-out data.

    python -m eval.sweeps              # run every sweep
    python -m eval.sweeps S-CAPDIR     # one sweep by name

Each sweep point sets the knob, clears the per-instance caches (prepared
episode, oracle plans) so the change takes effect, runs the baselines, and
restores the defaults afterwards.
"""
from __future__ import annotations

import sys

import numpy as np

from eval.evaluate import (BASELINE_POLICIES, SEEDS_TRAIN, aggregate,
                           load_policy, run_policy)
from sim.params import P, set_params, reset_params
from sim.scenarios import build_scenarios, SCENARIO_IDS
import policies.oracle as _oracle

# name -> (knob, [values]).  Mirrors SPEC Appendix A I20-I38.
SWEEPS = {
    "S-CAPDIR":        ("ul_dl_ratio_cellular", [0.25, 0.53, 1.0]),
    "S-CAPDIR-WIFI":   ("ul_dl_ratio_wifi", [0.5, 1.0]),
    "S-EPISODE-LO":    ("episode_min_s", [60.0, 90.0, 120.0]),
    "S-ROUTER-HOLD":   ("router_failover_hold_s", [1.0, 2.0, 5.0]),
    "S-ROUTER-FAILBACK": ("router_failback", [False, True]),
    "S-ANCHOR":        ("anchor_min_persist_s", [0.0, 2.0, 5.0]),
    "S-HS-TIMEOUT":    ("hs_attempt_timeout_s", [5.0, 10.0, 20.0]),
    "S-HS-RTO":        ("hs_rto_s", [0.5, 1.0, 5.0]),
    "S-HS-LOSS":       ("hs_loss_model", [False, True]),
    "S-RETRY-PHASE":   ("retry_phase", ["immediate", "free", "worst"]),
    "S-RTT-DRAW":      ("rtt_draw_window_s", [5.0, 14.0, 30.0]),
    "S-RTT-DEFAULT":   ("rtt_default_scale", [0.5, 1.0, 2.0]),
    "S-LEO-RTT":       ("leo_rtt_mode", ["adjacent", "pooled"]),
    "S-GEO-FADEDUR":   ("geo_mean_fade_s", [60.0, 180.0, 600.0]),
    "S-GEO":           ("geo_availability", [0.94, 0.99, 0.996]),
    "S-UCC-PINGSEC":   ("ucc_drop_ping_second", [False, True]),
    "S-STALL":         ("stall_goodput_mbps", [0.0, 0.0024, 0.05]),
}

ORDER = ["static_documented", "mobike_roaming", "atsss_active_standby",
         "always_warm", "oracle"]


def _clear_caches(scen):
    _oracle.clear_plan_cache()
    for insts in scen.values():
        for inst in insts:
            inst.prepared_seed = None
            inst._env = None
            if hasattr(inst, "_feasref_cache"):
                inst._feasref_cache = {}


def _eval_point(seeds):
    scen = build_scenarios("train")
    _clear_caches(scen)
    out = {}
    for name, path in ORDER_PATHS.items():
        cls = load_policy(path)
        agg = aggregate(run_policy(cls, seeds, scen))
        out[name] = agg["overall"]["outage_per_transition_s"]
    return out


ORDER_PATHS = {n: p for n, p in BASELINE_POLICIES}


def run_sweep(name, seeds=None):
    seeds = seeds or SEEDS_TRAIN[:8]      # a subset keeps a full sweep tractable
    knob, values = SWEEPS[name]
    base_default = getattr(P, knob)
    print(f"\n== {name}  ({knob}; default {base_default}) ==")
    print(f"{'value':>12} | " + " | ".join(f"{n[:10]:>10}" for n in ORDER)
          + " | ordering(outage asc)")
    reset_params()
    for v in values:
        set_params(**{knob: v})
        pt = _eval_point(seeds)
        order = ",".join(n[:4] for n in sorted(ORDER, key=lambda m: pt[m]))
        print(f"{str(v):>12} | "
              + " | ".join(f"{pt[n]:10.2f}" for n in ORDER) + f" | {order}")
    reset_params()


def main():
    names = sys.argv[1:] or list(SWEEPS)
    for nm in names:
        if nm not in SWEEPS:
            print(f"unknown sweep {nm}; known: {', '.join(SWEEPS)}")
            continue
        run_sweep(nm)


if __name__ == "__main__":
    main()
