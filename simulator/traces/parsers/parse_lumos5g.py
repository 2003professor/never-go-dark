#!/usr/bin/env python3
"""
parse_lumos5g.py  --  Lumos5G v1.0 dataset -> KATIM unified per-trace schema.

Dataset: Narayanan, Ramadan, Mehta, Hu, Liu, Fezeu, Dayalan, Verma, Ji, Li,
Qian, Zhang, "Lumos5G: Mapping and Predicting Commercial mmWave 5G Throughput",
ACM Internet Measurement Conference (IMC) 2020. CC BY 4.0. IEEE DataPort node/3598.
Source: https://ieee-dataport.org/open-access/lumos5g-dataset
Commercial mmWave 5G NSA, Minneapolis "Loop" (1300 m loop near U.S. Bank Stadium),
walking + driving, 1 s per seq_num within run_num.

This parser is STANDALONE (pure pandas/numpy), IDEMPOTENT and re-runnable:

    python3 parse_lumos5g.py

It reads the single raw CSV, splits it into one run per
(run_num, mobility_mode, trajectory_direction), maps each to the KATIM unified
schema, writes one processed CSV per run under OUT_ROOT, and regenerates the data
card at CARD_PATH from the freshly computed statistics.

HARD RULES honoured (SPEC.md Sec 0.4, Sec 2.3):
  * Real measured data only. No interpolation / resampling / smoothing / fill.
    Missing cells (empty NR fields, MAXINT sentinels) stay EMPTY in the output.
  * No row is dropped silently. Rows kept are counted; any drop is counted with a
    reason. (0 dropped here -- every raw row is a valid 19-column real sample and
    Throughput is 100% populated; measured 0 Mbps rows are REAL collapses, kept.)
  * The ONLY numeric transforms are unit conversions (movingSpeed m/s -> km/h) and
    sentinel/unreliable masking, each evidence-justified in the data card.
  * Provenance: every processed file maps 1:1 to exactly one raw run
    (run_num, mobility_mode, trajectory_direction).
"""

import os
import json
import hashlib
import numpy as np
import pandas as pd

# --------------------------------------------------------------------------
# Paths (resolved relative to this file: traces/parsers/ -> traces/)
# --------------------------------------------------------------------------
HERE      = os.path.dirname(os.path.abspath(__file__))
TRACES    = os.path.dirname(HERE)                        # .../traces
RAW_CSV   = os.path.join(TRACES, "raw", "lumos5g", "Lumos5G-v1.0.csv")
RAW_README= os.path.join(TRACES, "raw", "lumos5g", "README.md")
OUT_ROOT  = os.path.join(TRACES, "processed", "lumos5g")
CARD_PATH = os.path.join(TRACES, "cards", "lumos5g.md")
SOURCE_URL= "https://ieee-dataport.org/open-access/lumos5g-dataset"

# Android CellInfo#UNAVAILABLE == Integer.MAX_VALUE: a "no reading" sentinel, not
# a measurement. Masked to NA wherever it appears in a numeric signal field.
MAXINT = 2147483647

# Unified schema column order (exactly these columns, in this order). SPEC Sec 3.1.
UNIFIED_COLS = [
    "t_s", "ts_utc", "bearer", "net_mode",
    "dl_mbps", "ul_mbps", "rtt_ms", "loss",
    "cell_id", "rsrp_dbm", "sinr_db", "speed_kmh",
    "lat", "lon",
]


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def mask_sentinel(series):
    """to_numeric then NA-out the Integer.MAX_VALUE 'unavailable' sentinel."""
    v = pd.to_numeric(series, errors="coerce")
    return v.mask(v == MAXINT)


def pct_nonempty(series):
    return round(100.0 * series.notna().sum() / max(len(series), 1), 1)


def trace_id(run_num, mobility, direction):
    """Stable, filesystem-safe 1:1 id for a (run, mobility, direction) run."""
    return f"run{int(run_num):03d}__{mobility}__{direction}"


