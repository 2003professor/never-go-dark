"""predictor/events.py — handover-event extraction + validation (SPEC §6.3).

The transition-event rule itself lives in ``sim.scenarios.extract_events``
(one published rule per signal, OR-combined, causal features only). This module
wraps it for the D-2 predictor work and provides the self-contained validation
the spec asks for: precision/recall of the rule against the UCC 5G
``NetworkMode`` switch (ground truth that ships in the trace), used when Berlin
V2X is not available. No future information is used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from sim.bearers import discover_traces
from sim.scenarios import extract_events


def mode_switch_truth(df: pd.DataFrame, tol_s: float = 3.0):
    """Ground-truth transition instants = net_mode flips in the trace."""
    t = df["t_s"].to_numpy(dtype=float)
    nm = df["net_mode"].astype(str).to_numpy()
    truth = [float(t[i]) for i in range(1, len(t))
             if nm[i] and nm[i - 1] and nm[i] != nm[i - 1]]
    return truth


def precision_recall(df: pd.DataFrame, tol_s: float = 3.0):
    """Match extracted events to ground-truth mode flips within ``tol_s``."""
    truth = mode_switch_truth(df)
    events = [t for (t, _r) in extract_events(df)]
    if not truth:
        return None
    matched_truth = set()
    tp = 0
    for e in events:
        for i, g in enumerate(truth):
            if i not in matched_truth and abs(e - g) <= tol_s:
                matched_truth.add(i)
                tp += 1
                break
    precision = tp / len(events) if events else 0.0
    recall = len(matched_truth) / len(truth)
    return dict(precision=precision, recall=recall, n_truth=len(truth),
                n_events=len(events), tp=tp)


def validate_on_ucc5g(max_files: int = 20):
    """Aggregate precision/recall of the §6.3 rule on UCC 5G mode switches."""
    traces = discover_traces().get("ucc5g", {})
    tot_tp = tot_truth = tot_events = 0
    used = 0
    for tid, path in sorted(traces.items())[:max_files]:
        df = pd.read_csv(path, dtype={"net_mode": str})
        pr = precision_recall(df)
        if pr is None:
            continue
        used += 1
        tot_tp += pr["tp"]
        tot_truth += pr["n_truth"]
        tot_events += pr["n_events"]
    return dict(files=used,
                recall=(tot_tp / tot_truth) if tot_truth else None,
                precision=(tot_tp / tot_events) if tot_events else None,
                n_truth=tot_truth, n_events=tot_events)


if __name__ == "__main__":
    print("UCC 5G NetworkMode-switch validation of the §6.3 event rule:")
    print(" ", validate_on_ucc5g())
