#!/usr/bin/env python3
"""
parse_hsr_mptcp.py  --  HSR-MPTCP (Li et al., ACM SIGCOMM 2018) -> unified KATIM trace schema.

Dataset: github.com/superlitong/hsr-mptcp  (MIT). Per-subflow tshark CSV captures taken
AT THE CLIENT during bulk MPTCP downloads from a server on TCP port 8000, over two cellular
carriers on a high-speed rail. Each measurement run (one "experiment", keyed by the capture
epoch) has exactly two concurrent subflow files -- one per carrier/interface -- which are the
two simultaneous bearers the KATIM simulator will replay.

This script is standalone, pure pandas/numpy, and idempotent: re-running it overwrites the
processed CSVs from the raw files without changing anything else.

    python3 parse_hsr_mptcp.py              # process elephant flows (default)
    python3 parse_hsr_mptcp.py --mouse      # ALSO process mouse flows (short; see data card)

Raw packet CSV columns (tshark export):
    frame.time_epoch, ip.src, ip.dst, tcp.srcport, tcp.dstport, tcp.seq, tcp.ack,
    tcp.len, tcp.flags, tcp.analysis.bytes_in_flight, tcp.analysis.ack_rtt,
    tcp.options.mptcp.rawdataack, tcp.options.mptcp.rawdataseqno

Output: one CSV per subflow file in the unified schema (see UNIFIED_COLUMNS), 1 s time series.
"""

import argparse
import csv as _csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------- paths
HERE = os.path.dirname(os.path.abspath(__file__))
TRACES_DIR = os.path.dirname(HERE)                                   # .../traces
RAW_ROOT = os.path.join(TRACES_DIR, "raw", "hsr-mptcp", "hsr-mptcp-master", "data_traces")
OUT_DIR = os.path.join(TRACES_DIR, "processed", "hsr_mptcp")
CARD_DIR = os.path.join(TRACES_DIR, "cards")
MANIFEST = os.path.join(OUT_DIR, "_manifest.json")   # machine-readable stats for the data card

SERVER_PORT = 8000          # the download server listens here (verified across all files)
BIN_S = 1.0                 # 1-second aggregation window

UNIFIED_COLUMNS = [
    "t_s", "ts_utc", "bearer", "net_mode", "dl_mbps", "ul_mbps", "rtt_ms", "loss",
    "cell_id", "rsrp_dbm", "sinr_db", "speed_kmh", "lat", "lon",
]

# tshark TCP flag words we treat as a SYN (connection (re)start -> reset seq high-water mark)
SYN_FLAGS = {"0x00000002", "0x00000012", "0x0002", "0x0012", "2", "18"}


# ----------------------------------------------------------------------------- filename parsing
def parse_filename(fname):
    """client.<epoch>.<cc>.laptop#N.<exprtype>.<size>.<iface>.<carrier>.pcap.csv"""
    parts = fname.split(".")
    # ['client','1529470329','olia','laptop#3','M','F100','wlan16','U4','pcap','csv']
    if len(parts) < 10 or parts[0] != "client":
        return None
    return {
        "epoch": parts[1],
        "cc": parts[2],
        "device": parts[3],
        "exprtype": parts[4],
        "size": parts[5],
        "iface": parts[6],
        "carrier": parts[7],
    }


def trace_id(meta):
    return "hsr_e{epoch}_{cc}_{carrier}_{iface}".format(**meta)


def group_id(meta):
    return "hsr_e{epoch}_{cc}".format(**meta)


