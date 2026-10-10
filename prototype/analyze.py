"""Score one prototype run from the container logs (host side)."""
import json
import sys
from pathlib import Path

import pandas as pd

LOGS = Path(__file__).parent / "logs"
VOICE_PPS, GAP_PKTS = 50, 10          # 10 consecutive lost 20 ms frames = 200 ms dark


def analyze(run, mode):
    v = pd.read_csv(LOGS / "recv_voice.log", names=["run", "seq", "sent", "recv", "size"])
    v = v[v.run == run].drop_duplicates("seq").sort_values("seq")
    rows = [l.rstrip("\n").split(",", 2) for l in open(LOGS / f"ctl_{run}.csv")][1:]
    ctl = pd.DataFrame([r + [""] * (3 - len(r)) for r in rows], columns=["t", "event", "detail"])
    ctl["t"] = ctl.t.astype(float)
    link = pd.read_csv(LOGS / f"link_{run}_hq.csv")
    t_start = v.sent.min()
    n_sent = int(v.seq.max()) + 1 if len(v) else 0
    got = set(v.seq)
    lost = [s for s in range(n_sent) if s not in got]
    runs, cur = [], []
    for s in lost:
        if cur and s != cur[-1] + 1:
            runs.append(cur)
            cur = []
        cur.append(s)
    if cur:
        runs.append(cur)
    dark = [r for r in runs if len(r) >= GAP_PKTS]
    owd = 1000 * (v.recv - v.sent)
    summ = ctl[ctl.event == "summary"].detail.iloc[-1] if (ctl.event == "summary").any() else ""
    kv = dict(x.split("=") for x in summ.split(",")) if summ else {}
    res = {
        "run": run, "mode": mode, "voice_sent": n_sent, "voice_received": len(v),
        "dark_gaps": len(dark),
        "dark_s": round(sum(len(r) for r in dark) / VOICE_PPS, 2),
        "longest_dark_s": round(max((len(r) for r in dark), default=0) / VOICE_PPS, 2),
        "owd_p50_ms": round(float(owd.median()), 1) if len(v) else None,
        "owd_p95_ms": round(float(owd.quantile(0.95)), 1) if len(v) else None,
        "voice_le_150ms": round(float((owd <= 150).sum() / max(n_sent, 1)), 3),
        "voice_le_400ms": round(float((owd <= 400).sum() / max(n_sent, 1)), 3),
        "standby_s": float(kv.get("standby_s", "nan")),
        "bearer_b_tx_bytes": int(kv.get("bearer_b_tx_bytes", 0) or 0),
        "events": ctl[ctl.event.isin(["dead", "predict", "hs_start", "hs_done", "hs_fail", "route", "roam"])]
                  .apply(lambda r: f"{r.t:.1f}s {r.event} {r.detail}", axis=1).tolist(),
        "dark_windows_s": [[round((min(r) / VOICE_PPS), 2), round((max(r) + 1) / VOICE_PPS, 2)] for r in dark],
        "link_unusable_s": int((link.a_usable == 0).sum()),
    }
    (LOGS / f"result_{run}.json").write_text(json.dumps(res, indent=1))
    return res


if __name__ == "__main__":
    r = analyze(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "")
    print(json.dumps({k: v for k, v in r.items()}, indent=1))
