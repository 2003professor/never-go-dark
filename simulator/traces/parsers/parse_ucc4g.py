#!/usr/bin/env python3
"""
parse_ucc4g.py  --  UCC/MISL 4G LTE dataset -> KATIM unified per-trace schema.

Dataset: Raca, Quinlan, Zahran, Sreenan, "Beyond Throughput: a 4G LTE Dataset
with Channel and Context Metrics", ACM MMSys 2018. CC BY 4.0.
Zenodo 10.5281/zenodo.1219679.  Logger: G-NetTrack Pro (Android).

This script is STANDALONE (pure pandas/numpy), idempotent, and re-runnable:

    python3 parse_ucc4g.py

It reads every raw CSV under RAW_ROOT (folders car/bus/static/pedestrian/train,
each file = one measurement run), maps it to the unified schema, and writes one
processed CSV per run to OUT_ROOT. It also writes a machine-readable QA summary
(ucc4g_summary.json) next to the processed files, which the data card is built
from.

HARD RULES honoured:
  * Real data only. No interpolation / resampling / smoothing / gap-filling.
    Missing values are left EMPTY in the output.
  * No row is dropped silently. The QA summary counts every row read, written,
    and (if any) skipped, with the reason.
  * Each processed file maps to exactly ONE raw file (provenance preserved in
    the trace_id and in the summary's "raw_relpath").
  * Documented sentinels are represented as MISSING (empty), not as fake numbers
    (this is the opposite of filling: it refuses to pass a sentinel off as a
    real measurement). Every sentinel substitution is COUNTED.
"""

import os
import sys
import json
import glob
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
TRACES = os.path.dirname(HERE)  # .../traces
RAW_ROOT = os.path.join(TRACES, "raw", "ucc-4g-lte", "Dataset")
OUT_ROOT = os.path.join(TRACES, "processed", "ucc4g")

MOBILITIES = ["car", "bus", "static", "pedestrian", "train"]

# Raw column order, verified identical across all 135 files (G-NetTrack Pro export).
RAW_COLS = [
    "Timestamp", "Longitude", "Latitude", "Speed", "Operatorname", "CellID",
    "NetworkMode", "RSRP", "RSRQ", "SNR", "CQI", "RSSI", "DL_bitrate",
    "UL_bitrate", "State", "NRxRSRP", "NRxRSRQ", "ServingCell_Lon",
    "ServingCell_Lat", "ServingCell_Distance",
]

# Unified schema (exact column order for every processed CSV).
UNIFIED_COLS = [
    "t_s", "ts_utc", "bearer", "net_mode", "dl_mbps", "ul_mbps", "rtt_ms",
    "loss", "cell_id", "rsrp_dbm", "sinr_db", "speed_kmh", "lat", "lon",
]

# Metrics we report "% non-empty" for, in the per-trace table.
METRIC_COLS = ["dl_mbps", "ul_mbps", "cell_id", "rsrp_dbm", "sinr_db",
               "speed_kmh", "lat", "lon"]

# G-NetTrack missing marker.
NA_TOKENS = ["-", ""]

# Documented sentinels (represented as missing in the unified output).
RSRP_SENTINEL = -200.0       # LTE RSRP physical floor ~ -140 dBm; -200 = "no signal".
CELLID_SENTINEL = {"0", "0.0"}  # G-NetTrack logs 0 when no serving cell id is known.

TS_FORMAT = "%Y.%m.%d_%H.%M.%S"


def to_num(series):
    """Coerce a string Series to float, turning NA tokens and blanks into NaN."""
    s = series.astype(str).str.strip()
    s = s.where(~s.isin(NA_TOKENS), other=np.nan)
    return pd.to_numeric(s, errors="coerce")


def fmt_num(x):
    """Render a float for CSV: empty for NaN, int-looking for whole numbers."""
    if pd.isna(x):
        return ""
    xf = float(x)
    if xf == int(xf) and abs(xf) < 1e15:
        return str(int(xf))
    return repr(round(xf, 6))


