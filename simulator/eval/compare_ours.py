"""Score our policy (+ ablation variants) against the frozen baselines — TRAIN only.

Uses the frozen evaluator (run_policy / aggregate / scalar_score). Baseline rows
are cached per seed set so iterating on policy.py only re-runs our policies.
Never touches the held-out split.
usage: python3 -m eval.compare_ours [--seeds 10] [--only policy] [--rebuild-baselines]
"""
import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from eval.evaluate import aggregate, load_policy, run_policy, scalar_score  # noqa: E402
from sim.scenarios import SCENARIO_IDS, build_scenarios  # noqa: E402

BASELINES = [("documented", "policies/static_documented.py"), ("mobike", "policies/mobike_roaming.py"),
             ("atsss", "policies/atsss_active_standby.py"), ("always_warm", "policies/always_warm.py"),
             ("oracle", "policies/oracle.py")]
OURS = [("ours", "policies/policy.py"), ("ours_modem", "policies/variant_modem.py"),
        ("ours_with_p1", "policies/variant_with_p1.py")]


def _job(args):
    path, sid, seeds = args
    cls = load_policy(path)
    scen = build_scenarios("train")
    return run_policy(cls, seeds, {sid: scen.get(sid, [])})


def rows_for(path, seeds, workers):
    jobs = [(path, sid, seeds) for sid in SCENARIO_IDS]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        out = []
        for r in ex.map(_job, jobs):
            out += r
    return out


def fold_of(inst_id):
    """Disjoint trace folds for the loop's keep rule: fold 0/1 by sha1 of the instance id."""
    return int(hashlib.sha1(inst_id.encode()).hexdigest(), 16) % 2


def fold_score(rows, base, fold):
    pick = lambda rs: [r for r in rs if fold_of(r["inst_id"]) == fold]
    ag = aggregate(pick(rows))
    return scalar_score(ag, aggregate(pick(base["documented"])), aggregate(pick(base["always_warm"])))["score"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--only", default=None)
    ap.add_argument("--rebuild-baselines", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--policy", default=None, help="score one policy file only (prints RESULT json)")
    ap.add_argument("--seed-start", type=int, default=0)
    a = ap.parse_args()
    seeds = list(range(a.seed_start, a.seed_start + a.seeds))
    tag = f"s{a.seeds}" if a.seed_start == 0 else f"s{a.seed_start}-{a.seed_start + a.seeds - 1}"
    cache = os.path.join(HERE, "results", f"baseline_rows_train_{tag}.json")
    if os.path.exists(cache) and not a.rebuild_baselines:
        base = json.load(open(cache))
    else:
        base = {n: rows_for(p, seeds, a.workers) for n, p in BASELINES}
        json.dump(base, open(cache, "w"))
    if a.policy:
        rows = rows_for(a.policy, seeds, a.workers)
        ag = aggregate(rows)
        ab = {n: aggregate(r) for n, r in base.items()}
        sc = scalar_score(ag, ab["documented"], ab["always_warm"])
        o = ag["overall"]
        res = {"score": round(sc["score"], 4), "outage_per_tr": round(o["outage_per_transition_s"], 3),
               "emission_tx": round(o["emission_tx_s"], 1), "aoi_p95": round(o["aoi_p95_s"], 2),
               "video_gp": round(o["video_goodput"], 3), "alert_feas": round(o["alert_hit_rate_feasible"], 4),
               "voice_feas": round(o["voice_within_hard_feasible"], 4), "gate_shortfall": round(sc["gate_shortfall"], 4),
               "per_scenario": {s_: [round(ag[s_]["outage_per_transition_s"], 2), round(ag[s_]["emission_tx_s"], 0)]
                                for s_ in SCENARIO_IDS if s_ in ag},
               "seeds": [seeds[0], seeds[-1]],
               "score_fold0": round(fold_score(rows, base, 0), 4),
               "score_fold1": round(fold_score(rows, base, 1), 4)}
        print("RESULT " + json.dumps(res))
        return
    ours = {n: rows_for(p, seeds, a.workers) for n, p in OURS if (a.only is None or n == a.only)}
    allr = {**base, **ours}
    agg = {n: aggregate(r) for n, r in allr.items()}
    lines = ["| Mechanism | AoI p95 (s) | Outage/tr (s) | Emission-tx (s) | Standby-tx (s) | Alert-hit feas | "
             "Voice<=400 feas | Video gp | Score |", "|---|---|---|---|---|---|---|---|---|"]
    for n in allr:
        o = agg[n]["overall"]
        sc = scalar_score(agg[n], agg["documented"], agg["always_warm"])
        lines.append(f"| {n} | {o['aoi_p95_s']:.2f} | {o['outage_per_transition_s']:.2f} | {o['emission_tx_s']:.1f} | "
                     f"{o['standby_tx_s']:.1f} | {o['alert_hit_rate_feasible']:.3f} | {o['voice_within_hard_feasible']:.3f} | "
                     f"{o['video_goodput']:.3f} | {sc['score']:+.3f} |")
    lines += ["", "Outage per transition (s) / emission-tx (s) by scenario:", "",
              "| Mechanism | " + " | ".join(SCENARIO_IDS) + " |", "|---|" + "---|" * len(SCENARIO_IDS)]
    for n in allr:
        cells = [f"{agg[n][s]['outage_per_transition_s']:.2f} / {agg[n][s]['emission_tx_s']:.0f}"
                 if s in agg[n] else "-" for s in SCENARIO_IDS]
        lines.append(f"| {n} | " + " | ".join(cells) + " |")
    txt = f"# Ours vs baselines — TRAIN split, seeds 0..{a.seeds - 1} (held-out untouched)\n\n" + "\n".join(lines) + "\n"
    open(os.path.join(HERE, "results", f"ours_train_s{a.seeds}.md"), "w").write(txt)
    print(txt)


if __name__ == "__main__":
    main()
