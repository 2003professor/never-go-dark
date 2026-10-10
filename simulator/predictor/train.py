"""Train the link-collapse predictor and compile it into the policy (SPEC §14.2).

Offline, privileged training code (never imported by a policy):
  1. Run TRAIN-split episodes through the real engine with a passive collector
     policy that computes features with the policy's own FeatureTracker, so the
     training features are exactly what the policy sees at runtime.
  2. Label every tick where the tunnel rides the primary bearer A and A can
     currently deliver: y = 1 if A becomes unable to deliver (link_ok false for
     >= MIN_OUT_S contiguous) starting within the next H_S seconds. Labels use
     trace ground truth, which is allowed for TRAINING only.
  3. Split TRAIN instances into fit / validation BY TRACE (never by time); the
     held-out split is never touched here.
  4. Fit a small gradient-boosted tree model and a logistic baseline, report
     validation metrics, then COMPILE the boosted trees into a pure-Python
     function (no libraries) and write it into policies/policy.py between the
     MODEL markers. A no-modem model is compiled for the ablation variant.
Usage: python3 -m predictor.train
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from sklearn.ensemble import GradientBoostingClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import average_precision_score, roc_auc_score  # noqa: E402

from policies.interface import Action, Policy  # noqa: E402
from policies.policy import FeatureTracker  # noqa: E402
from sim.engine import run  # noqa: E402
from sim.gateway import SIM_TICK_S  # noqa: E402
from sim.scenarios import SCENARIO_IDS, build_scenarios, link_ok  # noqa: E402

H_S = 5.0             # prediction horizon: covers a GEO handshake (~2.5 s) + margin
MIN_OUT_S = 1.0       # an "inability to deliver" must last at least this long
COLLECT_SEEDS = list(range(10))   # subset of the frozen TRAIN seed set (0..19)
OUT = os.path.join(HERE, "predictor")


class Collector(Policy):
    """Passive: documented-static behaviour; records features each tick."""
    name = "collector"
    reads_messages = False

    def __init__(self, use_modem):
        self.use_modem = use_modem

    def reset(self, scenario_name, seed):
        self.ft = FeatureTracker(use_modem=self.use_modem)
        self.rows, self.cls, self.t0 = [], [], None

    def decide(self, obs):
        if self.t0 is None:
            self.t0 = obs.t_s
        x = self.ft.update(obs)
        cur = obs.bearer_hint.current_bearer_class if obs.bearer_hint else None
        self.rows.append(x)
        self.cls.append("" if cur is None else str(cur.value))
        return Action()


def _val_instance(inst_id: str) -> bool:
    return int(hashlib.sha1(inst_id.encode()).hexdigest(), 16) % 4 == 0


def collect(use_modem: bool):
    scen = build_scenarios("train")
    X, y, grp, split = [], [], [], []
    for sid in SCENARIO_IDS:
        for inst in scen.get(sid, []):
            is_val = _val_instance(inst.inst_id)
            for seed in COLLECT_SEEDS:
                pol = Collector(use_modem)
                rr = run(inst, pol, seed)
                n = len(pol.rows)
                # ground-truth A deliverability on the tick grid (training only)
                a_ok = np.array([link_ok(inst.env(k * SIM_TICK_S).roleA.sample) for k in range(n)])
                classA = getattr(inst, "classA", None) or inst.env(0.0).roleA.bclass
                classA = str(getattr(classA, "value", classA))
                h = int(round(H_S / SIM_TICK_S))
                m = int(round(MIN_OUT_S / SIM_TICK_S))
                bad = ~a_ok
                # start of a >= MIN_OUT_S outage at tick j
                run_start = np.zeros(n, bool)
                cnt = 0
                for j in range(n - 1, -1, -1):
                    cnt = cnt + 1 if bad[j] else 0
                    run_start[j] = bad[j] and cnt >= m and (j == 0 or not bad[j - 1])
                starts = np.flatnonzero(run_start)
                for k in range(n):
                    if pol.cls[k] != classA or not a_ok[k]:
                        continue
                    nxt = starts[starts > k]
                    lab = 1 if (len(nxt) and nxt[0] - k <= h) else 0
                    X.append(pol.rows[k])
                    y.append(lab)
                    grp.append(f"{sid}|{inst.inst_id}|{seed}")
                    split.append(is_val)
    return np.array(X, float), np.array(y, int), np.array(grp), np.array(split, bool)


def event_metrics(p, y, grp, th):
    """Event-level view on validation: lead time of the first alarm before each
    positive run, and the fraction of negative ticks spent alarmed (emission cost)."""
    leads, misses = [], 0
    for g in np.unique(grp):
        idx = np.flatnonzero(grp == g)
        yy, pp = y[idx], p[idx]
        k = 0
        while k < len(yy):
            if yy[k] == 1:
                j = k
                while j < len(yy) and yy[j] == 1:
                    j += 1
                alarm = np.flatnonzero(pp[k:j] >= th)
                if len(alarm):
                    leads.append((j - (k + alarm[0])) * SIM_TICK_S)
                else:
                    misses += 1
                k = j
            else:
                k += 1
    neg = y == 0
    return {"threshold": th, "events": len(leads) + misses, "caught": len(leads), "missed": misses,
            "median_lead_s": float(np.median(leads)) if leads else 0.0,
            "false_alarm_tick_frac": float((p[neg] >= th).mean()) if neg.any() else 0.0}


def compile_gb(clf, names, fn_name="p_collapse"):
    """Emit the boosted trees as plain Python (no libraries)."""
    prior = float(clf.init_.class_prior_[1])
    raw0 = float(np.log(prior / (1 - prior)))
    lr = float(clf.learning_rate)
    lines = [f"def {fn_name}(x):",
             f'    """Compiled gradient-boosted trees ({len(clf.estimators_)} trees, depth '
             f'{clf.max_depth}); inputs = FeatureTracker.NAMES. Generated by predictor/train.py."""',
             f"    z = {raw0!r}"]
    for est in clf.estimators_[:, 0]:
        t = est.tree_

        def emit(node, ind):
            if t.children_left[node] == -1:
                return [f"{' ' * ind}z += {lr * float(t.value[node][0][0])!r}"]
            f, thr = int(t.feature[node]), float(t.threshold[node])
            out = [f"{' ' * ind}if x[{f}] <= {thr!r}:  # {names[f]}"]
            out += emit(t.children_left[node], ind + 4)
            out += [f"{' ' * ind}else:"]
            out += emit(t.children_right[node], ind + 4)
            return out
        lines += emit(0, 4)
    lines += ["    if z < -30.0:", "        return 0.0", "    if z > 30.0:", "        return 1.0",
              "    return 1.0 / (1.0 + math.exp(-z))"]
    return "\n".join(lines)