def parse_one(path, mobility):
    """Parse one raw run -> (unified DataFrame, per-trace stats dict)."""
    base = os.path.splitext(os.path.basename(path))[0]      # e.g. A_2017.12.07_08.08.44
    trace_id = f"{mobility}_{base}"
    raw_relpath = os.path.relpath(path, TRACES)

    # Read everything as string so we fully control coercion and sentinels.
    raw = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    rows_read = len(raw)

    # Header sanity: the dataset is uniform, but verify rather than assume.
    header_ok = list(raw.columns) == RAW_COLS
    if not header_ok:
        # Fall back to positional mapping if the header text differs but width matches.
        if raw.shape[1] == len(RAW_COLS):
            raw.columns = RAW_COLS
        else:
            raise ValueError(
                f"{trace_id}: unexpected column count {raw.shape[1]} "
                f"(expected {len(RAW_COLS)})"
            )

    # --- Timestamp -> t_s (relative seconds) and ts_utc (ISO-8601) ---
    ts = pd.to_datetime(raw["Timestamp"].str.strip(), format=TS_FORMAT, errors="coerce")
    bad_ts = int(ts.isna().sum())
    # Keep raw file order (= measurement order); do NOT sort (would scramble the
    # sub-second ties G-NetTrack emits and break provenance order).
    t0 = ts.dropna().iloc[0] if ts.notna().any() else pd.NaT
    t_s = (ts - t0).dt.total_seconds()
    # Count non-monotonic (backwards) steps for QA; order is still preserved.
    diffs = t_s.diff()
    backwards = int((diffs < 0).sum())
    # All runs fall Nov 2017 - Feb 2018, entirely within Irish winter (Europe/Dublin
    # observes GMT = UTC+0, no DST). G-NetTrack logs device-local time, so local == UTC
    # here. ts_utc is the raw wall clock relabelled UTC under that (stated) assumption.
    ts_utc = ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ").where(ts.notna(), other="")

    # --- throughput: G-NetTrack logs kbit/s; unified schema is Mbit/s ---
    dl_kbps = to_num(raw["DL_bitrate"])
    ul_kbps = to_num(raw["UL_bitrate"])
    dl_mbps = dl_kbps / 1000.0
    ul_mbps = ul_kbps / 1000.0

    # --- signal ---
    rsrp = to_num(raw["RSRP"])
    n_rsrp_sentinel = int((rsrp == RSRP_SENTINEL).sum())
    rsrp = rsrp.where(rsrp != RSRP_SENTINEL, other=np.nan)  # sentinel -> missing
    sinr = to_num(raw["SNR"])  # device-reported SNR == SINR equivalent (see card)

    # --- cell id (string identifying attachment); 0 = "no cell known" -> empty ---
    cell_raw = raw["CellID"].astype(str).str.strip()
    n_cellid_sentinel = int(cell_raw.isin(CELLID_SENTINEL).sum())
    cell_id = cell_raw.where(~cell_raw.isin(CELLID_SENTINEL | {"", "-"}), other="")

    # --- context ---
    speed = to_num(raw["Speed"])
    lat = to_num(raw["Latitude"])
    lon = to_num(raw["Longitude"])
    net_mode = raw["NetworkMode"].astype(str).str.strip()

    out = pd.DataFrame({
        "t_s": t_s,
        "ts_utc": ts_utc,
        "bearer": "LTE",          # dataset's cellular bearer family (enum value)
        "net_mode": net_mode,     # true access tech: LTE / HSPA+ / UMTS / EDGE / ...
        "dl_mbps": dl_mbps,
        "ul_mbps": ul_mbps,
        "rtt_ms": np.nan,         # not measured by this dataset (no ping columns)
        "loss": np.nan,           # not measured by this dataset
        "cell_id": cell_id,
        "rsrp_dbm": rsrp,
        "sinr_db": sinr,
        "speed_kmh": speed,
        "lat": lat,
        "lon": lon,
    }, columns=UNIFIED_COLS)

    # No rows are dropped: every raw row produces exactly one output row.
    rows_written = len(out)

    # Per-trace stats for the data card.
    def pct_nonempty(col):
        s = out[col]
        if s.dtype == object:
            nonempty = (s.astype(str).str.len() > 0).sum()
        else:
            nonempty = s.notna().sum()
        return round(100.0 * nonempty / rows_written, 1) if rows_written else 0.0

    operators = sorted(set(raw["Operatorname"].astype(str).str.strip()))
    mode_counts = net_mode.value_counts().to_dict()
    n_idle = int((raw["State"].astype(str).str.strip() == "I").sum())
    # attachment / mode change events (useful for P1 collapse + handover labelling).
    # Over the sequence of KNOWN cell ids (blanks dropped), count how many times
    # the serving cell id changed value -> number of attachment changes.
    known = cell_id[cell_id.astype(str).str.len() > 0]
    cell_changes = int((known != known.shift()).sum() - 1) if len(known) else 0
    mode_changes = int((net_mode != net_mode.shift()).sum() - 1)

    stats = {
        "trace_id": trace_id,
        "mobility": mobility,
        "raw_relpath": raw_relpath,
        "operators": operators,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "rows_skipped": 0,
        "duration_s": round(float(t_s.dropna().iloc[-1]), 1) if t_s.notna().any() else 0.0,
        "unparseable_timestamps": bad_ts,
        "backwards_time_steps": backwards,
        "rsrp_sentinel_blanked": n_rsrp_sentinel,
        "cellid_sentinel_blanked": n_cellid_sentinel,
        "idle_rows": n_idle,
        "net_mode_changes": max(mode_changes, 0),
        "cell_changes": max(cell_changes, 0),
        "net_modes": mode_counts,
        "pct_nonempty": {c: pct_nonempty(c) for c in METRIC_COLS},
        "sanity": {
            "dl_mbps_min": _safe(out["dl_mbps"].min()),
            "dl_mbps_med": _safe(out["dl_mbps"].median()),
            "dl_mbps_max": _safe(out["dl_mbps"].max()),
            "ul_mbps_max": _safe(out["ul_mbps"].max()),
            "rsrp_min": _safe(out["rsrp_dbm"].min()),
            "rsrp_max": _safe(out["rsrp_dbm"].max()),
            "sinr_min": _safe(out["sinr_db"].min()),
            "sinr_max": _safe(out["sinr_db"].max()),
            "speed_max": _safe(out["speed_kmh"].max()),
        },
    }
    return out, stats


