#!/usr/bin/env python3
"""
parse_starlink_road.py  -- Starlink-on-the-Road -> KATIM unified per-trace schema.

Dataset: "Starlink on the Road: A First Look at Mobile Starlink Performance in
Central Europe", Laniewski, Lanfer, Beginn, Dunker, Dueckers, Aschenbruck,
Proc. 8th TMA, 2024 (arXiv:2403.13497). Data: github.com/sys-uos/Starlink-on-the-Road
Licence: CC BY-SA 4.0.  Bearer: LEO (Starlink).

Pure pandas/numpy. Idempotent: re-running reproduces the same outputs
(the dataset output folder is cleared of generated CSVs first).

Run:
    python3 parse_starlink_road.py

Outputs (relative to the traces/ root inferred from this file):
    processed/starlink_road/sotr_<NN>.csv   one file per driving session (run)
    processed/starlink_road/ping_windows.csv  all ping windows on the session clock

HARD RULES honoured: real data only; NO interpolation / resampling / smoothing /
gap-filling; missing values are left empty; no row is dropped silently; every
processed file maps to exactly one raw file/run; units justified (see data card).
"""

import os
import shutil
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file so the script is location-independent)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_ROOT = os.path.abspath(os.path.join(HERE, ".."))          # .../traces
RAW_DIR = os.path.join(
    TRACES_ROOT, "raw", "starlink-on-the-road",
    "Starlink-on-the-Road-main", "data",
)
OUT_DIR = os.path.join(TRACES_ROOT, "processed", "starlink_road")

IPERF_CSV = os.path.join(RAW_DIR, "iperf.csv")
PING_CSV = os.path.join(RAW_DIR, "ping.csv")

# ---------------------------------------------------------------------------
# Tunables (documented in the data card)
# ---------------------------------------------------------------------------
# Run = a contiguous driving session. iperf was logged in ~15 s bursts on a
# ~55 s measurement duty cycle (iperf burst, then a ~25 s ping window, repeat),
# so intra-session gaps run up to ~135 s. Genuine breaks between sessions are
# >=150 s. The gap distribution is empty in (135 s, 150 s], so any threshold in
# that window yields the identical split into 76 runs.
RUN_GAP_THRESHOLD_S = 150.0

# Mobility labelling from driving speed (km/h). README: urban 0-50, highway >100.
SPEED_STATIONARY_MAX = 2.0     # <2 km/h: effectively parked
SPEED_URBAN_MAX = 60.0         # <=60 km/h: urban/suburban
SPEED_HIGHWAY_MIN = 100.0      # >=100 km/h: reached highway speeds

# Unified schema column order (exact).
UNIFIED_COLS = [
    "t_s", "ts_utc", "bearer", "net_mode", "dl_mbps", "ul_mbps",
    "rtt_ms", "loss", "cell_id", "rsrp_dbm", "sinr_db",
    "speed_kmh", "lat", "lon",
]

BEARER = "LEO"


def _iso_naive(ts: pd.Timestamp) -> str:
    """ISO 8601 WITHOUT a timezone designator.

    Source timestamps are naive wall-clock (Osnabruck, Germany; CET=UTC+1 in
    winter). They are NOT UTC and we do not convert them -- the 'ts_utc' column
    carries them for provenance/ordering only; all timing uses t_s. The absence
    of a trailing 'Z'/offset signals the value is not asserted to be UTC.
    """
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


def load_raw():
    ip = pd.read_csv(IPERF_CSV)
    pg = pd.read_csv(PING_CSV)
    ip["ts"] = pd.to_datetime(ip["timestamp_start"])
    ip["te"] = pd.to_datetime(ip["timestamp_end"])
    pg["ps"] = pd.to_datetime(pg["timestamp_start"])
    pg["pe"] = pd.to_datetime(pg["timestamp_end"])
    ip = ip.sort_values("ts", kind="mergesort").reset_index(drop=True)
    pg = pg.sort_values("ps", kind="mergesort").reset_index(drop=True)
    return ip, pg


def assign_runs(ip: pd.DataFrame) -> pd.Series:
    gap = ip["ts"].diff().dt.total_seconds()
    run = (gap > RUN_GAP_THRESHOLD_S).cumsum().astype(int)
    return run


def ping_window_for(ts_values, pg):
    """For each iperf timestamp, index of the ping window that *contains* it
    (ps <= ts <= pe), else -1. iperf and ping were scheduled alternately, so
    in practice this matches ~0 rows -- that is the point of a separate
    ping_windows.csv. No value is filled outside an actual containing window."""
    ps = pg["ps"].values.astype("datetime64[ns]")
    pe = pg["pe"].values.astype("datetime64[ns]")
    tsv = np.asarray(ts_values).astype("datetime64[ns]")
    k = np.searchsorted(ps, tsv, side="right") - 1
    out = np.full(len(tsv), -1, dtype=int)
    ok = k >= 0
    idx = np.where(ok)[0]
    containing = tsv[idx] <= pe[k[idx]]
    out[idx[containing]] = k[idx[containing]]
    return out


