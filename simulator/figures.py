"""Figures for the baseline evaluation (SPEC run_baselines deliverable).

(1) transition timeline: per mechanism, tunnel/link state, standby warming,
    alert delivery and position AoI through one representative transition.
(2) outage-seconds per mechanism bar chart.
(3) standby-transmit-seconds vs outage scatter (the emissions trade-off).
"""
from __future__ import annotations

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

from eval.evaluate import load_policy
from eval.metrics import compute_metrics
from sim.engine import run
from sim.gateway import SIM_TICK_S
from sim.scenarios import SCENARIO_CONSTRUCTION, SCENARIO_IDS

# SPEC §3.6/§3.7: every figure states how its data were constructed.
DATA_CAPTION = (
    "Data: S1 real UCC 5G Download replay | S2 STITCHED UCC 5G + Starlink LEO "
    "(measured independently, only time-aligned) | S3/S6 UCC 5G replay + GEO "
    "measurement-parameterised MODEL (not a replay; 94% availability) | S4 "
    "STITCHED Meireles Wi-Fi + UCC 4G | S5 real simultaneous HSR-MPTCP subflows."
)
# outage (SPEC §8.4) = tunnel dark OR tunnel up over ~zero deliverable capacity
OUTAGE_NOTE = ("Outage = seconds no plane can deliver (SPEC §8.4): tunnel dark "
               "OR tunnel up over <2.4 kbps deliverable capacity (stall).")


def _caption(fig, text, y=0.005, size=7):
    fig.text(0.5, y, text, ha="center", va="bottom", fontsize=size,
             wrap=True, color="#333333")

MECHS = [
    ("static_documented", "Documented KATIM (reactive)", "#c23b22"),
    ("mobike_roaming", "MOBIKE-style (RFC 4555)", "#e08214"),
    ("atsss_active_standby", "ATSSS-style (TS 23.501)", "#1f78b4"),
    ("always_warm", "Always-warm", "#6a51a3"),
    ("oracle", "Oracle (perfect foresight)", "#2d8a3e"),
]
PATHS = {
    "static_documented": "policies/static_documented.py",
    "mobike_roaming": "policies/mobike_roaming.py",
    "atsss_active_standby": "policies/atsss_active_standby.py",
    "always_warm": "policies/always_warm.py",
    "oracle": "policies/oracle.py",
}


def _mean_aoi_per_tick(rr):
    plis = [m for m in rr.messages if m.data_type == "pli"]
    srcs = sorted({m.source_id for m in plis if m.source_id is not None})
    n = len(rr.t_grid)
    if not srcs:
        return np.zeros(n)
    acc = np.zeros(n)
    for r in srcs:
        dl = sorted(((m.delivered_t_s, m.enqueue_t_s) for m in plis
                     if m.source_id == r and m.delivered_t_s is not None),
                    key=lambda x: x[0])
        j = 0
        fresh = None
        for k in range(n):
            t = rr.t_grid[k]
            while j < len(dl) and dl[j][0] <= t:
                g = dl[j][1]
                fresh = g if fresh is None or g > fresh else fresh
                j += 1
            acc[k] += (t - fresh) if fresh is not None else t
    return acc / len(srcs)


def _pick_timeline_instance(scen):
    for sid in ("S6_collective_5g_geo", "S3_5g_geo_drive", "S2_5g_leo_drive"):
        if scen.get(sid):
            return sid, scen[sid][0]
    # fallback: first non-empty
    for sid, insts in scen.items():
        if insts:
            return sid, insts[0]
    raise RuntimeError("no scenario instances to plot")