def _safe(x):
    return None if pd.isna(x) else round(float(x), 3)


def write_csv(out, path):
    """Write unified CSV with blanks for NaN (no interpolation, no fabricated values)."""
    df = out.copy()
    for c in ["dl_mbps", "ul_mbps", "rtt_ms", "loss", "rsrp_dbm", "sinr_db",
              "speed_kmh", "lat", "lon", "t_s"]:
        df[c] = df[c].map(fmt_num)
    # ts_utc, bearer, net_mode, cell_id are already strings.
    df.to_csv(path, index=False)


def main():
    if not os.path.isdir(RAW_ROOT):
        sys.exit(f"raw dataset not found: {RAW_ROOT}")
    os.makedirs(OUT_ROOT, exist_ok=True)

    # Idempotent: clear previously generated outputs so a re-run is clean.
    for old in glob.glob(os.path.join(OUT_ROOT, "*.csv")):
        os.remove(old)

    all_stats = []
    totals = {"files": 0, "rows_read": 0, "rows_written": 0, "rows_skipped": 0,
              "rsrp_sentinel_blanked": 0, "cellid_sentinel_blanked": 0,
              "idle_rows": 0, "unparseable_timestamps": 0, "backwards_time_steps": 0}

    for mob in MOBILITIES:
        for path in sorted(glob.glob(os.path.join(RAW_ROOT, mob, "*.csv"))):
            out, stats = parse_one(path, mob)
            write_csv(out, os.path.join(OUT_ROOT, stats["trace_id"] + ".csv"))
            all_stats.append(stats)
            totals["files"] += 1
            for k in ["rows_read", "rows_written", "rows_skipped",
                      "rsrp_sentinel_blanked", "cellid_sentinel_blanked",
                      "idle_rows", "unparseable_timestamps", "backwards_time_steps"]:
                totals[k] += stats[k]

    summary = {
        "dataset": "ucc-4g-lte",
        "raw_root": os.path.relpath(RAW_ROOT, TRACES),
        "out_root": os.path.relpath(OUT_ROOT, TRACES),
        "unified_cols": UNIFIED_COLS,
        "totals": totals,
        "by_mobility": {},
        "traces": all_stats,
    }
    for mob in MOBILITIES:
        ts = [s for s in all_stats if s["mobility"] == mob]
        summary["by_mobility"][mob] = {
            "traces": len(ts),
            "rows": sum(s["rows_written"] for s in ts),
            "duration_s": round(sum(s["duration_s"] for s in ts), 1),
        }

    with open(os.path.join(OUT_ROOT, "ucc4g_summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    print(f"processed {totals['files']} files")
    print(f"rows_read={totals['rows_read']} rows_written={totals['rows_written']} "
          f"rows_skipped={totals['rows_skipped']}")
    print(f"rsrp_sentinel_blanked={totals['rsrp_sentinel_blanked']} "
          f"cellid_sentinel_blanked={totals['cellid_sentinel_blanked']} "
          f"idle_rows={totals['idle_rows']}")
    print(f"unparseable_timestamps={totals['unparseable_timestamps']} "
          f"backwards_time_steps={totals['backwards_time_steps']}")
    print(f"summary -> {os.path.join(OUT_ROOT, 'ucc4g_summary.json')}")


if __name__ == "__main__":
    main()
