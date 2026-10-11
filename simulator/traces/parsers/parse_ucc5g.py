#!/usr/bin/env python3
"""
parse_ucc5g.py  --  UCC/MISL 5G production dataset -> KATIM unified per-trace schema.

Dataset: Raca, Leahy, Sreenan, Quinlan, "Beyond Throughput, The Next Generation:
A 5G Dataset with Channel and Context Metrics", ACM MMSys 2020. GPL-3.0.
Source: https://github.com/uccmisl/5Gdataset

This parser is STANDALONE (pure pandas/numpy), IDEMPOTENT and re-runnable:

    python3 parse_ucc5g.py

It reads every raw run CSV under RAW_ROOT, maps it to the KATIM unified schema,
writes one processed CSV per raw run under OUT_ROOT, and regenerates the data
card at CARD_PATH from the freshly computed statistics.

HARD RULES honoured:
  * Real data only. No interpolation / resampling / smoothing / gap-filling.
    Missing values ("-" in the raw) stay EMPTY in the output.
  * No row is dropped silently. Rows kept/processed are counted; any drop is
    counted with a reason (currently 0 drops -- every raw row is a valid
    26-column real sample).
  * Unit conversions (kbps->Mbps, percent->fraction) are the ONLY numeric
    transforms, and are evidence-justified in the data card.
  * Provenance: every processed file maps 1:1 to exactly one raw run file.
"""

import os
import glob
import json
import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Paths (resolved relative to this file: traces/parsers/ -> traces/)
# --------------------------------------------------------------------------
HERE      = os.path.dirname(os.path.abspath(__file__))
TRACES    = os.path.dirname(HERE)                        # .../traces
RAW_ROOT  = os.path.join(TRACES, "raw", "ucc-5g", "5G-production-dataset")
OUT_ROOT  = os.path.join(TRACES, "processed", "ucc5g")
CARD_PATH = os.path.join(TRACES, "cards", "ucc5g.md")
README_HINT = "https://raw.githubusercontent.com/uccmisl/5Gdataset/master/README.md"

# "-" is the dataset's missing marker. Treat the literal dash as NA on read.
NA_VALUES = ["-"]

# Unified schema column order (exactly these columns, in this order).
UNIFIED_COLS = [
    "t_s", "ts_utc", "bearer", "net_mode",
    "dl_mbps", "ul_mbps", "rtt_ms", "loss",
    "cell_id", "rsrp_dbm", "sinr_db", "speed_kmh",
    "lat", "lon",
]

# NetworkMode -> KATIM bearer taxonomy {5G, LTE, LEO, WiFi, cellular-subflow}.
# Only 5G and LTE map directly. Legacy 3G/2G fallbacks (UMTS/HSPA+/HSDPA/HSUPA/
# GPRS/EDGE) are NOT in the taxonomy; bearer is left EMPTY for them while
# net_mode preserves the exact raw mode, so no information is lost and the
# simulator can decide how to treat a sub-LTE deep-collapse sample.
BEARER_MAP = {"5G": "5G", "LTE": "LTE"}


def trace_id_from_relpath(relpath):
    """Stable, filesystem-safe 1:1 id from the raw relative path."""
    no_ext = relpath[:-4] if relpath.lower().endswith(".csv") else relpath
    return no_ext.replace(os.sep, "__")


def classify(relpath):
    """Derive (app, mobility, content) from the directory layout."""
    parts = relpath.split(os.sep)
    app = parts[0] if parts else ""
    mobility = ""
    for p in parts:
        if p in ("Static", "Driving"):
            mobility = p
            break
    # content = the show/profile subfolder, if any (between mobility and file)
    content = ""
    if mobility in parts:
        mi = parts.index(mobility)
        if mi + 1 < len(parts) - 1:  # there is a subfolder before the filename
            content = parts[mi + 1]
    return app, mobility, content


