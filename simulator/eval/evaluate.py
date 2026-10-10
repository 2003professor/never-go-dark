"""Frozen evaluator — SPEC §8 (metrics + scalar score) + §6.4 (split) + §10.3.

One-command reproduction:

    python -m eval.evaluate --split heldout --policy policies/policy.py \
        --seeds heldout --out results/figures/

The evaluator is change-controlled (SPEC §10.1): only the policy files change
after the D-4 freeze. It never reports held-out numbers unless explicitly run
with --split heldout, and the loop only ever sees the train split.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
from collections import defaultdict

import numpy as np

from eval.metrics import (compute_metrics, gates_pass, gate_shortfall,
                          build_feasref, ALERT_HIT_MIN)
from sim.isolation import (PolicyIsolationError, check_policy_source,
                           register_loaded_policy_class)
from sim.engine import run
from sim.scenarios import SCENARIO_IDS, build_scenarios

# SPEC §13.8: 20 train seeds (was 5) so the seed-sensitive scalar is a stable
# basis for the bar and the loop; held-out uses a disjoint 20-seed set.
SEEDS_TRAIN = list(range(20))
SEEDS_HELDOUT = list(range(100, 120))

# frozen score weights (SPEC §8.7) + gradient gate penalty (SPEC §13.3)
W_AOI, W_OUT, W_OVH, W_TX, W_VID = 1.0, 1.0, 0.3, 0.3, 0.2
GATE_PENALTY = 10.0
EPS = 1e-6

BASELINE_POLICIES = [
    ("static_documented", "policies/static_documented.py"),
    ("mobike_roaming", "policies/mobike_roaming.py"),
    ("atsss_active_standby", "policies/atsss_active_standby.py"),
    ("always_warm", "policies/always_warm.py"),
    ("oracle", "policies/oracle.py"),
]


def load_policy(path: str):
    """Import a policy module by file path and return its POLICY class.

    SPEC §7.4 enforcement: the file is statically checked BEFORE it is
    executed (sim/isolation.check_policy_source) — imports outside
    policies.interface + pure stdlib, file/introspection primitives, dunder
    and frame access, and any use of the oracle-only instance hook are
    rejected, so a policy that would import sim.* or read a trace off disk
    never runs. The loaded class is registered with its verified source path;
    the engine grants the scenario instance only to the frozen oracle file.
    """
    if not os.path.isabs(path):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if not os.path.exists(path):
            path = os.path.join(here, path)
    check_policy_source(path)
    spec = importlib.util.spec_from_file_location("loaded_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    pol = getattr(mod, "POLICY")
    cls = pol.__class__
    if getattr(cls, "__module__", None) != "loaded_policy":
        raise PolicyIsolationError(
            f"{path}: POLICY must be an instance of a class defined in that "
            "file (not an imported one)")
    register_loaded_policy_class(cls, path)
    return cls   # return the class so each run gets a fresh instance


def run_policy(policy_cls, seeds, scenarios):
    """Run a policy over every (instance, seed) in ``scenarios``. Returns a
    list of per-run metric dicts."""
    rows = []
    for sid in SCENARIO_IDS:
        for inst in scenarios.get(sid, []):
            for seed in seeds:
                rr = run(inst, policy_cls(), seed)
                # feasibility reference is a property of the (prepared) instance
                # + seed, identical for every mechanism (SPEC §13.3).
                feas = build_feasref(inst, rr.duration_s, seed)
                m = compute_metrics(rr, feas)
                m["gate_shortfall"] = gate_shortfall(m)
                rows.append(m)
    return rows


def aggregate(rows, by="scenario"):
    """Mean/std of each metric grouped by scenario (and an 'overall' group)."""
    groups = defaultdict(list)
    for m in rows:
        groups[m[by]].append(m)
    groups["overall"] = rows
    numeric = ["aoi_mean_s", "aoi_p95_s", "aoi_stale_frac", "alert_hit_rate",
               "voice_median_ms", "voice_within_mcptt", "voice_within_hard",
               "alert_hit_rate_feasible", "voice_within_hard_feasible",
               "voice_within_mcptt_feasible", "voice_median_feasible_ms",
               "n_feasible_alerts", "n_feasible_voice", "gate_shortfall",
               "outage_s_total", "outage_per_transition_s", "outage_p95_s",
               "tunnel_dark_s_total", "stall_s_total",
               "tunnel_dark_per_transition_s", "stall_per_transition_s",
               "outage_max_s", "dropped_voice_frames", "standby_tx_s",
               "emission_tx_s", "secondary_active_s",
               "overhead_bytes_satcom", "overhead_bytes_total",
               "video_goodput", "dropped_pli_superseded"]
    out = {}
    for g, ms in groups.items():
        agg = {"n_runs": len(ms)}
        for key in numeric:
            vals = np.array([mm.get(key, 0.0) for mm in ms], dtype=float)
            agg[key] = float(np.mean(vals))
            agg[key + "_std"] = float(np.std(vals))
        # gates over FEASIBLE messages (headline, SPEC §13.3) + absolute rates
        agg["gates_pass_frac"] = float(np.mean([gates_pass(mm) for mm in ms]))
        agg["all_gates_pass"] = all(gates_pass(mm) for mm in ms)
        agg["gates_pass_abs_frac"] = float(np.mean(
            [gates_pass(mm, feasible=False) for mm in ms]))
        agg["all_gates_pass_abs"] = all(
            gates_pass(mm, feasible=False) for mm in ms)
        out[g] = agg
    return out


def scalar_score(agg_policy: dict, agg_base: dict, agg_emref: dict = None) -> dict:
    """SPEC §8.7 + §13.3 scalar. The objective is a dimensionless margin over
    the documented-static baseline, defined so it is exactly 0 at baseline
    parity (the earlier normalisation left parity at ~-2.6). The -1e6 gate
    cliff is replaced by a steep but SMOOTH penalty proportional to the
    feasible-gate shortfall, so the loop keeps a gradient toward feasibility;
    gate PASS/FAIL is reported separately. Emissions metrics are scaled by an
    always-up reference (``agg_emref``, e.g. always-warm) because the
    documented baseline spends ~0 there (a 0 scale is undefined)."""
    p = agg_policy["overall"]
    b = agg_base["overall"]
    r = (agg_emref or agg_base)["overall"]
    sc_aoi = b["aoi_p95_s"] + EPS
    sc_out = b["outage_per_transition_s"] + EPS
    sc_ovh = r["overhead_bytes_satcom"] + EPS
    sc_tx = r["emission_tx_s"] + EPS          # SPEC §13.8 emissions axis
    sc_vid = b["video_goodput"] + EPS

    def cost(a):
        return (W_AOI * a["aoi_p95_s"] / sc_aoi
                + W_OUT * a["outage_per_transition_s"] / sc_out
                + W_OVH * a["overhead_bytes_satcom"] / sc_ovh
                + W_TX * a["emission_tx_s"] / sc_tx
                - W_VID * a["video_goodput"] / sc_vid)

    score_obj = cost(b) - cost(p)          # 0 at documented-baseline parity
    shortfall = p.get("gate_shortfall", 0.0)
    score = score_obj - GATE_PENALTY * shortfall
    return {"score": float(score), "score_obj": float(score_obj),
            "gates_pass": bool(p["all_gates_pass"]),
            "gates_pass_abs": bool(p["all_gates_pass_abs"]),
            "gate_shortfall": float(shortfall)}


def oracle_frontier(scenarios, seeds,
                    budgets=(0.0, 0.05, 0.10, 0.25, 0.50, 1.0)):
    """ORACLE FRONTIER (SPEC §13.4/§13.8): for each EMISSION-transmit budget
    (fraction of episode length), the minimum-outage oracle plan whose realised
    emission-tx is within the budget, evaluated through the real engine with
    the SAME planner as the headline (plans are cached, so this is near-free).
    Points are mean over scenarios x seeds; realised emission-tx is reported so
    the budget constraint is auditable. Monotone by construction (a larger
    budget's feasible set is a superset); no post-hoc smoothing."""
    from policies.oracle import Oracle
    # Read every budget's point from ONE bound oracle per (inst, seed) via its
    # engine-evaluated candidate cache (no engine re-runs). standby_tx is the
    # headline plan's (reported for continuity); the frontier axis is emission.
    acc = {bf: {"em": [], "out": []} for bf in budgets}
    for sid in SCENARIO_IDS:
        for inst in scenarios.get(sid, []):
            for seed in seeds:
                inst.prepare(seed)
                o = Oracle()
                o.reset("episode", seed)
                o.bind_instance(inst)
                ntr = max(1, len(inst.transitions))
                for (bf, em, out_s) in o.frontier_points(budgets):
                    acc[bf]["em"].append(em)
                    acc[bf]["out"].append(out_s / ntr)
    pts = []
    for bf in budgets:
        pts.append({"budget_frac": bf,
                    "emission_tx_s": float(np.mean(acc[bf]["em"])),
                    "standby_tx_s": 0.0,
                    "outage_per_transition_s": float(np.mean(acc[bf]["out"]))})
    return pts


def check_oracle_lexicographic(scenarios, seeds, extra_policies=None):
    """SPEC §13.4/§13.8 oracle property. The oracle is the ceiling for the
    make-before-break family under the SAME recovery physics (the documented
    reactive_rehandshake mechanism): it enumerates and engine-evaluates a
    superset of every such policy's schedules, so its outage is <= theirs on
    every run. The STRICT assertion is over those same-mechanism policies
    (always-warm, ATSSS, documented, the diagnostic gamer, and any supplied
    P1/P2). MOBIKE-style roaming uses a DIFFERENT, structurally faster recovery
    mechanism (mobike_migrate, ~1 RTT vs a full re-handshake); on a single-leg
    scenario where make-before-break offers nothing, MOBIKE can legitimately
    beat the reactive-physics oracle — that is a finding (prediction should be
    combined with migration), not a ceiling violation, so it is reported
    separately and never counted as a violation. Returns (violations, n,
    mobike_beats)."""
    from policies.oracle import Oracle
    from policies.always_warm import AlwaysWarm
    from policies.atsss_active_standby import AtsssActiveStandby
    from policies.static_documented import StaticDocumented
    from policies.mobike_roaming import MobikeRoaming
    import policies.diag_fixed_timer as dft
    same_mech = {"always_warm": AlwaysWarm, "atsss": AtsssActiveStandby,
                 "documented": StaticDocumented,
                 "diag_fixed_timer": dft.DiagFixedTimer}
    for nm, cls in (extra_policies or {}).items():
        same_mech[nm] = cls
    def outage(inst, cls, seed):
        rr = run(inst, cls(), seed)
        return float(rr.outage.sum()) * (rr.t_grid[1] - rr.t_grid[0]
                                         if len(rr.t_grid) > 1 else 0.1)
    viol, mobike_beats, n = [], [], 0
    for sid in SCENARIO_IDS:
        for inst in scenarios.get(sid, []):
            for seed in seeds:
                inst.prepare(seed)
                o = Oracle()
                o.reset("episode", seed)
                o.bind_instance(inst)          # cached plan, no engine re-run
                oo = o.plan_outage_s()
                n += 1
                for nm, cls in same_mech.items():
                    ov = outage(inst, cls, seed)
                    if oo > ov + 1e-6:
                        viol.append((sid, inst.inst_id, seed, nm, oo, ov))
                om = outage(inst, MobikeRoaming, seed)
                if oo > om + 1e-6:
                    mobike_beats.append((sid, inst.inst_id, seed, "mobike", oo, om))
    return viol, n, mobike_beats


def main():
    ap = argparse.ArgumentParser(description="KATIM continuity evaluator")
    ap.add_argument("--split", choices=["train", "heldout"], default="train")
    ap.add_argument("--policy", default="policies/policy.py")
    ap.add_argument("--seeds", choices=["train", "heldout"], default=None)
    ap.add_argument("--out", default="results/figures/")
    args = ap.parse_args()

    seeds = SEEDS_HELDOUT if (args.seeds or args.split) == "heldout" else SEEDS_TRAIN
    scen = build_scenarios(args.split)
    pol_cls = load_policy(args.policy)
    base_cls = load_policy("policies/static_documented.py")

    emref_cls = load_policy("policies/always_warm.py")
    rows = run_policy(pol_cls, seeds, scen)
    base_rows = run_policy(base_cls, seeds, scen)
    emref_rows = run_policy(emref_cls, seeds, scen)
    agg = aggregate(rows)
    base_agg = aggregate(base_rows)
    emref_agg = aggregate(emref_rows)
    sc = scalar_score(agg, base_agg, emref_agg)

    print(f"policy={args.policy} split={args.split} seeds={seeds}")
    print(f"scalar score vs documented-static: {sc['score']:.4f} "
          f"(objective {sc['score_obj']:.4f}; feasible-gates_pass={sc['gates_pass']}, "
          f"abs-gates_pass={sc['gates_pass_abs']}, gate_shortfall={sc['gate_shortfall']:.4f})")
    o = agg["overall"]
    print(f"  aoi_p95={o['aoi_p95_s']:.2f}s outage/tr={o['outage_per_transition_s']:.2f}s "
          f"emission_tx={o['emission_tx_s']:.1f}s (standby {o['standby_tx_s']:.1f}s) "
          f"satcom_ovh={o['overhead_bytes_satcom']:.0f}B "
          f"alert_hit(feas)={o['alert_hit_rate_feasible']:.3f} (abs {o['alert_hit_rate']:.3f}) "
          f"video_gp={o['video_goodput']:.3f}")


if __name__ == "__main__":
    main()
