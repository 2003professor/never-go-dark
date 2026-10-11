"""Side-by-side timeline of prototype runs (one row per mechanism) from the container logs."""
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

LOGS = Path(__file__).parent / "logs"
NAMES = {"reactive": "Documented reactive (emulated 9001-R)", "mobike": "MOBIKE-style roaming (RFC 4555)",
         "atsss": "ATSSS-style active-standby (TS 23.501)", "p2": "Ours v0: hand-written trigger",
         "ngd": "Never Go Dark: shipped policy file, unmodified"}


def ctl_events(run):
    rows = [l.rstrip("\n").split(",", 2) for l in open(LOGS / f"ctl_{run}.csv")][1:]
    return pd.DataFrame([r + [""] * (3 - len(r)) for r in rows], columns=["t", "event", "detail"]).astype({"t": float})


def standby_spans(ev, dur):
    spans, up, carrying_b = [], None, False
    for _, r in ev.iterrows():
        if r.event == "hs_start" and r.detail == "wgb" and up is None and not carrying_b:
            up = r.t
        elif r.event == "route":
            if r.detail == "wgb":
                carrying_b = True
                if up is not None:
                    spans.append((up, r.t)); up = None
            elif carrying_b and r.detail == "wga":
                carrying_b = False; up = r.t
        elif r.event == "standby_down" and up is not None:
            spans.append((up, r.t)); up = None
    if up is not None and not carrying_b:
        spans.append((up, dur))
    return spans


def main(suffix, out):
    modes = [m for m in ["reactive", "mobike", "atsss", "p2", "ngd"] if (LOGS / f"result_{m}_{suffix}.json").exists()]
    fig, axes = plt.subplots(len(modes) + 1, 1, figsize=(13, 9.5), sharex=True,
                             gridspec_kw={"height_ratios": [1.3] + [1] * len(modes)})
    link = pd.read_csv(LOGS / f"link_{modes[0]}_{suffix}_hq.csv")
    ax = axes[0]
    ax.step(link.k, link.a_rate_mbit.fillna(0), where="post", color="#2F6FA8", lw=1.8, label="5G/LTE capacity (real trace)")
    ax.set_ylabel("Mbit/s")
    ax2 = ax.twinx()
    ax2.plot(link.k, link.rsrp_dbm, color="#9A5214", lw=1.2, ls="--", label="signal RSRP (dBm)")
    ax2.set_ylabel("RSRP dBm", color="#9A5214")
    ax.set_title("Bearer A: real UCC 5G drive (27 Feb 2020) → 5G collapses to 3G at t≈44 s. Bearer B: GEO satellite model (600 ms RTT)", fontsize=10)
    ax.legend(loc="upper right", fontsize=8); ax2.legend(loc="center right", fontsize=8)
    for i, m in enumerate(modes, start=1):
        run = f"{m}_{suffix}"
        r = json.load(open(LOGS / f"result_{run}.json"))
        ev = ctl_events(run)
        ax = axes[i]
        for s, e in standby_spans(ev, 90):
            ax.axvspan(s, e, ymin=0.05, ymax=0.35, color="#E08A2E", alpha=0.55, lw=0)
        on_b = False
        for _, rr in ev[ev.event == "route"].iterrows():
            ax.axvline(rr.t, color="#14202B", lw=0.6, alpha=0.4)
        for s, e in r["dark_windows_s"]:
            ax.axvspan(s, e, ymin=0.45, ymax=0.95, color="#C0392B", lw=0)
        ax.set_yticks([])
        ax.text(0.5, 0.80, f"{NAMES[m]}   |   voice dark {r['dark_s']:.1f} s (longest {r['longest_dark_s']:.1f} s)   |   standby link up {r['standby_s']:.0f} s",
                transform=ax.transAxes, fontsize=9.5, va="center", ha="left",
                bbox=dict(facecolor="white", edgecolor="#DDD8CB", boxstyle="round,pad=0.25"))
    axes[-1].set_xlabel("time (s)")
    from matplotlib.patches import Patch
    axes[1].legend(handles=[Patch(color="#C0392B", label="voice dark (≥200 ms of lost frames)"),
                            Patch(color="#E08A2E", alpha=0.55, label="standby tunnel up, not carrying (detectable emissions)")],
                   loc="lower right", fontsize=8)
    fig.suptitle("Prototype: real WireGuard tunnels, real 5G trace — four continuity mechanisms on the same collapse", fontsize=12)
    fig.tight_layout()
    fig.savefig(out, dpi=140)
    print(out)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "2", sys.argv[2] if len(sys.argv) > 2 else str(LOGS.parent / "prototype_compare.png"))