# ----------------------------------------------------------------------------- core parse
def read_packets(path):
    """Read the raw tshark CSV, return a cleaned DataFrame plus a drop ledger.

    Rows kept: TCP packets belonging to the download connection (srcport==8000 OR
    dstport==8000). Everything else is dropped and COUNTED with a reason.
    """
    drops = defaultdict(int)
    df = pd.read_csv(
        path,
        usecols=[
            "frame.time_epoch", "tcp.srcport", "tcp.dstport",
            "tcp.seq", "tcp.len", "tcp.flags", "tcp.analysis.ack_rtt",
        ],
        dtype={
            "frame.time_epoch": "float64",
            "tcp.srcport": "float64",
            "tcp.dstport": "float64",
            "tcp.seq": "float64",
            "tcp.len": "float64",
            "tcp.flags": "string",
            "tcp.analysis.ack_rtt": "float64",
        },
        engine="c",
    )
    df.columns = ["t", "sp", "dp", "seq", "len", "flags", "ack_rtt"]
    n_raw = len(df)

    # unparseable time or ports -> drop
    bad = df["t"].isna() | df["sp"].isna() | df["dp"].isna()
    drops["unparseable_time_or_port"] += int(bad.sum())
    df = df[~bad].copy()

    df["sp"] = df["sp"].astype("int64")
    df["dp"] = df["dp"].astype("int64")
    df["len"] = df["len"].fillna(0).astype("int64")
    df["seq"] = df["seq"].fillna(-1).astype("int64")

    # keep only packets of the download connection
    keep = (df["sp"] == SERVER_PORT) | (df["dp"] == SERVER_PORT)
    drops["not_download_connection"] += int((~keep).sum())
    df = df[keep].copy()

    # capture order == time order for pcap; make sure
    df.sort_values("t", kind="stable", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df, n_raw, dict(drops)


def detect_retransmissions(df):
    """Mark downlink (server->client) data packets that are TCP retransmissions.

    Faithful to the authors' retrans.m: within a connection, a data segment whose
    sequence number is below the running high-water mark of already-seen sequence
    numbers is a retransmission. Connections are segmented by client port (the
    dst port of downlink packets); the high-water mark resets on a SYN.

    Relative sequence numbers (tshark) start at 0 per connection and, for these runs
    (<=~210 MB per subflow), never approach 2^31, so no wrap handling is needed.
    Returns a boolean Series aligned to df, True on downlink data retransmissions.
    """
    is_dl_data = (df["sp"] == SERVER_PORT) & (df["len"] > 0)
    retr = pd.Series(False, index=df.index)
    if not is_dl_data.any():
        return retr, is_dl_data

    sub = df[is_dl_data]
    conn = sub["dp"]                      # client port identifies the connection
    seq = sub["seq"]
    is_syn = sub["flags"].isin(SYN_FLAGS)

    # segment id increments on each SYN within a connection (handles rare reuse)
    seg = is_syn.groupby(conn).cumsum()
    key = list(zip(conn.values, seg.values))
    key = pd.Series(key, index=sub.index)

    prev_max = seq.groupby(key).cummax().groupby(key).shift(1)
    retr_sub = seq < prev_max             # NaN prev_max (first in segment) -> False
    retr_sub = retr_sub.fillna(False)
    retr.loc[sub.index] = retr_sub.values
    return retr, is_dl_data


def build_timeseries(df, group_t0, meta, n_raw, drops):
    """Aggregate packets into a 1 s unified-schema DataFrame."""
    retr, is_dl_data = detect_retransmissions(df)
    df = df.assign(_retr=retr, _dldata=is_dl_data)

    # group-relative time -> integer second bin
    df["trel"] = df["t"] - group_t0
    df["bin"] = np.floor(df["trel"]).astype("int64")

    is_dl = df["sp"] == SERVER_PORT
    is_ul = df["dp"] == SERVER_PORT

    dl_bytes = df.loc[is_dl].groupby("bin")["len"].sum()
    ul_bytes = df.loc[is_ul].groupby("bin")["len"].sum()
    dl_datpkts = df.loc[df["_dldata"]].groupby("bin").size()
    dl_retr = df.loc[df["_dldata"] & df["_retr"]].groupby("bin").size()

    # network-RTT samples = wireshark ack_rtt on SERVER->client packets (handshake /
    # server-acks-client-segment). client->server ack_rtt is local ACK delay -> excluded.
    rtt_src = df.loc[is_dl & df["ack_rtt"].notna(), ["bin", "ack_rtt"]]
    rtt_med = (rtt_src.assign(rtt_ms=rtt_src["ack_rtt"] * 1000.0)
                      .groupby("bin")["rtt_ms"].median()) if len(rtt_src) else pd.Series(dtype="float64")

    b_lo = int(df["bin"].min())
    b_hi = int(df["bin"].max())
    bins = list(range(b_lo, b_hi + 1))

    rows = []
    cell = meta["carrier"]                       # carrier id (constant per subflow)
    for b in bins:
        dlb = int(dl_bytes.get(b, 0))
        ulb = int(ul_bytes.get(b, 0))
        ndat = int(dl_datpkts.get(b, 0))
        nretr = int(dl_retr.get(b, 0))
        loss = (nretr / ndat) if ndat > 0 else None
        rttv = rtt_med.get(b, np.nan)
        ts_utc = datetime.fromtimestamp(group_t0 + b, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        rows.append({
            "t_s": float(b),
            "ts_utc": ts_utc,
            "bearer": "cellular-subflow",
            "net_mode": "",
            "dl_mbps": round(dlb * 8.0 / 1e6 / BIN_S, 6),
            "ul_mbps": round(ulb * 8.0 / 1e6 / BIN_S, 6),
            "rtt_ms": ("" if (isinstance(rttv, float) and np.isnan(rttv)) else round(float(rttv), 3)),
            "loss": ("" if loss is None else round(loss, 6)),
            "cell_id": cell,
            "rsrp_dbm": "", "sinr_db": "", "speed_kmh": "", "lat": "", "lon": "",
        })
    out = pd.DataFrame(rows, columns=UNIFIED_COLUMNS)

    # per-trace stats for the data card
    total_dl_data = int(df["_dldata"].sum())
    total_retr = int((df["_dldata"] & df["_retr"]).sum())
    dl_series = out["dl_mbps"].astype(float)
    stats = {
        "trace_id": trace_id(meta),
        "group_id": group_id(meta),
        "carrier": meta["carrier"],
        "iface": meta["iface"],
        "cc": meta["cc"],
        "size_code": meta["size"],
        "epoch": meta["epoch"],
        "start_utc": datetime.fromtimestamp(df["t"].iloc[0], tz=timezone.utc).isoformat().replace("+00:00", "Z"),
        "rel_start_s": round(float(df["trel"].iloc[0]), 3),
        "duration_s": round(float(df["t"].iloc[-1] - df["t"].iloc[0]), 1),
        "rows": len(out),
        "raw_packet_rows": int(n_raw),
        "pkts_kept": int(len(df)),
        "drops": drops,
        "pct_nonempty": {
            "dl_mbps": round(100.0 * (dl_series.notna()).mean(), 1),
            "ul_mbps": 100.0,
            "rtt_ms": round(100.0 * (out["rtt_ms"] != "").mean(), 1),
            "loss": round(100.0 * (out["loss"] != "").mean(), 1),
        },
        "dl_mbps_min": round(float(dl_series.min()), 3),
        "dl_mbps_median": round(float(dl_series.median()), 3),
        "dl_mbps_mean": round(float(dl_series.mean()), 3),
        "dl_mbps_max": round(float(dl_series.max()), 3),
        "stall_seconds_dl_zero": int((dl_series == 0).sum()),
        "dl_data_pkts": total_dl_data,
        "retransmissions": total_retr,
        "overall_retrans_rate": round(total_retr / max(total_dl_data, 1), 6),
        "rtt_samples": int(len(rtt_src)),
        "rtt_sample_values_ms": [round(float(v) * 1000.0, 1) for v in rtt_src["ack_rtt"].tolist()],
    }
    return out, stats


# ----------------------------------------------------------------------------- driver
def process_dir(flow_dir, label, out_dir, manifest):
    files = sorted(f for f in os.listdir(flow_dir)
                   if f.endswith(".pcap.csv") and not f.startswith("."))
    metas = {}
    groups = defaultdict(list)
    for f in files:
        m = parse_filename(f)
        if m is None:
            manifest["file_level_drops"].append({"file": f, "reason": "unrecognized_filename"})
            continue
        metas[f] = m
        groups[group_id(m)].append(f)

    # group start time = earliest first-packet epoch across the group's subflows
    group_t0 = {}
    first_epoch = {}
    for f, m in metas.items():
        path = os.path.join(flow_dir, f)
        # cheap first-data-row epoch read
        with open(path, "r") as fh:
            rd = _csv.reader(fh)
            next(rd, None)  # header
            t0 = None
            for row in rd:
                try:
                    t0 = float(row[0]); break
                except Exception:
                    continue
        first_epoch[f] = t0
    for gid, fs in groups.items():
        group_t0[gid] = min(first_epoch[f] for f in fs if first_epoch[f] is not None)

    for f in files:
        if f not in metas:
            continue
        m = metas[f]
        path = os.path.join(flow_dir, f)
        df, n_raw, drops = read_packets(path)
        if len(df) == 0:
            manifest["file_level_drops"].append({"file": f, "reason": "no_download_packets"})
            continue
        gt0 = group_t0[group_id(m)]
        out, stats = build_timeseries(df, gt0, m, n_raw, drops)
        stats["flow_class"] = label
        out_path = os.path.join(out_dir, trace_id(m) + ".csv")
        out.to_csv(out_path, index=False)
        manifest["traces"].append(stats)
        print("  wrote %-42s rows=%4d dur=%5.1fs dl[min/med/max]=%.2f/%.2f/%.2f Mbps "
              "stalls=%d retr=%.3f%% rttN=%d"
              % (trace_id(m) + ".csv", stats["rows"], stats["duration_s"],
                 stats["dl_mbps_min"], stats["dl_mbps_median"], stats["dl_mbps_max"],
                 stats["stall_seconds_dl_zero"], 100 * stats["overall_retrans_rate"],
                 stats["rtt_samples"]))

    # record grouping
    for gid, fs in groups.items():
        manifest["groups"].append({
            "group_id": gid,
            "flow_class": label,
            "group_t0_utc": datetime.fromtimestamp(group_t0[gid], tz=timezone.utc).isoformat().replace("+00:00", "Z"),
            "subflows": [trace_id(metas[f]) for f in sorted(fs)],
            "carriers": sorted(metas[f]["carrier"] for f in fs),
        })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mouse", action="store_true", help="also process short mouse flows")
    args = ap.parse_args()

    if not os.path.isdir(RAW_ROOT):
        raise SystemExit(f"raw directory not found: {RAW_ROOT}\n"
                         f"  Obtain the HSR-MPTCP dataset and place it there; see ../SOURCES.md.")

    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(CARD_DIR, exist_ok=True)

    manifest = {"traces": [], "groups": [], "file_level_drops": [],
                "mouse_excluded": None, "server_port": SERVER_PORT, "bin_s": BIN_S}

    el_dir = os.path.join(RAW_ROOT, "elephant")
    print("Processing ELEPHANT flows from", el_dir)
    process_dir(el_dir, "elephant", OUT_DIR, manifest)

    mo_dir = os.path.join(RAW_ROOT, "mouse")
    if args.mouse:
        print("Processing MOUSE flows from", mo_dir)
        process_dir(mo_dir, "mouse", OUT_DIR, manifest)
    else:
        n_mouse = len([f for f in os.listdir(mo_dir)
                       if f.endswith(".pcap.csv") and not f.startswith(".")])
        manifest["mouse_excluded"] = {
            "count_files": n_mouse,
            "reason": ("Short fixed-size downloads (64/256/1024 KB); per-subflow capture "
                       "spans ~0.6-1.2 s (3-400 packets), i.e. <=1-2 one-second bins. Too "
                       "short to form a per-second bearer time series with capacity-collapse "
                       "dynamics, which is what P1/P2 need. They measure short-flow completion "
                       "time, not sustained bearer behaviour. Re-enable with --mouse."),
        }
        print("Skipped %d MOUSE files (too short for a per-second series; see data card)." % n_mouse)

    with open(MANIFEST, "w") as fh:
        json.dump(manifest, fh, indent=2)
    print("\nManifest -> %s\nProcessed CSVs -> %s/" % (MANIFEST, OUT_DIR))
    print("Traces written: %d  Groups: %d" % (len(manifest["traces"]), len(manifest["groups"])))


if __name__ == "__main__":
    main()