def build_unified_run(block: pd.DataFrame, pg: pd.DataFrame) -> pd.DataFrame:
    t0 = block["ts"].iloc[0]
    n = len(block)
    df = pd.DataFrame(index=range(n))
    df["t_s"] = (block["ts"].values - np.datetime64(t0)) / np.timedelta64(1, "s")
    df["ts_utc"] = [_iso_naive(t) for t in block["ts"]]
    df["bearer"] = BEARER
    df["net_mode"] = ""                      # Starlink logs no access-mode column
    df["dl_mbps"] = block["download_mbps"].values   # Mbit/s (raw bit/s / 1e6)
    df["ul_mbps"] = block["upload_mbps"].values     # Mbit/s
    # ping join: only where an iperf timestamp truly falls inside a ping window
    wk = ping_window_for(block["ts"].values, pg)
    rtt = np.full(n, np.nan)
    loss = np.full(n, np.nan)
    hit = wk >= 0
    if hit.any():
        rtt[hit] = pg["ping_avg"].values[wk[hit]]
        loss[hit] = pg["packet_loss"].values[wk[hit]] / 100.0  # percent -> fraction
    df["rtt_ms"] = rtt
    df["loss"] = loss
    df["cell_id"] = ""                       # no satellite/cell id exposed
    df["rsrp_dbm"] = ""                      # no signal metrics in this dataset
    df["sinr_db"] = ""
    df["speed_kmh"] = block["average_speed"].values  # driving speed, km/h
    df["lat"] = ""                           # withheld by authors (privacy)
    df["lon"] = ""
    return df[UNIFIED_COLS]


def mobility_label(speeds: pd.Series):
    s = speeds.dropna()
    if len(s) == 0:
        return "unknown", np.nan, np.nan
    smax = float(s.max())
    smean = float(s.mean())
    if smax < SPEED_STATIONARY_MAX:
        lab = "stationary"
    elif smax <= SPEED_URBAN_MAX:
        lab = "urban"
    elif smax >= SPEED_HIGHWAY_MIN:
        lab = "highway"
    else:
        lab = "mixed"
    return lab, smax, smean


def session_for_windows(pg, sessions):
    """Assign each ping window to the driving session whose [t0,t1] span contains
    the window start; return session trace_id and t_s-within-session (else '')."""
    sid = np.full(len(pg), -1, dtype=int)
    for i, (rid, t0, t1) in enumerate(sessions):
        m = (pg["ps"] >= t0) & (pg["ps"] <= t1)
        sid[m.values] = rid
    return sid