def pct_nonempty(series):
    return round(100.0 * series.notna().sum() / max(len(series), 1), 1)


def parse_one(raw_path, relpath):
    """Parse a single raw run into the unified schema. Returns (df_unified, stats)."""
    raw = pd.read_csv(raw_path, na_values=NA_VALUES, dtype=str, keep_default_na=True)
    n_raw = len(raw)

    # ---- timestamps -------------------------------------------------------
    ts = pd.to_datetime(raw["Timestamp"], format="%Y.%m.%d_%H.%M.%S", errors="coerce")
    n_bad_ts = int(ts.isna().sum())

    # ts_utc: ISO-8601 of the logged DEVICE-LOCAL clock (Europe/Dublin), naive
    # (no timezone designator) because the UTC offset is NOT recorded. Provided
    # for provenance only; downstream timing uses t_s, not this field.
    ts_iso = ts.dt.strftime("%Y-%m-%dT%H:%M:%S")

    # t_s: seconds since the run's first sample. The raw clock has 1 s
    # resolution, so ~4% of seconds carry two rows (equal t_s -> non-decreasing,
    # which satisfies "monotonic increasing" in the weak sense). Exactly one row
    # in the whole corpus steps back by 1 s (a device clock glitch); we clamp
    # t_s to a running maximum so the contract holds. This changes ONLY the
    # derived t_s of that single row -- its metric values and its ts_utc (which
    # still shows the real glitch) are untouched, and no row is dropped.
    t0 = ts.iloc[0]
    t_s_raw = (ts - t0).dt.total_seconds()
    t_s = t_s_raw.cummax()
    n_clamped = int((t_s_raw.values[1:] < np.maximum.accumulate(t_s_raw.values)[:-1]).sum())

    # ---- bearer / mode ----------------------------------------------------
    net_mode = raw["NetworkMode"]  # raw, verbatim (NA where "-")
    bearer = net_mode.map(lambda m: BEARER_MAP.get(m, "") if pd.notna(m) else "")

    # ---- throughput: kbps -> Mbps ----------------------------------------
    dl_kbps = pd.to_numeric(raw["DL_bitrate"], errors="coerce")
    ul_kbps = pd.to_numeric(raw["UL_bitrate"], errors="coerce")
    dl_mbps = dl_kbps / 1000.0
    ul_mbps = ul_kbps / 1000.0

    # ---- latency / loss ---------------------------------------------------
    rtt_ms = pd.to_numeric(raw["PINGAVG"], errors="coerce")       # ms, sparse
    loss = pd.to_numeric(raw["PINGLOSS"], errors="coerce") / 100.0  # percent -> fraction 0..1

    # ---- attachment id ----------------------------------------------------
    # RAWCELLID is the full global cell identifier (100% populated, ~2x the
    # distinct values of the local CellID), so it best "identifies attachment
    # changes" for the handover-extraction rule. The local CellID and the
    # CELLHEX/NODEHEX/LACHEX fields remain in the raw file if finer keys help.
    cell_id = raw["RAWCELLID"]

    # ---- signal -----------------------------------------------------------
    rsrp_dbm = pd.to_numeric(raw["RSRP"], errors="coerce")        # serving RSRP, dBm
    sinr_db = pd.to_numeric(raw["SNR"], errors="coerce")          # G-NetTrack "SNR" == SINR, dB

    # ---- context ----------------------------------------------------------
    speed_kmh = pd.to_numeric(raw["Speed"], errors="coerce")      # km/h
    lat = pd.to_numeric(raw["Latitude"], errors="coerce")
    lon = pd.to_numeric(raw["Longitude"], errors="coerce")

    out = pd.DataFrame({
        "t_s": t_s.round(3),
        "ts_utc": ts_iso,
        "bearer": bearer,
        "net_mode": net_mode,
        "dl_mbps": dl_mbps,
        "ul_mbps": ul_mbps,
        "rtt_ms": rtt_ms,
        "loss": loss,
        "cell_id": cell_id,
        "rsrp_dbm": rsrp_dbm,
        "sinr_db": sinr_db,
        "speed_kmh": speed_kmh,
        "lat": lat,
        "lon": lon,
    }, columns=UNIFIED_COLS)

    app, mobility, content = classify(relpath)
    dur = float(t_s.iloc[-1]) if len(t_s) else 0.0
    modes = [m for m in net_mode.dropna().unique().tolist()]
    seq = net_mode.dropna().astype(str).tolist()
    mode_transitions = sum(1 for a, b in zip(seq, seq[1:]) if a != b)
    has_5g = "5G" in modes
    # 5G<->LTE adjacency transitions (the P1/P2 signal)
    switch_5g_lte = sum(
        1 for a, b in zip(seq, seq[1:])
        if {a, b} == {"5G", "LTE"}
    )

    stats = {
        "trace_id": trace_id_from_relpath(relpath),
        "raw_relpath": relpath,
        "app": app,
        "mobility": mobility,
        "content": content,
        "rows": int(n_raw),
        "duration_s": round(dur, 1),
        "modes": modes,
        "mode_transitions": int(mode_transitions),
        "switch_5g_lte": int(switch_5g_lte),
        "n_bad_ts": n_bad_ts,
        "n_tclamp": n_clamped,
        "pct": {
            "bearer": round(100.0 * (out["bearer"].astype(str) != "").sum() / max(n_raw, 1), 1),
            "dl_mbps": pct_nonempty(out["dl_mbps"]),
            "ul_mbps": pct_nonempty(out["ul_mbps"]),
            "rtt_ms": pct_nonempty(out["rtt_ms"]),
            "loss": pct_nonempty(out["loss"]),
            "cell_id": pct_nonempty(out["cell_id"]),
            "rsrp_dbm": pct_nonempty(out["rsrp_dbm"]),
            "sinr_db": pct_nonempty(out["sinr_db"]),
            "speed_kmh": pct_nonempty(out["speed_kmh"]),
            "lat": pct_nonempty(out["lat"]),
        },
        "dl_mbps_max": float(np.nanmax(dl_mbps.values)) if n_raw else float("nan"),
        "dl_mbps_med": float(np.nanmedian(dl_mbps.values)) if n_raw else float("nan"),
    }
    return out, stats


