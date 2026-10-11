#!/usr/bin/env python3
"""
parse_berlin_v2x.py  --  Berlin V2X cellular -> KATIM unified per-trace schema.

Dataset: R. Hernangomez, P. Geuer, A. Palaios, D. Schaeufele, C. Watermann,
K. Taleb-Bendiab, et al., "Berlin V2X: A Machine Learning Dataset from Multiple
Vehicles and Radio Access Technologies", IEEE VTC2023-Spring, 2023
(arXiv:2212.10343). Data: IEEE DataPort, DOI 10.21227/8cj7-q373.
Licence: IEEE DataPort open access; licence not stated on the record page.
Bearer: LTE (two Berlin operators, MCC 262).

Role in the trace set (SPEC §6.3): this is the GROUND TRUTH for the
handover-event extraction rule. Serving-cell (PCI) changes with native
timestamps are the labelled handover events; we preserve them faithfully.

Pure pandas/numpy/pyarrow. Idempotent: re-running clears the generated CSVs in
the output folder and reproduces identical outputs.

Run:
    python3 parse_berlin_v2x.py

Outputs (relative to the traces/ root inferred from this file):
    processed/berlin_v2x/<trace_id>.csv     one file per run (device x round x scenario)

HARD RULES honoured: real measured data only; NO interpolation / resampling /
smoothing / gap-filling; missing values are left EMPTY; native 1 Hz timestamps
kept (no resampling); every processed file maps to exactly one contiguous run;
units justified from column names + value ranges (see data card). The policy
never sees this file; it is replayed by the engine with zero-order hold (SPEC
§2.2) and its horizons (SPEC §2.3).
"""

import os
import shutil
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file so the script is location-independent)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_ROOT = os.path.abspath(os.path.join(HERE, ".."))              # .../traces
RAW_FILE = os.path.join(TRACES_ROOT, "raw", "berlin-v2x", "cellular_dataframe.parquet")
OUT_DIR = os.path.join(TRACES_ROOT, "processed", "berlin_v2x")

# ---------------------------------------------------------------------------
# Unified schema column order (exact, SPEC §3.1). No other columns are written.
# ---------------------------------------------------------------------------
UNIFIED_COLS = [
    "t_s", "ts_utc", "bearer", "net_mode",
    "dl_mbps", "ul_mbps", "rtt_ms", "loss",
    "cell_id", "rsrp_dbm", "sinr_db", "speed_kmh", "lat", "lon",
]

# Raw columns we read (everything else in the 160-col frame is left in the raw
# file; see the data card for the columns intentionally NOT carried).
RAW_COLS = [
    "device", "operator", "measurement", "scenario", "direction", "measured_qos",
    "target_datarate", "ping_ms", "datarate", "ts_gps", "area",
    "PCell_Cell_ID", "PCell_Cell_Identity", "PCell_MNC", "PCell_MCC",
    "PCell_RSRP_max", "PCell_SNR_1", "speed_kmh", "Latitude", "Longitude",
]

# ---------------------------------------------------------------------------
# Scenario -> (direction, measurement-purpose). Established by cross-tabulation
# of scenario x direction x measured_qos x target_datarate on the whole frame:
#   A3D  downlink  datarate(saturation)  target 350 Mbps  -> dl capacity is REAL
#   A3U  uplink    datarate(saturation)  target  75 Mbps  -> ul capacity is REAL
#   A2D  downlink  delay (400 kbps CBR probe)              -> datarate is NOT capacity
#   A2U  uplink    delay (400 kbps CBR probe)              -> datarate is NOT capacity
# On the delay (A2*) runs the link is NOT saturated, so `datarate` is just the
# fixed low-rate probe and is NOT a measurement of link capacity. Recording it
# as dl/ul_mbps would fabricate a capacity collapse, so we leave dl/ul EMPTY on
# those runs and keep only the real RTT. This is the key honesty call for this
# dataset and is foregrounded in the data card.
# ---------------------------------------------------------------------------
SATURATION_DL = "A3D"    # datarate -> dl_mbps
SATURATION_UL = "A3U"    # datarate -> ul_mbps

# Unit guard (SPEC §3.2 style): reject a saturation run whose median throughput
# would imply > 10 Gbps (a units error). The 9001-R tops out at 1 Gbps; Berlin
# LTE peaks ~271 Mbps, so this never fires -- it is a tripwire, not a filter.
UNIT_GUARD_MAX_MBPS = 10_000.0

# Rounding (avoid fabricating precision; these are display/storage precisions).
R = dict(t_s=3, dl_mbps=6, ul_mbps=6, rtt_ms=3, rsrp_dbm=2, sinr_db=2,
         speed_kmh=3, lat=6, lon=6)


def _cell_id_str(series: pd.Series) -> pd.Series:
    """PCell_Cell_ID (float PCI, integer-valued) -> integer string, else ''."""
    out = series.copy()
    mask = out.notna()
    s = pd.Series("", index=series.index, dtype=object)
    s.loc[mask] = out.loc[mask].round().astype("int64").astype(str)
    return s


