"""Reduced-planes experiment (SPEC §14.4): does P1 matter when fewer QoS planes are configured?
Runs documented, ours (P1+P2, frozen snapshot) and ours without P1 (P2 only) with
P.planes_configured in {1, 2, 8} on TRAIN seeds 0-9. Never touches the held-out split."""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from eval.evaluate import aggregate, load_policy, run_policy  # noqa: E402
from sim.scenarios import SCENARIO_IDS, build_scenarios  # noqa: E402

POLS = [("documented", "policies/static_documented.py"), ("ours_P1+P2", "policies/snap_planes_ours.py"),
        ("ours_P2_only", "policies/variant_p2only.py")]


def job(a):
    n, path, sid, seeds = a
    from sim.params import P
    P.planes_configured = n
    cls = load_policy(path)
    return run_policy(cls, seeds, {sid: build_scenarios("train").get(sid, [])})


def main():
    seeds = list(range(10))
    out = {}
    with ProcessPoolExecutor(max_workers=int(os.environ.get("WORKERS", "3"))) as ex:
        for n in (1, 2, 8):
            for name, path in POLS:
                rows = []
                for r in ex.map(job, [(n, path, sid, seeds) for sid in SCENARIO_IDS]):
                    rows += r
                o = aggregate(rows)["overall"]
                out[f"{n}|{name}"] = {k: round(o[k], 4) for k in ("aoi_p95_s", "aoi_mean_s", "outage_per_transition_s",
                                     "alert_hit_rate_feasible", "voice_within_hard_feasible", "video_goodput", "emission_tx_s")}
                print(n, name, out[f"{n}|{name}"], flush=True)
    json.dump(out, open(os.path.join(HERE, "results", "planes_experiment.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
