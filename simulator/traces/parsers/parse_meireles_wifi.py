#!/usr/bin/env python3
"""
parse_meireles_wifi.py  --  Meireles et al. V2I Wi-Fi diversity dataset -> unified KATIM trace schema.

Dataset : "A Dataset for Exploring Wi-Fi Network Diversity in Vehicle-to-Infrastructure
           Communication", Rui Meireles, Antonio Rodrigues, Andrei Stanciu, Ana Aguiar,
           Peter Steenkiste. IEEE VNC 2020. Zenodo record 6884095. Licence: CC BY 4.0.
Source  : traces/raw/meireles-v2i-wifi.csv   (one raw file, ~46.5k rows, 1 Hz)
Output  : traces/processed/meireles_wifi/<trace_id>.csv   (unified schema, one file per run)

A "run" = one (traceNr, radio) pair. Each lap (traceNr) was measured simultaneously on
three Wi-Fi radios, one per 802.11 type, with a fixed 1:1 mapping:
    receiver m1    <-> ap-n   <-> 802.11n   (2.4 GHz, 20 MHz)
    receiver w4    <-> ap-ac  <-> 802.11ac  (5 GHz, 40 MHz)
    receiver tp-03 <-> ap-ad  <-> 802.11ad  (60 GHz, 2160 MHz)
so 8 laps x 3 radios = 24 runs. trace_id = "t<traceNr>_<wifiType>", e.g. t302_ad.

Pure pandas/numpy. Idempotent: re-running with `python3 parse_meireles_wifi.py`
reproduces the same files (the output dir's *.csv are cleared first).

HARD RULES honoured:
  * Real data only; no interpolation, resampling, smoothing or gap-filling.
  * Missing values are written empty (never 0, never forward-filled).
  * No row dropped silently: drops are counted with a reason and printed.
  * Units justified (see traces/cards/meireles_wifi.md): README + value-range evidence.
  * Provenance: every output row maps to exactly one raw row of the single raw file.
"""

import os
import sys
import glob
import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Paths (resolved relative to this file, so the script runs from anywhere)
# ---------------------------------------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
TRACES = os.path.dirname(HERE)                                   # .../traces
RAW = os.path.join(TRACES, "raw", "meireles-v2i-wifi.csv")
OUT_DIR = os.path.join(TRACES, "processed", "meireles_wifi")

# Sentinel used by the dataset for "no value" in signal columns.
SENTINEL = -100.0

# Fixed unified-schema column order (exactly these 14 columns, in this order).
SCHEMA = [
    "t_s", "ts_utc", "bearer", "net_mode",
    "dl_mbps", "ul_mbps", "rtt_ms", "loss",
    "cell_id", "rsrp_dbm", "sinr_db", "speed_kmh",
    "lat", "lon",
]


