"""Deliverable figures for the PDF/video (results/figures/final/).

Results charts use the ONE-SHOT held-out results (results/final_heldout_s300-319.json);
nothing is re-run on held-out data. The timeline is an illustrative TRAINING-split
example, labelled as such. Colours follow the team deck: blue = documented design /
existing mechanisms, orange = Never Go Dark, green = oracle ceiling.
usage: python3 report_figures.py
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import Patch

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
OUT = os.path.join(HERE, "results", "figures", "final")
os.makedirs(OUT, exist_ok=True)

INK, PAPER, MUTED = "#14202B", "#F3F1EA", "#5A6570"
COL = {"documented": "#2F6FA8", "mobike": "#6FA0C8", "atsss": "#8A949C", "always_warm": "#B9C6CF",
       "never_go_dark": "#E08A2E", "never_go_dark_modem": "#F0C08A", "oracle": "#2E7D32"}
NAME = {"documented": "Documented reactive", "mobike": "MOBIKE-style (RFC 4555)", "atsss": "ATSSS-style (TS 23.501)",
        "always_warm": "Always-warm", "never_go_dark": "Never Go Dark (ours)",
        "never_go_dark_modem": "Ours + modem hint", "oracle": "Oracle (perfect foresight)"}
SC_SHORT = {"S1_lte5g_walk": "5G walk\n(single link)", "S2_5g_leo_drive": "5G→LEO\ndrive",
            "S3_5g_geo_drive": "5G→GEO\ndrive", "S4_wifi_edge": "Wi-Fi\nedge", "S5_mptcp_hsr": "High-speed\nrail",
            "S6_collective_5g_geo": "Collective\n5G→GEO", "S7_lumos_walk": "mmWave\nwalk", "S8_lumos_drive": "mmWave\ndrive"}
HO = json.load(open(os.path.join(HERE, "results", "final_heldout_s300-319.json")))
FOOT = ("Held-out traces × fresh seeds 300–319, run once (hashes in results/final_heldout_s300-319.json). "
        "Real measured traces; GEO = measurement-parameterised model; some dual-link scenarios stitched from independent traces.")

plt.rcParams.update({"font.size": 10, "axes.edgecolor": MUTED, "axes.labelcolor": INK, "xtick.color": INK,
                     "ytick.color": INK, "axes.titleweight": "bold", "axes.titlesize": 12})


def foot(fig, text=FOOT):
    fig.text(0.01, 0.01, text, fontsize=7, color=MUTED, ha="left", va="bottom", wrap=True)


def fig_tradeoff():
    o = HO["overall"]
    fig, ax = plt.subplots(figsize=(9.5, 6))
    order = ["documented", "mobike", "atsss", "always_warm", "oracle", "never_go_dark"]
    for k in order:
        x, y = o[k]["emission_tx_s"], o[k]["outage_per_transition_s"]
        big = k == "never_go_dark"
        ax.scatter(x, y, s=360 if big else 170, color=COL[k], edgecolor=INK if big else "white", lw=1.5, zorder=4)
        dx, dy, ha = 3, 0.07, "left"
        if k == "always_warm":
            dy = -0.13
        if k == "atsss":
            dy = 0.10
        ax.annotate(f"{NAME[k]}\n{y:.2f} s outage · {x:.0f} s airtime", (x, y), (x + dx, y + dy), fontsize=9,
                    color=INK, ha=ha, fontweight="bold" if big else "normal")
    a, n = o["atsss"], o["never_go_dark"]
    ax.annotate("", (n["emission_tx_s"] + 2, n["outage_per_transition_s"]), (a["emission_tx_s"] - 3, a["outage_per_transition_s"]),
                arrowprops=dict(arrowstyle="->", color=COL["never_go_dark"], lw=2.2))
    ax.text((a["emission_tx_s"] + n["emission_tx_s"]) / 2, 0.62,
            f"−{100 * (1 - n['emission_tx_s'] / a['emission_tx_s']):.0f}% airtime,\nsame or better continuity",
            color="#9A5214", ha="center", fontsize=10, fontweight="bold")
    ax.axvspan(0, 45, ymin=0, ymax=0.32, color=COL["never_go_dark"], alpha=0.07)
    ax.text(2, 0.06, "target corner: little outage, little airtime", color="#9A5214", fontsize=8.5)
    ax.set_xlim(-2, 125)
    ax.set_ylim(0, 2.9)
    ax.set_xlabel("standby/satellite airtime per episode (s): detectable emissions, lower is better")
    ax.set_ylabel("outage per transition (s): lower is better")
    ax.set_title("Continuity vs emissions on unseen data")
    ax.grid(alpha=0.25)
    foot(fig)
    fig.tight_layout(rect=[0, 0.04, 1, 1])
    fig.savefig(os.path.join(OUT, "fig1_tradeoff_heldout.png"), dpi=170)
    plt.close(fig)


def fig_scenarios():
    ps = HO["per_scenario"]
    mechs = ["documented", "mobike", "atsss", "never_go_dark", "oracle"]
    sids = [s for s in SC_SHORT if s != "S1_lte5g_walk"]
    x = np.arange(len(sids))
    w = 0.16
    fig, (ax, ax2) = plt.subplots(2, 1, figsize=(11, 7.2), sharex=True, gridspec_kw={"height_ratios": [1.4, 1]})
    for i, k in enumerate(mechs):
        ax.bar(x + (i - 2) * w, [min(ps[k][s][0], 5.0) for s in sids], w, color=COL[k], label=NAME[k],
               edgecolor=INK if k == "never_go_dark" else "none", lw=1.0)
        ax2.bar(x + (i - 2) * w, [ps[k][s][1] for s in sids], w, color=COL[k],
                edgecolor=INK if k == "never_go_dark" else "none", lw=1.0)
    for j, s in enumerate(sids):
        for i, k in enumerate(mechs):
            if ps[k][s][0] > 5.0:
                ax.text(x[j] + (i - 2) * w, 5.05, f"{ps[k][s][0]:.1f}", ha="center", fontsize=7, color=INK)
    ax.set_ylabel("outage per transition (s)")
    ax.set_ylim(0, 5.5)
    ax.set_title("Per scenario on unseen data: outage (top) and standby airtime (bottom)")
    ax.legend(ncol=5, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, 1.0))
    ax2.set_ylabel("standby airtime (s)")
    ax2.set_xticks(x)
    ax2.set_xticklabels([SC_SHORT[s] for s in sids], fontsize=9)
    for a_ in (ax, ax2):
        a_.grid(axis="y", alpha=0.25)
    foot(fig, FOOT + " Single-link scenario omitted (no standby exists; all mechanisms equal).")
    fig.tight_layout(rect=[0, 0.05, 1, 1])
    fig.savefig(os.path.join(OUT, "fig2_scenarios_heldout.png"), dpi=170)
    plt.close(fig)


def fig_headline():
    o = HO["overall"]
    mechs = ["documented", "mobike", "atsss", "always_warm", "never_go_dark", "oracle"]
    panels = [("outage_per_transition_s", "Outage per transition (s)", False),
              ("emission_tx_s", "Standby airtime (s)", False),
              ("aoi_p95_s", "Location staleness p95 (s)", False),
              ("alert_hit_rate_feasible", "Alerts on time", True)]
    fig, axes = plt.subplots(1, 4, figsize=(13, 4.2))
    for ax, (key, title, frac) in zip(axes, panels):
        vals = [o[k][key] for k in mechs]
        bars = ax.barh(range(len(mechs)), vals, color=[COL[k] for k in mechs],
                       edgecolor=[INK if k == "never_go_dark" else "none" for k in mechs])
        for b, v in zip(bars, vals):
            ax.text(b.get_width(), b.get_y() + b.get_height() / 2, f" {v:.3f}" if frac else f" {v:.2f}" if v < 10 else f" {v:.0f}",
                    va="center", fontsize=8.5)
        ax.set_yticks(range(len(mechs)))
        ax.set_yticklabels([NAME[k] for k in mechs] if ax is axes[0] else [], fontsize=8.5)
        ax.invert_yaxis()
        ax.set_title(title, fontsize=10)
        if frac:
            ax.set_xlim(0.9, 1.005)
        else:
            ax.set_xlim(0, max(vals) * 1.25)
        ax.grid(axis="x", alpha=0.25)
    fig.suptitle("Never Go Dark vs existing mechanisms on unseen data", fontweight="bold")
    foot(fig)
    fig.tight_layout(rect=[0, 0.06, 1, 0.95])
    fig.savefig(os.path.join(OUT, "fig3_headline_heldout.png"), dpi=170)
    plt.close(fig)


def fig_planes():
    v2 = json.load(open(os.path.join(HERE, "results", "planes_experiment.json")))
    v1 = json.load(open(os.path.join(HERE, "results", "planes_experiment_v1_thrashing.json")))
    planes = ["8", "2", "1"]
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(12, 4.6))
    series = [("documented", v2, "Documented reactive", COL["documented"]),
              ("ours_P2_only", v2, "Never Go Dark (P2)", COL["never_go_dark"]),
              ("ours_P1+P2", v1, "+ naive freshness dropping (thrashing)", "#C0392B")]
    x = np.arange(len(planes))
    w = 0.26
    for i, (key, src, lab, c) in enumerate(series):
        a1.bar(x + (i - 1) * w, [src[f"{p}|{key}"]["aoi_p95_s"] for p in planes], w, color=c, label=lab)
        if key != "ours_P1+P2":
            a2.bar(x + (i - 0.5) * w * 1.2, [src[f"{p}|{key}"]["voice_within_hard_feasible"] for p in planes],
                   w * 1.2, color=c, label=lab)
    a1.set_xticks(x)
    a1.set_xticklabels(["8 planes\n(documented)", "2 planes", "1 shared queue"])
    a1.set_ylabel("location staleness p95 (s)")
    a1.set_title("Freshness: separate planes matter; never drop in-flight updates")
    a1.legend(fontsize=8)
    a2.set_xticks(x)
    a2.set_xticklabels(["8 planes\n(documented)", "2 planes", "1 shared queue"])
    a2.set_ylabel("voice frames within 400 ms")
    a2.set_ylim(0, 1.05)
    a2.set_title("Voice collapses without separate QoS planes")
    for a_ in (a1, a2):
        a_.grid(axis="y", alpha=0.25)
    foot(fig, "Training split, seeds 0–9. 'Naive freshness dropping' = P1 v1, which discarded superseded position "
              "reports still in transmission (results/planes_experiment_v1_thrashing.json).")
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(os.path.join(OUT, "fig5_reduced_planes.png"), dpi=170)
    plt.close(fig)


def fig_predictor():
    rep = json.load(open(os.path.join(HERE, "predictor", "report.json")))["no_modem"]
    ev = rep["val_event"]
    fa = [e["false_alarm_tick_frac"] for e in ev]
    catch = [e["caught"] / e["events"] for e in ev]
    th = [e["threshold"] for e in ev]
    fig, ax = plt.subplots(figsize=(7.5, 5))
    ax.plot(fa, catch, "-o", color=COL["never_go_dark"], lw=2)
    for f, c, t in zip(fa, catch, th):
        ax.annotate(f"p ≥ {t}", (f, c), (f + 0.015, c - 0.06), fontsize=8.5, color=INK)
    ax.set_xlabel("share of healthy time spent warned (false-alarm ticks)")
    ax.set_ylabel("share of real link collapses warned ≥ ~5 s ahead")
    ax.set_title(f"Collapse predictor: AUC {rep['val_auc_gb']:.2f} (logistic baseline {rep['val_auc_logreg']:.2f})")
    ax.set_xlim(0, 0.85)
    ax.set_ylim(0, 1.05)
    ax.grid(alpha=0.25)
    foot(fig, "Gradient-boosted trees (60 trees, depth 3) on the gateway's own telemetry only; compiled to plain Python. "
              "Validation = held-aside training traces (by trace). Adaptive per-link thresholds are used at runtime.")
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(os.path.join(OUT, "fig6_predictor.png"), dpi=170)
    plt.close(fig)


def fig_timeline():
    """Illustrative TRAINING-split episode: same real event, four mechanisms."""
    from eval.evaluate import load_policy
    from eval.metrics import compute_metrics
    from figures import _bands
    from sim.engine import run
    from sim.scenarios import build_scenarios
    mechs = [("documented", "policies/static_documented.py"), ("mobike", "policies/mobike_roaming.py"),
             ("atsss", "policies/atsss_active_standby.py"), ("never_go_dark", "policies/policy.py")]
    scen = build_scenarios("train")
    pick = None
    # criterion: reactive mechanisms go dark, ours does not, AND ours uses well under half
    # of ATSSS's standby airtime (the two claims the figure illustrates)
    for sid in ("S8_lumos_drive", "S2_5g_leo_drive", "S3_5g_geo_drive", "S6_collective_5g_geo", "S5_mptcp_hsr"):
        for inst in scen.get(sid, []):
            for seed in range(5):
                res = {k: compute_metrics(run(inst, load_policy(p)(), seed)) for k, p in mechs}
                if (res["documented"]["outage_per_transition_s"] > 2.0 and res["mobike"]["outage_per_transition_s"] > 0.5
                        and res["never_go_dark"]["outage_per_transition_s"] < 0.5
                        and res["never_go_dark"]["standby_tx_s"] < 0.5 * res["atsss"]["standby_tx_s"]):
                    pick = (sid, inst, seed)
                    break
            if pick:
                break
        if pick:
            break
    sid, inst, seed = pick
    fig, axes = plt.subplots(len(mechs), 1, figsize=(11, 7.5), sharex=True)
    for ax, (k, p) in zip(axes, mechs):
        rr = run(inst, load_policy(p)(), seed)
        m = compute_metrics(rr)
        t = rr.t_grid
        dark = rr.tunnel_dark if rr.tunnel_dark is not None else rr.outage
        stall = rr.stall if rr.stall is not None else np.zeros_like(rr.outage)
        _bands(ax, t, dark | stall, 0.55, 0.35, "#C0392B")
        _bands(ax, t, rr.standby_warming | (rr.standby_ready & ~rr.outage), 0.1, 0.35, COL["never_go_dark"])
        ax.set_ylim(0, 1)
        ax.set_yticks([])
        if inst.transitions:
            ax.axvline(inst.transitions[0], color=INK, ls="--", lw=1)
        ax.text(0.005, 0.93, f"{NAME[k]}: dark {m['outage_per_transition_s']:.1f} s per transition · standby up {m['standby_tx_s']:.0f} s",
                transform=ax.transAxes, fontsize=9.5, va="top",
                bbox=dict(boxstyle="round", fc="white", ec=COL[k]))
    axes[-1].set_xlabel("time (s); dashed line = real link-degradation event taken from the measured trace")
    axes[0].legend(handles=[Patch(fc="#C0392B", label="tunnel dark / no delivery (outage)"),
                            Patch(fc=COL["never_go_dark"], label="standby tunnel up (detectable airtime)")],
                   loc="upper right", fontsize=8)
    fig.suptitle(f"Illustrative episode (training split): {SC_SHORT[sid].replace(chr(10), ' ')}, same real event for every mechanism",
                 fontweight="bold")
    foot(fig, f"Illustrative training-split episode ({sid}, seed {seed}); chosen to show a clear dark gap for the reactive "
              "mechanisms. Representative numbers are in the held-out tables, not this example.")
    fig.tight_layout(rect=[0, 0.04, 1, 0.96])
    fig.savefig(os.path.join(OUT, "fig4_timeline_illustrative.png"), dpi=170)
    plt.close(fig)
    return sid, seed


if __name__ == "__main__":
    only = sys.argv[1:] or ["all"]
    if "all" in only:
        fig_tradeoff(); fig_scenarios(); fig_headline(); fig_planes(); fig_predictor()
    if "all" in only or "timeline" in only:
        print("timeline:", fig_timeline())
    print("written to", OUT)