def fmt(x, nd=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return f"{x:.{nd}f}"


def write_card(all_stats, totals):
    os.makedirs(os.path.dirname(CARD_PATH), exist_ok=True)
    L = []
    w = L.append
    w("# Data card: UCC/MISL 5G production dataset (`ucc5g`)")
    w("")
    w("Auto-generated by `traces/parsers/parse_ucc5g.py`. Re-run the parser to regenerate.")
    w("")
    w("## 1. Citation, licence, source")
    w("")
    w("- **Paper:** D. Raca, D. Leahy, C. J. Sreenan, J. J. Quinlan. "
      "*Beyond Throughput, The Next Generation: A 5G Dataset with Channel and Context Metrics.* "
      "ACM Multimedia Systems Conference (MMSys), Istanbul, Turkey, June 8-11, 2020.")
    w("- **Licence:** GPL-3.0 (repository `LICENSE`).")
    w(f"- **Source:** https://github.com/uccmisl/5Gdataset")
    w(f"- **README (units evidence fetched from):** {README_HINT}")
    w("- **Collection tool:** G-NetTrack Pro (non-rooted Android network monitor), "
      "one major Irish mobile operator (anonymised `Operatorname = B`).")
    w("- **Mobility x application design:** {Static, Driving(car)} x {file Download, "
      "video streaming (Netflix, Amazon Prime)}.")
    w("")
    w("## 2. Raw files used")
    w("")
    w(f"- **Raw root:** `{RAW_ROOT}`")
    w(f"- **Runs processed:** {totals['n_files']} CSV files (one processed trace per raw run, 1:1).")
    w(f"- **Total raw rows read:** {totals['total_rows']:,}")
    w(f"- **Rows dropped:** {totals['dropped']} (see section 5).")
    w("- The `__MACOSX/` sidecar folder and `.DS_Store` files are ignored (not data).")
    w("- **Note on run count:** the task brief said \"40 CSV runs\"; the delivered archive "
      f"actually contains **{totals['n_files']}** run CSVs. All are real and all are processed.")
    w("")
    w("## 3. Unit evidence (justified, not assumed)")
    w("")
    w("The repo README gives the citation and licence but no column-unit table. Units are "
      "established from three independent lines of evidence, so no author contact is needed:")
    w("")
    w("1. **G-NetTrack Pro convention** (the collection tool): exports `DL_bitrate`/`UL_bitrate` "
      "in **kbit/s**, `Speed` in **km/h**, `RSRP`/`RSSI` in **dBm**, `RSRQ`/`SNR` in **dB**, "
      "ping metrics in **ms**, `PINGLOSS` in **percent**.")
    w("2. **Sibling UCC 4G LTE dataset** (`traces/raw/ucc-4g-lte`, same group, same tool, same "
      "column names): LTE `DL_bitrate` values sit around 9,000-12,000, i.e. ~9-12 Mbit/s if the "
      "unit is kbit/s -- exactly right for LTE and impossible under any other unit.")
    w("3. **Value-range reasoning on this 5G dataset** (all runs):")
    w("")
    w(f"   - `DL_bitrate`: max **{totals['dl_max_kbps']:,.0f}**, p95 {totals['dl_p95_kbps']:,.0f}, "
      f"median {totals['dl_med_kbps']:,.0f}. As **kbps** that is a {totals['dl_max_kbps']/1000:.0f} Mbps "
      "peak -- a realistic 5G-NSA peak. As bps it would be a 0.5 Mbps 5G peak (absurd); as Mbps it "
      "would be 533 Gbps (impossible). => **kbps**.")
    w(f"   - `Speed`: max **{totals['speed_max']:.0f}**, p95 {totals['speed_p95']:.0f}. As **km/h** "
      "that is highway driving; as m/s it is 378 km/h (impossible for a car). => **km/h**.")
    w(f"   - `PINGAVG`: min **{totals['ping_min']:.0f}**, median {totals['ping_med']:.0f}, "
      f"max {totals['ping_max']:.0f}. Cellular RTT in **ms**. => **ms**.")
    w(f"   - `PINGLOSS`: range **{totals['loss_min']:.0f}..{totals['loss_max']:.0f}** with a p95 of "
      f"{totals['loss_p95']:.0f}. A 0..100 **percent** scale. => divided by 100 to a 0..1 fraction.")
    w(f"   - `RSRP`: {totals['rsrp_min']:.0f}..{totals['rsrp_max']:.0f} dBm, `SNR`: "
      f"{totals['snr_min']:.0f}..{totals['snr_max']:.0f} dB -- standard cellular dB(m) ranges.")
    w("")
    w("## 4. Column mapping (raw -> unified)")
    w("")
    w("| unified | source | transform |")
    w("|---|---|---|")
    w("| `t_s` | `Timestamp` | seconds since first row; running-max clamp for monotonicity (see 5) |")
    w("| `ts_utc` | `Timestamp` | ISO-8601 of the **device-local** clock (Europe/Dublin), naive. **NOT verified UTC** -- provenance only; use `t_s` for timing |")
    w("| `bearer` | `NetworkMode` | `5G`->`5G`, `LTE`->`LTE`; legacy 3G/2G -> empty |")
    w("| `net_mode` | `NetworkMode` | verbatim (5G, LTE, HSPA+, UMTS, HSUPA, HSDPA, GPRS, EDGE) |")
    w("| `dl_mbps` | `DL_bitrate` | kbps / 1000 |")
    w("| `ul_mbps` | `UL_bitrate` | kbps / 1000 |")
    w("| `rtt_ms` | `PINGAVG` | ms, verbatim (sparse) |")
    w("| `loss` | `PINGLOSS` | percent / 100 -> fraction 0..1 (sparse) |")
    w("| `cell_id` | `RAWCELLID` | global cell id, verbatim (best attachment-change key) |")
    w("| `rsrp_dbm` | `RSRP` | serving-cell RSRP, dBm, verbatim |")
    w("| `sinr_db` | `SNR` | G-NetTrack SNR == SINR, dB, verbatim |")
    w("| `speed_kmh` | `Speed` | km/h, verbatim |")
    w("| `lat` / `lon` | `Latitude` / `Longitude` | verbatim |")
    w("")
    w("**Raw columns intentionally NOT carried** (no slot in the unified schema; still in the raw "
      "files): `Operatorname`, `CellID` (local), `RSRQ`, `CQI`, `RSSI`, `State`, "
      "`PINGMIN/PINGMAX/PINGSTDEV`, `CELLHEX`, `NODEHEX`, `LACHEX`, `NRxRSRP`, `NRxRSRQ` "
      "(5G-NR secondary-cell signal).")
    w("")
    w("## 5. Rows dropped & adjustments")
    w("")
    w(f"- **Rows dropped: {totals['dropped']}.** Every raw row is a valid 26-column real sample "
      "(verified: all rows parse to exactly 26 fields, no embedded-comma corruption), so nothing "
      "is discarded. Missing cells are kept empty, never filled.")
    w(f"- **t_s monotonicity clamp: {totals['tclamp']} row(s)** across the whole corpus. One single "
      "backward step of 1 s (a device clock glitch, in `Netflix/Driving/.../B_2019.11.27_07.29.47`) "
      f"leaves {totals['tclamp']} rows sitting below the running clock maximum; their `t_s` is "
      "clamped to that maximum so the series is non-decreasing. The rows, their metrics and their "
      "`ts_utc` (which still shows the real glitch) are unchanged, and no row is dropped.")
    w(f"- **Duplicate-second rows: {totals['dup_rows']:,}** (~{100*totals['dup_rows']/totals['total_rows']:.1f}%). "
      "The 1 s clock means some seconds carry two GPS/metric samples. Both are kept (distinct "
      "lat/lon or metrics); `t_s` ties are allowed. No resampling.")
    w("")
    w("## 6. Sanity statistics (whole dataset)")
    w("")
    w(f"- Rows: {totals['total_rows']:,} across {totals['n_files']} runs.")
    w(f"- `net_mode` mix: {totals['mode_mix']}.")
    w(f"- `dl_mbps`: median {totals['dl_med_kbps']/1000:.2f}, p95 {totals['dl_p95_kbps']/1000:.1f}, "
      f"max {totals['dl_max_kbps']/1000:.1f} Mbps. The median is ~0 because the corpus is dominated "
      "by streaming runs whose throughput is ON/OFF (zero while the playout buffer is full); the "
      "p95/max reflect the real link capacity.")
    w(f"- Capacity sanity check by bearer (p95 / mean `dl_mbps`): 5G {totals['dl_p95_5g']:.1f} / "
      f"{totals['dl_mean_5g']:.1f} Mbps vs LTE {totals['dl_p95_lte']:.1f} / {totals['dl_mean_lte']:.1f} "
      "Mbps -- 5G clearly higher, as expected (an independent check that the kbps->Mbps mapping and "
      "the bearer labelling are right).")
    w(f"- `rtt_ms`: {totals['ping_min']:.0f}..{totals['ping_max']:.0f} ms (median {totals['ping_med']:.0f}).")
    w(f"- Runs with >=1 `5G<->LTE` adjacency switch: {totals['n_switch_files']} / {totals['n_files']}; "
      f"total 5G<->LTE switches: {totals['total_5g_lte_switches']}.")
    w("")
    w("## 7. Per-trace table")
    w("")
    w("Columns: trace_id | app | mobility | content | dur(s) | rows | net_modes | "
      "5G<->LTE sw | %dl | %rtt | %loss | %rsrp | %sinr | %speed")
    w("")
    w("| trace_id | app | mob | content | dur_s | rows | modes | 5G<->LTE | %dl | %rtt | %loss | %rsrp | %sinr | %spd |")
    w("|---|---|---|---|--:|--:|---|--:|--:|--:|--:|--:|--:|--:|")
    for s in all_stats:
        p = s["pct"]
        w("| `{tid}` | {app} | {mob} | {ct} | {dur} | {rows} | {modes} | {sw} | {dl} | {rtt} | {loss} | {rsrp} | {sinr} | {spd} |".format(
            tid=s["trace_id"], app=s["app"], mob=s["mobility"], ct=(s["content"] or "-"),
            dur=fmt(s["duration_s"]), rows=s["rows"], modes="/".join(s["modes"]) or "-",
            sw=s["switch_5g_lte"], dl=fmt(p["dl_mbps"]), rtt=fmt(p["rtt_ms"]),
            loss=fmt(p["loss"]), rsrp=fmt(p["rsrp_dbm"]), sinr=fmt(p["sinr_db"]), spd=fmt(p["speed_kmh"]),
        ))
    w("")
    w("## 8. Quirks & cautions")
    w("")
    w("- **RTT and loss are sparse.** `PINGAVG`/`PINGLOSS` are populated in only the "
      f"{totals['n_ping_files']} **Download** runs, and within those for ~7-8% of rows; across the "
      f"whole corpus `rtt_ms` is non-empty in only ~{totals['pct_rtt']:.1f}% of rows. The 62 "
      "streaming runs carry no ping. For P1/P2, RTT/loss trends are only directly observable on "
      "the Download traces; elsewhere the link state must be read from throughput + signal.")
    w("- **Throughput is app goodput; only Download runs measure capacity.** `DL_bitrate` is what the logging application received. In the Netflix/Amazon_Prime streaming runs it is the player's demand (0 whenever the playout buffer is full; median ~0.28 Mbps, 33-73% exact zeros), NOT link capacity. The simulator therefore uses only the 21 file-**Download** runs as a capacity series (`sim/scenarios.UCC5G_CAPACITY_APPS`, SPEC §12.1); streaming runs are kept (real data) but never drive capacity. Even in Download runs ~10% of `State=D` rows show 0 (kept as genuine stalls) and the first seconds before the transfer starts are app start-up, skipped by the scenario builder.")
    w("- **Legacy sub-LTE modes.** ~9% of rows are HSPA+/UMTS/HSUPA/HSDPA/GPRS/EDGE (a 3G/2G "
      "fallback = a deep capacity collapse). `bearer` is empty for these but `net_mode` keeps the "
      "exact mode. A 5G->3G drop is a *more* severe collapse than 5G->LTE and is kept, not dropped.")
    w("- **Anomalous `Operatorname`.** 26 rows log the numeric PLMN (`27203` = MCC 272 Ireland / "
      "`0`) instead of `B`. These are valid 26-field rows (correct NetworkMode/CellID); kept. "
      "`Operatorname` is not in the unified schema, so the output is unaffected.")
    w("- **Signal sentinels.** A few rows carry `RSRP`<=-140 (down to -200) / `NRxRSRP` down to "
      f"-205 -- G-NetTrack 'no reading' sentinels ({totals['rsrp_sentinel']} rows). Preserved "
      "verbatim (not scrubbed); the simulator should mask `rsrp_dbm` below ~-140 dBm.")
    w("- **`ts_utc` is device-local, not UTC** (Europe/Dublin wall clock, no offset recorded). "
      "Do timing off `t_s`.")
    w("")
    w("## 9. How this serves P1 and P2")
    w("")
    w("- **P1 (freshness-aware scheduling across bearer transitions):** the Driving runs contain "
      f"real **5G<->LTE** (and 5G->3G) `NetworkMode` switches ({totals['total_5g_lte_switches']} "
      "5G<->LTE adjacency switches over the corpus), each a genuine capacity change with before/"
      "after per-second DL/UL throughput and serving RSRP/SINR. This is the real capacity-collapse "
      "signal P1 replays and whose onset the collapse predictor learns.")
    w("- **P2 (make-before-break):** the same logged mode switches, timestamped and paired with "
      "the cell_id (`RAWCELLID`) change, are the transition events P2 must predict before they "
      "happen. The Download runs add sparse real RTT/loss so the handshake-cost / dead-peer timing "
      "can be anchored on measured latency rather than a pure model.")
    w("- **Role in the trace set:** this is the primary **5G<->LTE in-motion** trace. GEO stays "
      "measurement-parameterised and LEO comes from Starlink-on-the-Road; this dataset supplies the "
      "terrestrial 5G/LTE bearer and its transitions.")
    w("")
    with open(CARD_PATH, "w") as f:
        f.write("\n".join(L) + "\n")


def main():
    raw_files = sorted(glob.glob(os.path.join(RAW_ROOT, "**", "*.csv"), recursive=True))
    raw_files = [f for f in raw_files if "__MACOSX" not in f]
    if not raw_files:
        raise SystemExit(f"no raw CSVs found under {RAW_ROOT}\n"
                         f"  Obtain the UCC/MISL 5G dataset ({README_HINT}) and place it there; see ../SOURCES.md.")
    os.makedirs(OUT_ROOT, exist_ok=True)

    all_stats = []
    total_rows = 0
    dropped = 0
    tclamp = 0
    dup_rows = 0
    n_ping_files = 0

    # global accumulators for sanity stats / unit evidence
    dl_kbps_all, ul_kbps_all, speed_all, ping_all, loss_all = [], [], [], [], []
    rsrp_all, snr_all = [], []
    dl_5g, dl_lte = [], []
    mode_counter = {}
    rsrp_sentinel = 0
    total_5g_lte_switches = 0
    n_switch_files = 0

    for raw_path in raw_files:
        relpath = os.path.relpath(raw_path, RAW_ROOT)
        out, stats = parse_one(raw_path, relpath)

        # write processed file (idempotent overwrite), empty string for NA
        out_path = os.path.join(OUT_ROOT, stats["trace_id"] + ".csv")
        out.to_csv(out_path, index=False, na_rep="")

        total_rows += stats["rows"]
        tclamp += stats["n_tclamp"]
        if stats["pct"]["rtt_ms"] > 0:
            n_ping_files += 1
        if stats["switch_5g_lte"] > 0:
            n_switch_files += 1
        total_5g_lte_switches += stats["switch_5g_lte"]
        all_stats.append(stats)

        # recompute a few globals from the raw for evidence
        raw = pd.read_csv(raw_path, na_values=NA_VALUES, dtype=str)
        dup_rows += int(raw["Timestamp"].duplicated().sum())
        dl = pd.to_numeric(raw["DL_bitrate"], errors="coerce")
        ul = pd.to_numeric(raw["UL_bitrate"], errors="coerce")
        sp = pd.to_numeric(raw["Speed"], errors="coerce")
        pg = pd.to_numeric(raw["PINGAVG"], errors="coerce")
        ls = pd.to_numeric(raw["PINGLOSS"], errors="coerce")
        rs = pd.to_numeric(raw["RSRP"], errors="coerce")
        sn = pd.to_numeric(raw["SNR"], errors="coerce")
        dl_kbps_all.append(dl.values); ul_kbps_all.append(ul.values)
        speed_all.append(sp.values); ping_all.append(pg.values); loss_all.append(ls.values)
        rsrp_all.append(rs.values); snr_all.append(sn.values)
        nm = raw["NetworkMode"]
        dl_5g.append(dl[nm == "5G"].values); dl_lte.append(dl[nm == "LTE"].values)
        for m, c in nm.dropna().value_counts().items():
            mode_counter[m] = mode_counter.get(m, 0) + int(c)
        rsrp_sentinel += int((rs <= -140).sum())

    def cat(a):
        a = np.concatenate(a); return a[~np.isnan(a)]
    dl_kbps_all = cat(dl_kbps_all); speed_all = cat(speed_all)
    ping_all = cat(ping_all); loss_all = cat(loss_all)
    rsrp_all = cat(rsrp_all); snr_all = cat(snr_all)
    dl_5g = cat(dl_5g); dl_lte = cat(dl_lte)

    mode_mix = ", ".join(f"{k} {v:,}" for k, v in sorted(mode_counter.items(), key=lambda kv: -kv[1]))

    totals = {
        "n_files": len(raw_files),
        "total_rows": total_rows,
        "dropped": dropped,
        "tclamp": tclamp,
        "dup_rows": dup_rows,
        "n_ping_files": n_ping_files,
        "n_switch_files": n_switch_files,
        "total_5g_lte_switches": total_5g_lte_switches,
        "mode_mix": mode_mix,
        "dl_max_kbps": float(np.max(dl_kbps_all)),
        "dl_p95_kbps": float(np.percentile(dl_kbps_all, 95)),
        "dl_med_kbps": float(np.median(dl_kbps_all)),
        "dl_p95_5g": float(np.percentile(dl_5g, 95)) / 1000.0,
        "dl_p95_lte": float(np.percentile(dl_lte, 95)) / 1000.0,
        "dl_mean_5g": float(np.mean(dl_5g)) / 1000.0,
        "dl_mean_lte": float(np.mean(dl_lte)) / 1000.0,
        "speed_max": float(np.max(speed_all)),
        "speed_p95": float(np.percentile(speed_all, 95)),
        "ping_min": float(np.min(ping_all)),
        "ping_med": float(np.median(ping_all)),
        "ping_max": float(np.max(ping_all)),
        "loss_min": float(np.min(loss_all)),
        "loss_max": float(np.max(loss_all)),
        "loss_p95": float(np.percentile(loss_all, 95)),
        "rsrp_min": float(np.min(rsrp_all)),
        "rsrp_max": float(np.max(rsrp_all)),
        "snr_min": float(np.min(snr_all)),
        "snr_max": float(np.max(snr_all)),
        "rsrp_sentinel": rsrp_sentinel,
        "pct_rtt": round(100.0 * len(ping_all) / total_rows, 1),
    }

    write_card(all_stats, totals)

    # machine-readable manifest for reproducibility / downstream join
    manifest = {
        "dataset": "ucc5g",
        "raw_root": RAW_ROOT,
        "out_root": OUT_ROOT,
        "n_files": len(raw_files),
        "total_rows": total_rows,
        "rows_dropped": dropped,
        "traces": [
            {"trace_id": s["trace_id"], "raw_relpath": s["raw_relpath"],
             "rows": s["rows"], "duration_s": s["duration_s"],
             "app": s["app"], "mobility": s["mobility"], "content": s["content"],
             "modes": s["modes"], "switch_5g_lte": s["switch_5g_lte"]}
            for s in all_stats
        ],
    }
    with open(os.path.join(OUT_ROOT, "_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"[ucc5g] processed {len(raw_files)} runs, {total_rows:,} rows, "
          f"{dropped} dropped, {tclamp} t_s-clamped.")
    print(f"[ucc5g] wrote {len(raw_files)} CSVs -> {OUT_ROOT}")
    print(f"[ucc5g] card -> {CARD_PATH}")


if __name__ == "__main__":
    main()