def _count_value_changes(series: pd.Series) -> int:
    """Number of changes in a (sparse) categorical series, ignoring NaN gaps."""
    v = series.dropna()
    if len(v) < 2:
        return 0
    v = v.round().astype("int64").to_numpy()
    return int((np.diff(v) != 0).sum())


def main():
    if not os.path.isfile(RAW_FILE):
        raise SystemExit(f"raw file not found: {RAW_FILE}")

    # ----- idempotency: clear previously generated CSVs in the output dir -----
    os.makedirs(OUT_DIR, exist_ok=True)
    for fn in os.listdir(OUT_DIR):
        if fn.endswith(".csv"):
            os.remove(os.path.join(OUT_DIR, fn))

    # ----- read (timestamp is the tz-aware Europe/Berlin index) ---------------
    df = pd.read_parquet(RAW_FILE, columns=RAW_COLS)
    df = df.reset_index()                       # -> 'timestamp' column
    assert "timestamp" in df.columns, "expected a 'timestamp' index in the parquet"
    n_raw = len(df)

    # timestamp is the authoritative 1 Hz clock (0% null, tz-aware). ts_gps has
    # ~1.2% NaT, so we drive timing off `timestamp`, not ts_gps.
    ts = pd.to_datetime(df["timestamp"], utc=False)
    df["_ts_utc"] = ts.dt.tz_convert("UTC")

    # ----- run key: one contiguous drive = (operator, device, round, scenario)
    # Verified: (device, measurement) maps 1:1 to one (operator, scenario), and
    # each group is contiguous and strictly 1 Hz within itself.
    df["_mnum"] = df["measurement"].astype("int64")
    df["trace_id"] = (
        "op" + df["operator"].astype("int64").astype(str)
        + "_" + df["device"].astype(str)
        + "_m" + df["_mnum"].map(lambda m: f"{m:02d}")
        + "_" + df["scenario"].astype(str)
    )

    per_trace = []
    rows_written = 0
    dropped_all_empty = 0
    runs_rejected_unitguard = 0
    gappy_runs = 0

    for trace_id, sub in df.groupby("trace_id", sort=True):
        sub = sub.sort_values("timestamp").reset_index(drop=True)
        scenario = sub["scenario"].iloc[0]
        operator = int(sub["operator"].iloc[0])
        device = sub["device"].iloc[0]

        # ----- unit guard on saturation runs --------------------------------
        if scenario in (SATURATION_DL, SATURATION_UL):
            med_mbps = (sub["datarate"].median() / 1e6)
            if np.isfinite(med_mbps) and med_mbps > UNIT_GUARD_MAX_MBPS:
                runs_rejected_unitguard += 1
                continue

        out = pd.DataFrame(index=sub.index)

        # t_s: seconds since this run's first sample (NOT filled across gaps).
        t0 = sub["timestamp"].iloc[0]
        out["t_s"] = (sub["timestamp"] - t0).dt.total_seconds().round(R["t_s"])

        # ts_utc: genuine UTC (the source clock is tz-aware Europe/Berlin).
        out["ts_utc"] = sub["_ts_utc"].dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")

        out["bearer"] = "LTE"
        out["net_mode"] = "LTE"      # single-RAT dataset; no mode switches

        # dl/ul throughput: ONLY from saturation runs (real capacity), by
        # direction. Delay (A2*) runs leave both empty (datarate = 400 kbps
        # probe, not capacity).
        dl = pd.Series(np.nan, index=sub.index, dtype="float64")
        ul = pd.Series(np.nan, index=sub.index, dtype="float64")
        if scenario == SATURATION_DL:
            dl = sub["datarate"] / 1e6
        elif scenario == SATURATION_UL:
            ul = sub["datarate"] / 1e6
        out["dl_mbps"] = dl.round(R["dl_mbps"])
        out["ul_mbps"] = ul.round(R["ul_mbps"])

        # rtt_ms: ping_ms verbatim (ms) wherever present (all run types). On
        # saturation runs this is LOADED (bufferbloat-inflated) RTT; on delay
        # runs it is unloaded RTT. Documented in the card; kept verbatim.
        out["rtt_ms"] = sub["ping_ms"].round(R["rtt_ms"])

        # loss: no packet-loss / PER column in this frame -> always empty.
        out["loss"] = np.nan

        # cell_id: PCell_Cell_ID = LTE Physical Cell ID (PCI, 0..503). The
        # handover-event key (SPEC §6.3). Integer string.
        out["cell_id"] = _cell_id_str(sub["PCell_Cell_ID"])

        # signal: RSRP_max (best-server RSRP, dBm), SNR_1 (dB, SINR proxy).
        out["rsrp_dbm"] = sub["PCell_RSRP_max"].round(R["rsrp_dbm"])
        out["sinr_db"] = sub["PCell_SNR_1"].round(R["sinr_db"])

        # pose: speed already km/h; lat/lon degrees.
        out["speed_kmh"] = sub["speed_kmh"].round(R["speed_kmh"])
        out["lat"] = sub["Latitude"].round(R["lat"])
        out["lon"] = sub["Longitude"].round(R["lon"])

        out = out[UNIFIED_COLS]

        # ----- drop rows that carry NO unified payload (timestamp only) ------
        payload = ["dl_mbps", "ul_mbps", "rtt_ms", "loss", "cell_id",
                   "rsrp_dbm", "sinr_db", "speed_kmh", "lat", "lon"]
        def _empty(colname, s):
            if colname == "cell_id":
                return s.astype(str).eq("") | s.isna()
            return s.isna()
        empty_mask = np.ones(len(out), dtype=bool)
        for c in payload:
            empty_mask &= _empty(c, out[c]).to_numpy()
        n_drop = int(empty_mask.sum())
        dropped_all_empty += n_drop
        out = out.loc[~empty_mask].reset_index(drop=True)

        if len(out) == 0:
            continue

        # ----- intra-run measurement gap (NOT filled; just reported) ---------
        dts = np.diff(out["t_s"].to_numpy())
        max_gap = float(dts.max()) if len(dts) else 0.0
        if max_gap > 1.5:
            gappy_runs += 1

        # ----- write ---------------------------------------------------------
        out.to_csv(os.path.join(OUT_DIR, f"{trace_id}.csv"),
                   index=False, na_rep="")
        rows_written += len(out)

        # ----- per-trace bookkeeping -----------------------------------------
        test = "datarate" if scenario in (SATURATION_DL, SATURATION_UL) else "delay"
        areas = [a for a in sub["area"].dropna().unique().tolist() if a not in ("UNKNOWN",)]
        pct = lambda s: round(100.0 * (s.astype(str).ne("").mean()
                                       if s.dtype == object else s.notna().mean()), 1)
        per_trace.append(dict(
            trace_id=trace_id, operator=operator, device=device,
            scenario=scenario, test=test,
            rows=len(out), dur_s=round(float(out["t_s"].iloc[-1]), 0),
            max_gap_s=round(max_gap, 0),
            cell_changes=_count_value_changes(sub["PCell_Cell_ID"]),
            n_cells=int(sub["PCell_Cell_ID"].dropna().round().nunique()),
            pct_dl=pct(out["dl_mbps"]), pct_ul=pct(out["ul_mbps"]),
            pct_rtt=pct(out["rtt_ms"]), pct_cell=pct(out["cell_id"]),
            pct_rsrp=pct(out["rsrp_dbm"]), pct_sinr=pct(out["sinr_db"]),
            pct_speed=pct(out["speed_kmh"]),
            areas="/".join(areas[:6]),
        ))

    pt = pd.DataFrame(per_trace).sort_values("trace_id").reset_index(drop=True)

    # ----------------------------- report ----------------------------------
    w = pt["rows"].sum()
    wavg = lambda col: (pt[col] * pt["rows"]).sum() / w
    print("=" * 74)
    print("Berlin V2X (cellular) -> KATIM unified schema")
    print("=" * 74)
    print(f"raw rows read           : {n_raw}")
    print(f"rows written            : {rows_written}")
    print(f"rows dropped (all-empty): {dropped_all_empty}")
    print(f"runs written            : {len(pt)}")
    print(f"runs rejected (unitguard): {runs_rejected_unitguard}")
    print(f"runs with a >1.5s gap   : {gappy_runs}")
    print(f"total cell_id (PCI) changes : {int(pt['cell_changes'].sum())}  "
          f"(runs with >=1 change: {int((pt['cell_changes']>0).sum())}/{len(pt)})")
    print(f"total net_mode changes      : 0  (single-RAT LTE)")
    print("\nby scenario (runs / rows):")
    print(pt.groupby(["scenario", "test"]).agg(
        runs=("trace_id", "size"), rows=("rows", "sum")).to_string())
    print("\nrow-weighted non-empty %%: "
          f"dl {wavg('pct_dl'):.1f}  ul {wavg('pct_ul'):.1f}  "
          f"rtt {wavg('pct_rtt'):.1f}  cell {wavg('pct_cell'):.1f}  "
          f"rsrp {wavg('pct_rsrp'):.1f}  sinr {wavg('pct_sinr'):.1f}  "
          f"speed {wavg('pct_speed'):.1f}  (loss 0.0 -- no column)")
    print("\nPER-TRACE TABLE:")
    pd.set_option("display.max_rows", None)
    pd.set_option("display.width", 220)
    print(pt.to_string(index=False))
    print(f"\nwrote {len(pt)} run files to:\n  {OUT_DIR}")


if __name__ == "__main__":
    main()