def main():
    if not os.path.isfile(IPERF_CSV):
        raise SystemExit(f"raw file not found: {IPERF_CSV}\n"
                         f"  Obtain the Starlink-on-the-Road dataset and place it there; see ../SOURCES.md.")
    ip, pg = load_raw()
    n_ip_raw, n_pg_raw = len(ip), len(pg)

    run = assign_runs(ip)
    ip = ip.assign(_run=run)

    # Fresh output dir (idempotent re-runs).
    os.makedirs(OUT_DIR, exist_ok=True)
    for f in os.listdir(OUT_DIR):
        if f.endswith(".csv"):
            os.remove(os.path.join(OUT_DIR, f))

    run_ids = sorted(ip["_run"].unique())
    trace_id_of = {rid: f"sotr_{i:02d}" for i, rid in enumerate(run_ids)}

    sessions = []           # (trace_index, t0, t1) for ping assignment
    rows_written = 0
    per_trace = []
    for i, rid in enumerate(run_ids):
        block = ip[ip["_run"] == rid].sort_values("ts").reset_index(drop=True)
        tid = trace_id_of[rid]
        uni = build_unified_run(block, pg)
        uni.to_csv(os.path.join(OUT_DIR, f"{tid}.csv"), index=False)
        rows_written += len(uni)
        sessions.append((i, block["ts"].iloc[0], block["ts"].iloc[-1]))

        lab, smax, smean = mobility_label(block["average_speed"])
        dur = float((block["ts"].iloc[-1] - block["ts"].iloc[0]).total_seconds())
        prio = sorted(block["priority"].unique().tolist())
        per_trace.append({
            "trace_id": tid,
            "start": _iso_naive(block["ts"].iloc[0]),
            "end": _iso_naive(block["ts"].iloc[-1]),
            "rows": len(uni),
            "dur_s": dur,
            "mobility": lab,
            "speed_max": smax,
            "speed_mean": smean,
            "priority": prio,
            "pct_dl": 100.0 * uni["dl_mbps"].notna().mean(),
            "pct_ul": 100.0 * uni["ul_mbps"].notna().mean(),
            "pct_rtt": 100.0 * uni["rtt_ms"].notna().mean(),
            "pct_loss": 100.0 * uni["loss"].notna().mean(),
            "pct_speed": 100.0 * uni["speed_kmh"].notna().mean(),
        })

    # ---- ping_windows.csv : the latency/loss stream on the session clock ----
    sid = session_for_windows(pg, sessions)
    tid_by_index = {i: f"sotr_{i:02d}" for i in range(len(sessions))}
    t0_by_index = {i: s[1] for i, s in enumerate(sessions)}
    pw = pd.DataFrame()
    pw["window_id"] = [f"pw_{i:04d}" for i in range(len(pg))]
    pw["session_trace_id"] = [tid_by_index[s] if s >= 0 else "" for s in sid]
    pw["t_s"] = [
        ((pg["ps"].iloc[j] - t0_by_index[sid[j]]).total_seconds()
         if sid[j] >= 0 else np.nan)
        for j in range(len(pg))
    ]
    pw["ts_utc"] = [_iso_naive(t) for t in pg["ps"]]
    pw["ts_end_utc"] = [_iso_naive(t) for t in pg["pe"]]
    pw["bearer"] = BEARER
    pw["rtt_ms"] = pg["ping_avg"].values                 # mean RTT, ms
    pw["rtt_best_ms"] = pg["ping_best"].values
    pw["rtt_worst_ms"] = pg["ping_worst"].values
    pw["rtt_stddev_ms"] = pg["ping_stddev"].values
    pw["loss"] = pg["packet_loss"].values / 100.0        # percent -> fraction
    pw["packets_sent"] = pg["packets_send"].values
    pw["window_s"] = (pg["pe"] - pg["ps"]).dt.total_seconds().values
    pw["speed_kmh"] = pg["average_speed"].values
    pw.to_csv(os.path.join(OUT_DIR, "ping_windows.csv"), index=False)

    # ---------------------------- report -----------------------------------
    pt = pd.DataFrame(per_trace)
    n_in_session = int((sid >= 0).sum())
    iperf_hit = sum(1 for d in per_trace if d["pct_rtt"] > 0)
    print("=" * 70)
    print("Starlink-on-the-Road -> unified schema")
    print("=" * 70)
    print(f"iperf raw rows      : {n_ip_raw}")
    print(f"iperf rows written  : {rows_written}  (dropped: {n_ip_raw - rows_written})")
    print(f"runs (sessions)     : {len(run_ids)}  (gap threshold {RUN_GAP_THRESHOLD_S:g}s)")
    print(f"ping raw windows    : {n_pg_raw}")
    print(f"ping windows written: {len(pw)}  (dropped: {n_pg_raw - len(pw)})")
    print(f"ping windows inside a session span : {n_in_session}/{len(pw)}")
    print(f"iperf rows matched to a ping window: "
          f"{int(pt['pct_rtt'].mul(pt['rows']).sum()/100)} "
          f"(runs with any match: {iperf_hit})")
    print("\nmobility mix:")
    print(pt["mobility"].value_counts().to_string())
    w = pt["rows"].sum()
    print("\nrow-weighted non-empty overall: dl_mbps %.1f%%  ul_mbps %.1f%%  "
          "rtt_ms %.1f%%  loss %.1f%%  speed_kmh %.1f%%"
          % (pt["pct_dl"].mul(pt["rows"]).sum() / w,
             pt["pct_ul"].mul(pt["rows"]).sum() / w,
             pt["pct_rtt"].mul(pt["rows"]).sum() / w,
             pt["pct_loss"].mul(pt["rows"]).sum() / w,
             pt["pct_speed"].mul(pt["rows"]).sum() / w))
    # full per-trace table (printed, not written, so processed/ holds only traces)
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 200)
    show = pt[["trace_id", "start", "end", "rows", "dur_s", "mobility",
               "speed_max", "speed_mean", "priority",
               "pct_dl", "pct_ul", "pct_rtt", "pct_loss", "pct_speed"]].copy()
    for c in ["dur_s", "speed_max", "speed_mean", "pct_dl", "pct_ul",
              "pct_rtt", "pct_loss", "pct_speed"]:
        show[c] = show[c].round(1)
    print("\nPER-TRACE TABLE:")
    print(show.to_string(index=False))
    print(f"\nwrote {len(run_ids)} run files + ping_windows.csv to:\n  {OUT_DIR}")


if __name__ == "__main__":
    main()