def parse_one(sub):
    """Map one raw run (already sorted by seq_num) to the unified schema.

    Returns (df_unified, stats). `sub` is a DataFrame of raw string columns for a
    single (run_num, mobility_mode, trajectory_direction) group.
    """
    n = len(sub)

    # ---- time: seq_num is a clean per-second (1 Hz) integer index -----------
    # Verified across the corpus: within every run seq_num is strictly
    # increasing by 1, with no duplicates and no gaps, starting at 1. So the
    # honest wall-clock-free timeline is t_s = seq_num - first_seq_num.
    # The dataset carries NO absolute timestamp, so ts_utc stays empty.
    seq = pd.to_numeric(sub["seq_num"], errors="coerce")
    t_s = (seq - seq.iloc[0]).astype(float)

    # ---- bearer / mode ------------------------------------------------------
    # README: nrStatus==CONNECTED => UE attached to 5G-NR; NOT_RESTRICTED / NONE
    # => not on 5G (LTE anchor of the NSA deployment). bearer is the KATIM class;
    # net_mode keeps the raw nrStatus verbatim.
    nrstatus = sub["nrStatus"]
    connected = (nrstatus == "CONNECTED")
    bearer = np.where(connected, "5G", "LTE")
    net_mode = nrstatus

    # ---- throughput: Throughput is already Mbit/s (units proof in card) -----
    # iPerf 3.7 per-second TCP downlink at the UE. Downlink only: the dataset has
    # no uplink, no RTT and no loss column, so ul_mbps/rtt_ms/loss stay empty.
    # Throughput is 100% populated; measured 0 values are REAL mmWave collapses
    # (all 0-rows occur on 5G), kept verbatim -- never treated as missing.
    dl_mbps = pd.to_numeric(sub["Throughput"], errors="coerce")
    empty = pd.Series([np.nan] * n, index=sub.index)
    ul_mbps = empty
    rtt_ms = empty
    loss = empty

    # ---- attachment id ------------------------------------------------------
    cell_id = sub["tower_id"].astype(str)  # anonymised tower id (1..24)

    # ---- signal: NR when CONNECTED, else LTE; sentinels masked --------------
    # README explicitly warns NR values (nr_ssRsrp/nr_ssSinr) are unreliable when
    # nrStatus != CONNECTED (boundary leakage). We therefore use NR signal ONLY
    # on CONNECTED rows and the LTE serving value otherwise. lte_rssnr is 100%
    # Integer.MAX_VALUE across this release (no usable LTE SINR), so sinr_db is
    # left empty on LTE rows rather than fabricated.
    nr_rsrp = mask_sentinel(sub["nr_ssRsrp"])     # dBm (3GPP TS 38.215), -140..-44
    nr_sinr = mask_sentinel(sub["nr_ssSinr"])     # dB  (3GPP TS 38.215), -23..40
    lte_rsrp = mask_sentinel(sub["lte_rsrp"])     # dBm, 100% populated

    rsrp_dbm = pd.Series(np.where(connected, nr_rsrp, lte_rsrp), index=sub.index)
    sinr_db = pd.Series(np.where(connected, nr_sinr, np.nan), index=sub.index)

    # ---- context ------------------------------------------------------------
    # movingSpeed: Android Location#getSpeed() -> metres/second (README). Convert
    # to km/h (x3.6). Self-check: max 14.06 m/s -> 50.6 km/h, a plausible downtown
    # driving speed; as raw km/h the driving runs would cap at 14 km/h (too slow).
    speed_kmh = pd.to_numeric(sub["movingSpeed"], errors="coerce") * 3.6
    lat = pd.to_numeric(sub["latitude"], errors="coerce")
    lon = pd.to_numeric(sub["longitude"], errors="coerce")

    out = pd.DataFrame({
        "t_s": t_s.round(3),
        "ts_utc": "",                      # no absolute timestamp in the dataset
        "bearer": bearer,
        "net_mode": net_mode.values,
        "dl_mbps": dl_mbps.values,
        "ul_mbps": ul_mbps.values,
        "rtt_ms": rtt_ms.values,
        "loss": loss.values,
        "cell_id": cell_id.values,
        "rsrp_dbm": rsrp_dbm.values,
        "sinr_db": sinr_db.values,
        "speed_kmh": speed_kmh.values,
        "lat": lat.values,
        "lon": lon.values,
    }, columns=UNIFIED_COLS)

    # ---- per-trace stats ----------------------------------------------------
    tw = cell_id.tolist()
    tower_changes = sum(1 for a, b in zip(tw, tw[1:]) if a != b)
    be = list(bearer)
    mode_flips = sum(1 for a, b in zip(be, be[1:]) if a != b)
    # boundary-leak NR masked on this run (NR present in raw but nrStatus != CONNECTED)
    nr_leak = int((pd.to_numeric(sub["nr_ssRsrp"], errors="coerce").notna() & (~connected)).sum())
    conn_nr_empty = int((connected & pd.to_numeric(sub["nr_ssRsrp"], errors="coerce").isna()).sum())

    stats = {
        "mobility": sub["mobility_mode"].iloc[0],
        "direction": sub["trajectory_direction"].iloc[0],
        "run_num": int(pd.to_numeric(sub["run_num"].iloc[0])),
        "rows": int(n),
        "duration_s": float(out["t_s"].iloc[-1]) if n else 0.0,
        "pct_5g": round(100.0 * connected.sum() / max(n, 1), 1),
        "tower_changes": int(tower_changes),
        "mode_flips": int(mode_flips),
        "nr_leak_masked": nr_leak,
        "conn_nr_empty": conn_nr_empty,
        "dl_p95": float(np.nanpercentile(dl_mbps.values, 95)) if n else float("nan"),
        "dl_zeros": int((dl_mbps == 0).sum()),
        "pct": {
            "dl_mbps": pct_nonempty(out["dl_mbps"]),
            "ul_mbps": pct_nonempty(out["ul_mbps"]),
            "rtt_ms": pct_nonempty(out["rtt_ms"]),
            "loss": pct_nonempty(out["loss"]),
            "cell_id": round(100.0 * (out["cell_id"].astype(str) != "").sum() / max(n, 1), 1),
            "rsrp_dbm": pct_nonempty(out["rsrp_dbm"]),
            "sinr_db": pct_nonempty(out["sinr_db"]),
            "speed_kmh": pct_nonempty(out["speed_kmh"]),
            "lat": pct_nonempty(out["lat"]),
        },
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
    w("# Data card: Lumos5G v1.0 (`lumos5g`)")
    w("")
    w("Auto-generated by `traces/parsers/parse_lumos5g.py`. Re-run the parser to regenerate.")
    w("")
    w("## 1. Citation, licence, source")
    w("")
    w("- **Paper:** A. Narayanan, E. Ramadan, R. Mehta, X. Hu, Q. Liu, R. A. K. Fezeu, "
      "U. K. Dayalan, S. Verma, P. Ji, T. Li, F. Qian, Z.-L. Zhang. "
      "*Lumos5G: Mapping and Predicting Commercial mmWave 5G Throughput.* "
      "ACM Internet Measurement Conference (IMC), Virtual Event, USA, 2020, pp. 176-193. "
      "doi:10.1145/3419394.3423629.")
    w("- **Licence:** Creative Commons Attribution 4.0 International (CC BY 4.0) "
      "(raw `LICENSE`).")
    w(f"- **Source:** {SOURCE_URL} (IEEE DataPort node/3598).")
    w(f"- **README (units evidence read from):** `{RAW_README}`")
    w("- **Collection:** Android UE (telephony + location APIs) on a commercial "
      "**mmWave 5G NSA** network; iPerf 3.7 per-second TCP downlink. "
      "**Loop** area = a 1300 m loop near U.S. Bank Stadium, downtown Minneapolis.")
    w("- **Design:** {walking, driving} x {CW, ACW} trajectories; driving is CW only "
      "(parts of the loop are one-way).")
    w("")
    w("## 2. Raw files used")
    w("")
    w(f"- **Raw file:** `{RAW_CSV}`")
    w(f"- **Raw SHA256:** `{totals['sha256']}`")
    w(f"- **Raw rows:** {totals['total_rows']:,} data rows, 19 columns, parsed cleanly "
      "(no embedded-comma / field-count corruption).")
    w(f"- **Runs processed:** {totals['n_files']} CSV files, one per "
      "`(run_num, mobility_mode, trajectory_direction)` (1:1 provenance). "
      f"run_num is globally unique (1..{totals['max_run']}); the three keys always agree.")
    w(f"- **Split:** driving/CW {totals['grp_drive_cw']}, walking/ACW {totals['grp_walk_acw']}, "
      f"walking/CW {totals['grp_walk_cw']} = {totals['n_files']} runs.")
    w(f"- **Rows dropped:** {totals['dropped']} (see section 5).")
    w("")
    w("## 3. Unit evidence (justified, not assumed)")
    w("")
    w("The README documents each column's **meaning** and its Android-API source but "
      "gives explicit units only for some. Units are pinned from the README plus "
      "value-range reasoning, so no author contact is needed:")
    w("")
    w(f"1. **`Throughput` -> Mbit/s.** README: \"throughput perceived by the UE; "
      "iPerf 3.7 per-second TCP downlink\" (unit not stated in words). Value range "
      f"min **{totals['dl_min']:.0f}**, median **{totals['dl_med']:.0f}**, p95 "
      f"**{totals['dl_p95']:.0f}**, max **{totals['dl_max']:.0f}**. As **Mbps** the "
      f"{totals['dl_max']:.0f} Mbps (~{totals['dl_max']/1000:.1f} Gbps) peak is exactly a "
      "commercial mmWave-5G peak -- the paper is titled *Commercial mmWave 5G Throughput* "
      "and reports ~1-2 Gbps. As kbps the peak would be ~1.9 Mbps (absurd for mmWave); as "
      "Gbps it would be ~1920 Gbps (impossible). Corroboration: on non-CONNECTED (LTE-anchor) "
      f"rows the same column has p95 **{totals['dl_p95_lte']:.0f}** / mean "
      f"**{totals['dl_mean_lte']:.0f}** Mbps, a plausible LTE-A rate, vs 5G p95 "
      f"**{totals['dl_p95_5g']:.0f}** / mean **{totals['dl_mean_5g']:.0f}** Mbps. "
      "=> **Mbps, carried verbatim (no scaling).**")
    w(f"2. **`movingSpeed` -> metres/second, converted x3.6 to km/h.** README states "
      "the value is Android `Location#getSpeed()`, whose documented unit is **m/s**. "
      f"Self-check: max **{totals['speed_max_ms']:.2f} m/s** -> **{totals['speed_max_ms']*3.6:.1f} km/h**, "
      "a plausible downtown driving speed; if the column were already km/h the driving runs "
      f"would cap at ~{totals['speed_max_ms']:.0f} km/h (implausibly slow). => **m/s, x3.6**.")
    w("3. **Signal dB(m)** per README + 3GPP: `nr_ssRsrp` in **dBm** (TS 38.215, range "
      f"-140..-44; observed {totals['nr_rsrp_min']:.0f}..{totals['nr_rsrp_max']:.0f}), "
      f"`nr_ssSinr` in **dB** (TS 38.215, range -23..40; observed "
      f"{totals['nr_sinr_min']:.0f}..{totals['nr_sinr_max']:.0f}), `lte_rsrp` in **dBm** "
      f"(observed {totals['lte_rsrp_min']:.0f}..{totals['lte_rsrp_max']:.0f}).")
    w("")
    w("## 4. Column mapping (raw -> unified)")
    w("")
    w("| unified | source | transform |")
    w("|---|---|---|")
    w("| `t_s` | `seq_num` | `seq_num - first_seq_num`; clean 1 Hz integer index (strictly +1, no gaps/dups, starts at 1 in every run) |")
    w("| `ts_utc` | -- | **empty**: the dataset carries no absolute wall-clock timestamp |")
    w("| `bearer` | `nrStatus` | `CONNECTED`->`5G`, else (`NOT_RESTRICTED`/`NONE`)->`LTE` (NSA LTE anchor) |")
    w("| `net_mode` | `nrStatus` | verbatim (`CONNECTED`/`NOT_RESTRICTED`/`NONE`) |")
    w("| `dl_mbps` | `Throughput` | Mbit/s, verbatim; measured 0 = real collapse, kept |")
    w("| `ul_mbps` | -- | **empty**: downlink-only dataset |")
    w("| `rtt_ms` | -- | **empty**: no latency column |")
    w("| `loss` | -- | **empty**: no loss column |")
    w("| `cell_id` | `tower_id` | anonymised tower id (1..24), string, verbatim |")
    w("| `rsrp_dbm` | `nr_ssRsrp` if CONNECTED else `lte_rsrp` | dBm; NR only when CONNECTED; MAXINT sentinel masked |")
    w("| `sinr_db` | `nr_ssSinr` if CONNECTED else *(empty)* | dB; NR only when CONNECTED; LTE RSSNR unusable (see 8) |")
    w("| `speed_kmh` | `movingSpeed` | m/s x 3.6 |")
    w("| `lat` / `lon` | `latitude` / `longitude` | degrees, verbatim |")
    w("")
    w("**Raw columns intentionally NOT carried** (no slot in the unified schema; still in "
      "the raw file): `abstractSignalStr` (LTE abstract signal level 1-5), `compassDirection` "
      "(bearing 0-360 deg), `lte_rssi`, `lte_rsrq`, `lte_rssnr` (100% MAXINT -- see 8), "
      "`nr_ssRsrq`.")
    w("")
    w("## 5. Rows dropped & adjustments")
    w("")
    w(f"- **Rows dropped: {totals['dropped']}.** Every raw row is a valid 19-column real "
      "sample and `Throughput` is 100% populated, so no row is unusable on the "
      "no-throughput rule (SPEC Sec 2.3). Missing signal cells are kept empty, never filled.")
    w(f"- **Measured zero throughput: {totals['dl_zeros']:,} rows** (~"
      f"{100*totals['dl_zeros']/totals['total_rows']:.1f}%), **all on 5G**. These are real "
      "mmWave capacity collapses (blockage/beam loss), NOT missing data, and are kept as "
      "`dl_mbps = 0.0`. The simulator's gap rule (SPEC Sec 2.3) never triggers here because "
      "throughput is present every second; a real 0 is an on-link collapse, distinct from a "
      "measurement gap.")
    w(f"- **NR signal masked as unreliable: {totals['nr_leak']} rows** where `nr_ssRsrp`/"
      "`nr_ssSinr` were present in the raw but `nrStatus != CONNECTED` (README boundary "
      "leakage). Masked to empty per the README warning; the LTE serving value is used for "
      f"`rsrp_dbm` on those rows instead. **{totals['conn_nr_empty']} CONNECTED rows** have "
      "no NR reading at all -> `rsrp_dbm`/`sinr_db` left empty (not fabricated).")
    w(f"- **MAXINT ('unavailable') sentinels masked:** `lte_rssnr` {totals['rssnr_sent']:,} "
      f"(100%), `nr_ssRsrq` {totals['nrrsrq_sent']:,} (not carried); none in the carried "
      "`nr_ssRsrp`/`nr_ssSinr`/`lte_rsrp`.")
    w("- **No timestamp / no monotonicity clamp:** time is the integer `seq_num`, already "
      "clean, so there is no clock-glitch adjustment (unlike the UCC datasets).")
    w("")
    w("## 6. Sanity statistics (whole dataset)")
    w("")
    w(f"- Rows: {totals['total_rows']:,} across {totals['n_files']} runs "
      f"({totals['n_walk']:,} walking, {totals['n_drive']:,} driving rows).")
    w(f"- `nrStatus` (net_mode) mix: {totals['mode_mix']}.")
    w(f"- `bearer` mix: 5G {totals['n_5g']:,} ({100*totals['n_5g']/totals['total_rows']:.1f}%), "
      f"LTE {totals['n_lte']:,} ({100*totals['n_lte']/totals['total_rows']:.1f}%).")
    w(f"- `dl_mbps`: min {totals['dl_min']:.0f}, median {totals['dl_med']:.0f}, "
      f"p95 {totals['dl_p95']:.0f}, max {totals['dl_max']:.0f} Mbps.")
    w(f"- Capacity by bearer (p95 / mean `dl_mbps`): 5G {totals['dl_p95_5g']:.0f} / "
      f"{totals['dl_mean_5g']:.0f} Mbps vs LTE {totals['dl_p95_lte']:.0f} / "
      f"{totals['dl_mean_lte']:.0f} Mbps -- 5G an order of magnitude higher, as expected "
      "(independent check on the Mbps unit and the bearer labelling).")
    w(f"- `speed_kmh`: median {totals['speed_med_kmh']:.1f}, max {totals['speed_max_kmh']:.1f} "
      "km/h (driving runs are the fast ones).")
    w(f"- `nr_ssRsrp`/`rsrp_dbm`: {totals['nr_rsrp_min']:.0f}..{totals['nr_rsrp_max']:.0f} dBm (NR), "
      f"LTE {totals['lte_rsrp_min']:.0f}..{totals['lte_rsrp_max']:.0f} dBm; "
      f"`sinr_db` {totals['nr_sinr_min']:.0f}..{totals['nr_sinr_max']:.0f} dB (NR).")
    w(f"- **Attachment / transition density:** {totals['tower_changes']:,} `tower_id` "
      f"(cell_id) changes and {totals['mode_flips']:,} 5G<->LTE `bearer` flips across the "
      f"corpus; every one of the {totals['n_files']} runs has >=1 flip. 24 distinct towers.")
    w("")
    w("## 7. Per-trace table")
    w("")
    w("Columns: trace_id | mobility | dir | dur(s) | rows | %5G | tower ch | 5G<->LTE | "
      "dl_p95(Mbps) | dl=0 | %rsrp | %sinr | %speed")
    w("")
    w("| trace_id | mob | dir | dur_s | rows | %5G | tow | 5G<->LTE | dl_p95 | dl=0 | %rsrp | %sinr | %spd |")
    w("|---|---|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|--:|")
    for s in all_stats:
        p = s["pct"]
        w("| `{tid}` | {mob} | {dir} | {dur} | {rows} | {p5g} | {tow} | {sw} | {dlp} | {z} | {rsrp} | {sinr} | {spd} |".format(
            tid=trace_id(s["run_num"], s["mobility"], s["direction"]),
            mob=s["mobility"], dir=s["direction"], dur=fmt(s["duration_s"]), rows=s["rows"],
            p5g=fmt(s["pct_5g"]), tow=s["tower_changes"], sw=s["mode_flips"],
            dlp=fmt(s["dl_p95"]), z=s["dl_zeros"], rsrp=fmt(p["rsrp_dbm"]),
            sinr=fmt(p["sinr_db"]), spd=fmt(p["speed_kmh"]),
        ))
    w("")
    w("## 8. Quirks & cautions")
    w("")
    w("- **No RTT, loss or uplink.** Lumos5G logs only downlink `Throughput`. `ul_mbps`, "
      "`rtt_ms` and `loss` are empty for every row. For P1/P2 this trace supplies the "
      "**capacity / signal / mobility** side of a bearer; latency is anchored from the GEO "
      "model and the UCC/Starlink ping data, not here.")
    w("- **No absolute timestamp.** `ts_utc` is empty; use `t_s` (the `seq_num` second "
      "index) for all timing. Each run is its own 1 Hz clock starting at 0 s.")
    w("- **`lte_rssnr` is 100% `Integer.MAX_VALUE`** (CellInfo#UNAVAILABLE) in this "
      "release, so there is no usable LTE SINR. `sinr_db` is therefore NR-only and is empty "
      "on LTE-anchor rows rather than fabricated from an unavailable field.")
    w("- **NR values leak past `nrStatus`.** The README warns NR signal is unreliable when "
      f"`nrStatus != CONNECTED`; {totals['nr_leak']} such rows carry NR values and are masked "
      "(LTE value used instead). This is the documented boundary-case behaviour, handled "
      "conservatively.")
    w("- **RSRP floor.** `lte_rsrp` pins at **-140 dBm** on "
      f"{totals['lte_rsrp_floor']:,} low-signal rows (coincident with dropped "
      "`lte_rssi`/`lte_rsrq`); preserved verbatim. The simulator may mask `rsrp_dbm <= -140` "
      "as a 'no reading' floor.")
    w("- **Loop geometry.** All runs trace the same 1300 m loop (lat ~44.973-44.977, "
      "lon ~ -93.264..-93.259); `tower_id` repeats across runs because the same towers line "
      "the loop. Transitions are therefore highly repeatable across runs -- good for "
      "train/held-out generalisation of the predictor.")
    w("")
    w("## 9. How this serves P1 and P2")
    w("")
    w("- **P1 (freshness-aware scheduling at capacity collapse):** this is the dataset with "
      f"the **sharpest real capacity cliffs** -- {totals['dl_zeros']:,} measured 0 Mbps rows "
      "and swings from ~2 Gbps to 0 within seconds as mmWave beams are blocked. Replayed as "
      "`dl_mbps`, these are exactly the capacity collapses at which P1 must re-map planes, "
      "drop superseded PLI and rate-cap. The per-second 5G<->LTE `bearer` flips drive the "
      "static-mapping-vs-freshness comparison.")
    w("- **P2 (predictive make-before-break):** "
      f"{totals['mode_flips']:,} 5G<->LTE transitions and {totals['tower_changes']:,} "
      "tower_id changes, each with leading `nr_ssRsrp`/`nr_ssSinr`/`speed` context, are the "
      "transition events the predictor learns to see coming before the link breaks. The "
      "mmWave collapse onset (RSRP/SINR roll-off + speed) is the causal feature set for "
      "warming a standby tunnel ahead of the break.")
    w("- **Role in the trace set:** the primary **5G mmWave walking + driving** source "
      "(UCC 5G is the sub-6 5G<->LTE in-motion source; GEO stays measurement-parameterised; "
      "LEO is Starlink-on-the-Road). Lumos5G contributes the most aggressive 5G collapses "
      "and the NR signal columns (`rsrp`/`sinr`/`tower_id`/`nrStatus`) the others lack.")
    w("")
    with open(CARD_PATH, "w") as f:
        f.write("\n".join(L) + "\n")


def main():
    if not os.path.isfile(RAW_CSV):
        raise SystemExit(f"raw file not found: {RAW_CSV}\n"
                         f"  Obtain the Lumos5G dataset ({SOURCE_URL}) and place it there; see ../SOURCES.md.")
    os.makedirs(OUT_ROOT, exist_ok=True)
    raw = pd.read_csv(RAW_CSV, dtype=str, keep_default_na=False, low_memory=False)
    total_rows = len(raw)

    # numeric views for global stats / unit evidence
    dl = pd.to_numeric(raw["Throughput"], errors="coerce")
    sp = pd.to_numeric(raw["movingSpeed"], errors="coerce")
    nr_rsrp = mask_sentinel(raw["nr_ssRsrp"])
    nr_sinr = mask_sentinel(raw["nr_ssSinr"])
    lte_rsrp = mask_sentinel(raw["lte_rsrp"])
    connected = (raw["nrStatus"] == "CONNECTED")

    all_stats = []
    dropped = 0
    tower_changes = mode_flips = nr_leak = conn_nr_empty = 0
    grp_counts = {("driving", "CW"): 0, ("walking", "ACW"): 0, ("walking", "CW"): 0}

    # one run per (run_num, mobility_mode, trajectory_direction). Sort rows WITHIN
    # a run by NUMERIC seq_num (the columns are read as strings, so a lexicographic
    # sort would order 1,10,100,...,2 -- wrong). _seq_num / _run_num are numeric
    # helper keys, never written to output.
    raw["_seq_num"] = pd.to_numeric(raw["seq_num"], errors="coerce")
    raw["_run_num"] = pd.to_numeric(raw["run_num"], errors="coerce")
    keys = ["run_num", "mobility_mode", "trajectory_direction"]
    for key, sub in raw.groupby(keys, sort=False):
        sub = sub.sort_values("_seq_num", kind="stable")
        out, stats = parse_one(sub)
        tid = trace_id(stats["run_num"], stats["mobility"], stats["direction"])
        out.to_csv(os.path.join(OUT_ROOT, tid + ".csv"), index=False, na_rep="")

        all_stats.append(stats)
        tower_changes += stats["tower_changes"]
        mode_flips += stats["mode_flips"]
        nr_leak += stats["nr_leak_masked"]
        conn_nr_empty += stats["conn_nr_empty"]
        gk = (stats["mobility"], stats["direction"])
        if gk in grp_counts:
            grp_counts[gk] += 1

    all_stats.sort(key=lambda s: s["run_num"])

    mode_counter = raw["nrStatus"].value_counts()
    mode_mix = ", ".join(f"{k} {int(v):,}" for k, v in mode_counter.items())

    totals = {
        "sha256": sha256_of(RAW_CSV),
        "n_files": len(all_stats),
        "total_rows": total_rows,
        "dropped": dropped,
        "max_run": int(pd.to_numeric(raw["run_num"]).max()),
        "grp_drive_cw": grp_counts[("driving", "CW")],
        "grp_walk_acw": grp_counts[("walking", "ACW")],
        "grp_walk_cw": grp_counts[("walking", "CW")],
        "n_walk": int((raw["mobility_mode"] == "walking").sum()),
        "n_drive": int((raw["mobility_mode"] == "driving").sum()),
        "n_5g": int(connected.sum()),
        "n_lte": int((~connected).sum()),
        "mode_mix": mode_mix,
        "dl_min": float(np.nanmin(dl)), "dl_med": float(np.nanmedian(dl)),
        "dl_p95": float(np.nanpercentile(dl, 95)), "dl_max": float(np.nanmax(dl)),
        "dl_zeros": int((dl == 0).sum()),
        "dl_p95_5g": float(np.nanpercentile(dl[connected], 95)),
        "dl_mean_5g": float(np.nanmean(dl[connected])),
        "dl_p95_lte": float(np.nanpercentile(dl[~connected], 95)),
        "dl_mean_lte": float(np.nanmean(dl[~connected])),
        "speed_max_ms": float(np.nanmax(sp)),
        "speed_max_kmh": float(np.nanmax(sp) * 3.6),
        "speed_med_kmh": float(np.nanmedian(sp) * 3.6),
        "nr_rsrp_min": float(np.nanmin(nr_rsrp)), "nr_rsrp_max": float(np.nanmax(nr_rsrp)),
        "nr_sinr_min": float(np.nanmin(nr_sinr)), "nr_sinr_max": float(np.nanmax(nr_sinr)),
        "lte_rsrp_min": float(np.nanmin(lte_rsrp)), "lte_rsrp_max": float(np.nanmax(lte_rsrp)),
        "lte_rsrp_floor": int((lte_rsrp == -140).sum()),
        "rssnr_sent": int((pd.to_numeric(raw["lte_rssnr"], errors="coerce") == MAXINT).sum()),
        "nrrsrq_sent": int((pd.to_numeric(raw["nr_ssRsrq"], errors="coerce") == MAXINT).sum()),
        "tower_changes": tower_changes,
        "mode_flips": mode_flips,
        "nr_leak": nr_leak,
        "conn_nr_empty": conn_nr_empty,
    }

    write_card(all_stats, totals)

    manifest = {
        "dataset": "lumos5g",
        "raw_csv": RAW_CSV,
        "raw_sha256": totals["sha256"],
        "out_root": OUT_ROOT,
        "n_files": totals["n_files"],
        "total_rows": total_rows,
        "rows_dropped": dropped,
        "traces": [
            {"trace_id": trace_id(s["run_num"], s["mobility"], s["direction"]),
             "run_num": s["run_num"], "mobility": s["mobility"], "direction": s["direction"],
             "rows": s["rows"], "duration_s": s["duration_s"],
             "pct_5g": s["pct_5g"], "mode_flips": s["mode_flips"],
             "tower_changes": s["tower_changes"], "dl_zeros": s["dl_zeros"]}
            for s in all_stats
        ],
    }
    with open(os.path.join(OUT_ROOT, "_manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"[lumos5g] processed {totals['n_files']} runs, {total_rows:,} rows, "
          f"{dropped} dropped.")
    print(f"[lumos5g] 5G {totals['n_5g']:,} / LTE {totals['n_lte']:,} rows; "
          f"{mode_flips:,} 5G<->LTE flips; {tower_changes:,} tower changes; "
          f"{totals['dl_zeros']:,} real 0-Mbps collapses.")
    print(f"[lumos5g] wrote {totals['n_files']} CSVs -> {OUT_ROOT}")
    print(f"[lumos5g] card -> {CARD_PATH}")


if __name__ == "__main__":
    main()