def main():
    if not os.path.isfile(RAW):
        sys.exit(f"ERROR: raw file not found: {RAW}")

    df = pd.read_csv(RAW)
    n_raw = len(df)

    # -- Row filter (documented, provenance). The raw file is already pre-filtered to
    #    isIperfOn==1 & isInLap==1, so this is expected to drop 0 rows, but we enforce
    #    and count it so the mapping is one raw measurement row -> one output row and
    #    throughput always has iperf meaning.
    drops = {}
    before = len(df)
    df = df[df["isIperfOn"] == 1]
    drops["isIperfOn != 1 (no UDP consumer running -> throughput meaningless)"] = before - len(df)
    before = len(df)
    df = df[df["isInLap"] == 1]
    drops["isInLap != 1 (outside the lap-around-AP measurement window)"] = before - len(df)
    n_kept = len(df)

    # -- Build the unified frame.
    out = pd.DataFrame(index=df.index)

    # Per-run relative time: seconds since the first sample of THIS run. systime is an
    # NTP-synchronised unix epoch at 1 Hz; within a run it is contiguous integer seconds
    # (verified: no duplicates, no gaps), so t_s = 0,1,2,... No resampling is performed.
    run_key = df["traceNr"].astype(str) + "_" + df["wifiType"].astype(str)
    out["t_s"] = (df["systime"] - df.groupby(run_key)["systime"].transform("min")).astype(float)

    # ts_utc: the systime epoch is a known wall clock (NTP), so we emit it as ISO-8601 UTC.
    # This is a deterministic re-encoding of a measured value, not interpolation.
    out["ts_utc"] = pd.to_datetime(df["systime"], unit="s", utc=True).dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    out["bearer"] = "WiFi"
    out["net_mode"] = df["wifiType"].astype(str)          # n / ac / ad, verbatim

    # Throughput: tghptConsumer is the UDP consumer (receiver-side, downlink AP->vehicle)
    # application goodput in Mbit/s. Value 0 is a real measurement (coverage cliff) and is
    # kept, not blanked. There is no uplink stream in this dataset -> ul empty.
    out["dl_mbps"] = df["tghptConsumer"].astype(float)
    out["ul_mbps"] = np.nan                                # not measured

    # RTT and packet loss are not measured in this throughput dataset. nRetries (WLAN
    # retransmission count) is NOT a 0..1 loss fraction, so we do not fabricate one.
    out["rtt_ms"] = np.nan                                 # not measured
    out["loss"] = np.nan                                   # not measured

    # Attachment id: the AP serving the client. An AP change here would mark a handover;
    # in this dataset each run stays on one AP (one radio), which is the provenance anchor.
    out["cell_id"] = df["senderId"].astype(str)            # ap-n / ap-ac / ap-ad

    # Signal. Two different radios log two different quantities:
    #   * n / ac  -> rssiMean (dBm);  snrMean is sentinel (-100) throughout.
    #   * ad      -> snrMean (dB) from 802.11ad sector-sweep frames; rssiMean is -100 throughout.
    # We map rssiMean -> rsrp_dbm (RSSI is the Wi-Fi equivalent of RSRP) and
    # snrMean -> sinr_db (SNR is the equivalent of SINR), blanking the -100 sentinel.
    rsrp = df["rssiMean"].astype(float)
    out["rsrp_dbm"] = rsrp.where(rsrp != SENTINEL, np.nan)
    sinr = df["snrMean"].astype(float)
    out["sinr_db"] = sinr.where(sinr != SENTINEL, np.nan)

    # Speed: receiverSpeed is m/s (README) -> km/h. Deterministic unit conversion.
    out["speed_kmh"] = df["receiverSpeed"].astype(float) * 3.6

    out["lat"] = df["lat"].astype(float)                   # degrees
    out["lon"] = df["lon"].astype(float)                   # degrees

    out = out[SCHEMA]

    # -- Idempotent write: clear this dataset's previous output, then write one file per run.
    os.makedirs(OUT_DIR, exist_ok=True)
    for old in glob.glob(os.path.join(OUT_DIR, "*.csv")):
        os.remove(old)

    out["__tn"] = df["traceNr"].values
    out["__wt"] = df["wifiType"].astype(str).values
    out["__ap"] = df["senderId"].astype(str).values

    summary = []
    for (tn, wt), g in out.groupby(["__tn", "__wt"], sort=True):
        trace_id = f"t{tn}_{wt}"
        ap = g["__ap"].iloc[0]
        g = g.drop(columns=["__tn", "__wt", "__ap"]).sort_values("t_s")
        path = os.path.join(OUT_DIR, f"{trace_id}.csv")
        # na_rep='' -> missing values are written as empty fields, never 0 or NaN text.
        g.to_csv(path, index=False, na_rep="")
        summary.append((trace_id, ap, wt, len(g)))

    # -- Report.
    print(f"raw file          : {RAW}")
    print(f"raw rows          : {n_raw}")
    for reason, k in drops.items():
        print(f"  dropped {k:>5}  : {reason}")
    print(f"rows kept         : {n_kept}")
    print(f"output dir        : {OUT_DIR}")
    print(f"runs written      : {len(summary)}")
    tot = 0
    for trace_id, ap, wt, k in summary:
        tot += k
        print(f"  {trace_id:<10} ap={ap:<6} 802.11{wt:<3} rows={k}")
    print(f"total output rows : {tot}")
    assert tot == n_kept, "row-count mismatch: output rows != kept rows"
    print("OK: output rows == kept rows (provenance preserved).")


if __name__ == "__main__":
    main()
