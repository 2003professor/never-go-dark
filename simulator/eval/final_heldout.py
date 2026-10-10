"""ONE-SHOT final test (pre-registered, FINDINGS.md §0c): held-out traces, fresh seeds 300-319.
Primary = policies/policy.py (shipped, no-modem); optional = policies/variant_modem.py.
Baselines run on the same instances/seeds. Run once; results reported as-is."""
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from eval.evaluate import aggregate, load_policy, run_policy, scalar_score  # noqa: E402
from sim.scenarios import SCENARIO_IDS, build_scenarios  # noqa: E402

SEEDS = list(range(300, 320))
POLS = [("documented", "policies/static_documented.py"), ("mobike", "policies/mobike_roaming.py"),
        ("atsss", "policies/atsss_active_standby.py"), ("always_warm", "policies/always_warm.py"),
        ("oracle", "policies/oracle.py"), ("never_go_dark", "policies/policy.py"),
        ("never_go_dark_modem", "policies/variant_modem.py")]
FROZEN = ["policies/policy.py", "policies/variant_modem.py", "eval/evaluate.py", "eval/metrics.py",
          "sim/engine.py", "sim/gateway.py", "sim/bearers.py", "sim/scenarios.py", "sim/traffic.py", "sim/params.py"]


def job(a):
    path, sid = a
    cls = load_policy(path)
    return run_policy(cls, SEEDS, {sid: build_scenarios("heldout").get(sid, [])})


def main():
    out_json = os.path.join(HERE, "results", "final_heldout_s300-319.json")
    assert not os.path.exists(out_json), "one-shot test already run; refusing to run again"
    hashes = {f: hashlib.sha256(open(os.path.join(HERE, f), "rb").read()).hexdigest() for f in FROZEN}
    n_inst = {sid: len(build_scenarios("heldout").get(sid, [])) for sid in SCENARIO_IDS}
    t0 = time.time()
    rows = {}
    with ProcessPoolExecutor(max_workers=8) as ex:
        for name, path in POLS:
            rows[name] = [r for part in ex.map(job, [(path, sid) for sid in SCENARIO_IDS]) for r in part]
            print(name, "done", round(time.time() - t0), "s", flush=True)
    agg = {n: aggregate(r) for n, r in rows.items()}
    res = {"seeds": [SEEDS[0], SEEDS[-1]], "split": "heldout", "instances": n_inst, "frozen_sha256": hashes,
           "runtime_s": round(time.time() - t0), "overall": {}, "per_scenario": {}}
    for n in rows:
        o = agg[n]["overall"]
        sc = scalar_score(agg[n], agg["documented"], agg["always_warm"])
        res["overall"][n] = {"score": sc["score"], "gates_pass_feasible": sc["gates_pass"], **{k: o[k] for k in (
            "aoi_p95_s", "outage_per_transition_s", "emission_tx_s", "standby_tx_s", "alert_hit_rate_feasible",
            "alert_hit_rate", "voice_within_hard_feasible", "video_goodput")}}
        res["per_scenario"][n] = {s: [agg[n][s]["outage_per_transition_s"], agg[n][s]["emission_tx_s"]]
                                  for s in SCENARIO_IDS if s in agg[n]}
    json.dump(res, open(out_json, "w"), indent=1)
    print(json.dumps(res["overall"], indent=1))


if __name__ == "__main__":
    main()