def timeline_figure(scen, path, seed=0):
    sid, inst = _pick_timeline_instance(scen)
    fig, axes = plt.subplots(len(MECHS), 1, figsize=(11, 10), sharex=True)
    t_x = inst.transitions[0] if inst.transitions else None
    for ax, (name, label, color) in zip(axes, MECHS):
        rr = run(inst, load_policy(PATHS[name])(), seed)
        m = compute_metrics(rr)
        t = rr.t_grid
        aoi = _mean_aoi_per_tick(rr)
        ax.plot(t, aoi, color=color, lw=1.6, label="mean location AoI")
        ax.set_ylim(0, max(5, float(np.max(aoi)) * 1.1))
        ax.set_ylabel("AoI (s)", fontsize=8)
        # status strip at the bottom: outage (red), standby warming (orange),
        # standby ready (light blue). drawn as a band under the AoI axis.
        ymax = ax.get_ylim()[1]
        band_y, band_h = -0.18 * ymax, 0.12 * ymax
        ax.set_ylim(band_y - 0.05 * ymax, ymax)
        dark = rr.tunnel_dark if rr.tunnel_dark is not None else rr.outage
        stall = rr.stall if rr.stall is not None else np.zeros_like(rr.outage)
        _bands(ax, t, dark, band_y, band_h, "#c23b22")
        _bands(ax, t, stall, band_y, band_h, "#7f0000")
        _bands(ax, t, rr.standby_warming, band_y, band_h, "#e08214")
        _bands(ax, t, rr.standby_ready & ~rr.outage, band_y, band_h, "#9ecae1")
        # alert deliveries: green if within deadline, red if late/lost
        for mm in rr.messages:
            if mm.data_type != "alert":
                continue
            if mm.delivered_t_s is None:
                ax.scatter([mm.deadline_t_s or mm.enqueue_t_s], [ymax * 0.9],
                           marker="x", color="red", s=28, zorder=5)
            else:
                hit = (mm.deadline_t_s is not None
                       and mm.delivered_t_s <= mm.deadline_t_s)
                ax.scatter([mm.delivered_t_s], [ymax * 0.9], marker="v",
                           color=("green" if hit else "red"), s=22, zorder=5)
        if t_x is not None:
            ax.axvline(t_x, color="black", ls="--", lw=1, alpha=0.6)
        ax.text(0.01, 0.92, f"{label}  (outage {m['outage_per_transition_s']:.1f}s, "
                f"AoI p95 {m['aoi_p95_s']:.1f}s, standby {m['standby_tx_s']:.0f}s)",
                transform=ax.transAxes, fontsize=8.5, va="top",
                bbox=dict(boxstyle="round", fc="white", ec=color, alpha=0.8))
        ax.grid(True, alpha=0.2)
    axes[-1].set_xlabel("time (s)   — dashed line = REAL degradation event "
                        "extracted from the A-leg trace (SPEC §6.3), not a "
                        "scenario constant")
    legend = [Patch(fc="#c23b22", label="tunnel dark (outage)"),
              Patch(fc="#7f0000", label="stall: up, ~0 capacity (outage)"),
              Patch(fc="#e08214", label="standby warming"),
              Patch(fc="#9ecae1", label="standby ready"),
              plt.Line2D([], [], marker="v", color="green", ls="", label="alert on time"),
              plt.Line2D([], [], marker="v", color="red", ls="", label="alert late/lost")]
    axes[0].legend(handles=legend, fontsize=7, loc="upper right", ncol=2)
    fig.suptitle(f"Transition timeline — {sid}: {inst.label}\n"
                 f"[{SCENARIO_CONSTRUCTION.get(sid, '')}]", fontsize=10)
    _caption(fig, OUTAGE_NOTE + "  " + DATA_CAPTION, size=6.5)
    fig.tight_layout(rect=[0, 0.03, 1, 0.97])
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _bands(ax, t, mask, y, h, color):
    mask = np.asarray(mask, dtype=bool)
    runs = []
    s = None
    for i, v in enumerate(mask):
        if v and s is None:
            s = i
        elif not v and s is not None:
            runs.append((t[s], t[i - 1] - t[s] + SIM_TICK_S))
            s = None
    if s is not None:
        runs.append((t[s], t[-1] - t[s] + SIM_TICK_S))
    if runs:
        ax.broken_barh(runs, (y, h), facecolors=color)