def train(use_modem: bool):
    t0 = time.time()
    X, y, grp, val = collect(use_modem)
    fit = ~val
    pos_w = (y[fit] == 0).sum() / max(1, (y[fit] == 1).sum())
    w = np.where(y[fit] == 1, pos_w, 1.0)
    gb = GradientBoostingClassifier(n_estimators=60, max_depth=3, learning_rate=0.1,
                                    subsample=0.8, random_state=0)
    gb.fit(X[fit], y[fit], sample_weight=w)
    lr = LogisticRegression(max_iter=2000, class_weight="balanced")
    mu, sd = X[fit].mean(0), X[fit].std(0) + 1e-9
    lr.fit((X[fit] - mu) / sd, y[fit])
    rep = {"use_modem": use_modem, "n_ticks": int(len(y)), "pos_rate": float(y.mean()),
           "n_fit": int(fit.sum()), "n_val": int(val.sum()), "collect_seconds": round(time.time() - t0, 1)}
    if val.any() and y[val].any():
        pg = gb.predict_proba(X[val])[:, 1]
        pl = lr.predict_proba((X[val] - mu) / sd)[:, 1]
        rep.update({"val_auc_gb": float(roc_auc_score(y[val], pg)),
                    "val_ap_gb": float(average_precision_score(y[val], pg)),
                    "val_auc_logreg": float(roc_auc_score(y[val], pl)),
                    "val_ap_logreg": float(average_precision_score(y[val], pl)),
                    "val_event": [event_metrics(pg, y[val], grp[val], th) for th in (0.2, 0.35, 0.5, 0.65, 0.8)]})
    imp = sorted(zip(FeatureTracker.NAMES, gb.feature_importances_), key=lambda a: -a[1])
    rep["feature_importance"] = [[n, round(float(v), 4)] for n, v in imp]
    code = compile_gb(gb, FeatureTracker.NAMES)
    # verify the compiled function reproduces sklearn exactly
    ns = {"math": __import__("math")}
    exec(code, ns)
    probe = X[val][:2000] if val.any() else X[:2000]
    diff = max(abs(ns["p_collapse"](tuple(r)) - q) for r, q in zip(probe, gb.predict_proba(probe)[:, 1]))
    rep["compiled_max_abs_diff"] = float(diff)
    assert diff < 1e-9, diff
    return code, rep


def write_model(path, code):
    src = open(path).read()
    new = re.sub(r"(# >>> MODEL.*?\n).*?(# <<< MODEL)", lambda m: m.group(1) + code + "\n" + m.group(2),
                 src, flags=re.S)
    open(path, "w").write(new)


if __name__ == "__main__":
    reports = {}
    code_m, reports["modem"] = train(True)
    write_model(os.path.join(HERE, "policies", "policy.py"), code_m)
    open(os.path.join(OUT, "model_modem.pyfrag"), "w").write(code_m)
    code_n, reports["no_modem"] = train(False)
    open(os.path.join(OUT, "model_nomodem.pyfrag"), "w").write(code_n)
    json.dump(reports, open(os.path.join(OUT, "report.json"), "w"), indent=1)
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk != "feature_importance"} for k, v in reports.items()}, indent=1))
    print("top features (modem):", reports["modem"]["feature_importance"][:8])