def outage_bar_figure(results, order, pretty, path):
    """Grouped bars: MEDIAN outage per transition by scenario x mechanism, with
    IQR (p25-p75) whiskers over runs (never negative — SPEC §13.7, fix G). The
    median + IQR replace a mean +/- std whose whiskers dipped below zero
    (impossible for seconds) over the bimodal scenario mixture."""
    colors = {n: c for n, _, c in MECHS}
    groups = [sid for sid in SCENARIO_IDS
              if any(r["scenario"] == sid for r in results[order[0]])] + ["overall"]
    nm = len(order)
    width = 0.8 / nm
    x = np.arange(len(groups))
    fig, ax = plt.subplots(figsize=(13, 5.4))
    for j, n in enumerate(order):
        meds, lo, hi = [], [], []
        for g in groups:
            vals = np.array([r["outage_per_transition_s"] for r in results[n]
                             if g == "overall" or r["scenario"] == g])
            if vals.size == 0:
                vals = np.zeros(1)
            md = float(np.median(vals))
            p25, p75 = np.percentile(vals, [25, 75])
            meds.append(md)
            lo.append(max(0.0, md - p25))
            hi.append(max(0.0, p75 - md))
        xs = x - 0.4 + width * (j + 0.5)
        ax.bar(xs, meds, width, color=colors.get(n, "#888"),
               label=pretty[n], edgecolor="white", lw=0.5)
        ax.errorbar(xs, meds, yerr=[lo, hi], fmt="none", ecolor="#222222",
                    elinewidth=0.8, capsize=2)
    ax.set_xticks(x)
    ax.set_xticklabels([g.replace("_", "\n", 1) for g in groups], fontsize=7.5)
    ax.set_ylim(bottom=0)
    ax.set_ylabel("Outage s per transition\n(bar = median; whisker = IQR p25-p75 over runs)")
    ax.set_title("Outage per bearer transition (no plane can deliver), by "
                 "scenario and mechanism — TRAIN split")
    ax.grid(True, axis="y", alpha=0.3)
    ax.legend(fontsize=8, ncol=3, loc="upper left")
    _caption(fig, OUTAGE_NOTE + "\n" + DATA_CAPTION, size=6.5)
    fig.tight_layout(rect=[0, 0.06, 1, 1])
    fig.savefig(path, dpi=130)
    plt.close(fig)


def emissions_scatter_figure(aggs, order, pretty, path, frontier=None):
    """Outage vs EMISSION-transmit, with the ORACLE FRONTIER curve (SPEC
    §13.4/§13.8): the minimum achievable outage at each emission budget. ATSSS /
    always-warm sit well ABOVE the frontier (they reach low outage only by
    spending ~full-episode emission); the oracle reaches the same outage near
    the bottom; our future policy is plotted against the curve, whose
    bottom-left corner is the target."""
    colors = {n: c for n, _, c in MECHS}
    markers = {"static_documented": "o", "mobike_roaming": "s",
               "atsss_active_standby": "D", "always_warm": "^", "oracle": "*"}
    fig, ax = plt.subplots(figsize=(8.8, 6.2))
    if frontier:
        fx = [p["outage_per_transition_s"] for p in frontier]
        fy = [p.get("emission_tx_s", p.get("standby_tx_s", 0.0)) for p in frontier]
        ax.plot(fx, fy, "-", color="#2d8a3e", lw=1.4, alpha=0.8, zorder=2,
                label="oracle frontier (min outage per emission budget)")
        for p in frontier:
            ax.annotate(f"{int(round(p['budget_frac']*100))}%",
                        (p["outage_per_transition_s"],
                         p.get("emission_tx_s", p.get("standby_tx_s", 0.0))),
                        fontsize=6.5, color="#2d8a3e",
                        textcoords="offset points", xytext=(3, 3))
    sizes = {"atsss_active_standby": 320, "always_warm": 110, "oracle": 300}
    for n in order:
        o = aggs[n]["overall"]
        x, y = o["outage_per_transition_s"], o["emission_tx_s"]
        ax.scatter(x, y, s=sizes.get(n, 160), marker=markers.get(n, "o"),
                   color=colors.get(n, "#888"), edgecolor="k", zorder=3,
                   alpha=0.9, label=f"{pretty[n]}: {x:.2f} s outage, "
                                    f"{y:.1f} s emission")
    ax.set_xlabel("Outage seconds per transition  (lower = better)")
    ax.set_ylabel("Emission transmit-seconds: airtime on the non-primary/"
                  "satcom leg (lower = better)")
    ax.set_title("The emissions trade-off: outage vs standby transmit, with the "
                 "oracle frontier\n(bottom-left is the target a predictive policy "
                 "must reach)")
    ax.set_xlim(left=0)
    ax.set_ylim(bottom=-1)
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=7.5, loc="upper right", framealpha=0.95, markerscale=0.55)
    _caption(fig, OUTAGE_NOTE + "\n" + DATA_CAPTION, size=6.5)
    fig.tight_layout(rect=[0, 0.08, 1, 1])
    fig.savefig(path, dpi=130)
    plt.close(fig)
