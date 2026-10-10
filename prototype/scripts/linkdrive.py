"""Replay a real measured trace onto bearer A; bearer B = GEO model. (v2: priority classes)

Two layers, mirroring the simulator and KATIM's documented design:
  * Gateway (inner WireGuard interfaces, before encryption): strict-priority classes
    voice + keepalives  >  video  >  bulk, drained at the bearer's capacity (tbf + prio).
    This is KATIM's documented "internal classes of service ... discard priority on
    congestion within the gateway" and the simulator's plane model.
  * Bearer (underlay): the trace's one-way delay and loss; all packets lost while
    the trace says the link is unusable.

Bearer A capacity: zero-order hold of the last measured row (2 s horizon, SPEC §2.3);
uplink = dl_mbps * UL_RATIO [I30: 0.53, Berlin V2X]; UCC ping-second throughput
artefact dropped [I34], same as the simulator. Bearer B = GEO MODEL (SPEC §3.6):
600 ms RTT, 18/3 Mbit/s, 0.1 % loss (FCC Measuring Broadband America).
"""
import argparse
import bisect
import csv
import os
import time
from net import sh, underlay_dev

ap = argparse.ArgumentParser()
ap.add_argument("--role", choices=["vehicle", "hq"], required=True)
ap.add_argument("--run", required=True)
ap.add_argument("--trace", required=True)
ap.add_argument("--start", type=float, required=True)
ap.add_argument("--duration", type=int, required=True)
ap.add_argument("--t0", type=float, required=True, help="shared wall-clock start")
ap.add_argument("--ul-ratio", type=float, default=0.53)
ap.add_argument("--base-owd-ms", type=float, default=37.5)
ap.add_argument("--hold-s", type=float, default=2.0)
ap.add_argument("--queue-s", type=float, default=1.0, help="gateway class buffer depth (s at current rate) [I]")
# Optional per-second timelines exported by export_timelines.py from katim-sim's own bearer
# models. Without them the original behaviour is unchanged (A = UCC replay, B = GEO model).
ap.add_argument("--timeline-a", default=None, help="replay bearer A from this timeline CSV instead of --trace")
ap.add_argument("--timeline-b", default=None, help="replay bearer B from this timeline CSV instead of the GEO model")
a = ap.parse_args()


def load_timeline(path):
    rows = list(csv.DictReader(open(path)))
    return [{"usable": r["usable"] == "1", "ul": float(r["cap_ul_mbps"]), "dl": float(r["cap_dl_mbps"]),
             "owd": float(r["owd_ms"]), "loss": float(r["loss_pct"]), "t": float(r["trace_t"])} for r in rows]


TL_A = load_timeline(a.timeline_a) if a.timeline_a else None
TL_B = load_timeline(a.timeline_b) if a.timeline_b else None


def tl_at(tl, k):
    return tl[min(k, len(tl) - 1)]

GEO = {"owd_ms": 300.0, "vehicle_mbit": 3.0, "hq_mbit": 18.0, "loss_pct": 0.1}
VOICE_PORT, VIDEO_PORT, BULK_PORT, ECHO_PORT = 5000, 5001, 5002, 9000

by_sec = {}
with open(a.trace) as f:
    for r in csv.DictReader(f):
        t = float(r["t_s"])
        prev = by_sec.get(t)
        r["_ping"] = bool(r["rtt_ms"]) or bool(prev and prev.get("_ping"))
        by_sec[t] = r                     # duplicated same-second rows: last real value
for r in by_sec.values():
    if r["_ping"]:
        r["dl_mbps"] = r["ul_mbps"] = ""  # ping-second throughput artefact: absent
rows = sorted(by_sec.items())
ts = [t for t, _ in rows]
dev_a, dev_b = underlay_dev("10.10.1."), underlay_dev("10.10.2.")


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bearer(dev, owd_ms, loss_pct, usable):
    """Underlay = propagation delay + loss only (capacity is enforced in the gateway)."""
    loss = 100.0 if not usable else loss_pct
    sh(f"tc qdisc replace dev {dev} root netem delay {owd_ms:.1f}ms loss {loss:.2f}% limit 100000")


def gateway_classes(dev, rate_mbit, setup):
    """Strict-priority classes inside the gateway, drained at the bearer capacity."""
    r = max(rate_mbit, 0.01)
    burst = max(1600, int(r * 1e6 / 8 * 0.01))          # 10 ms of tokens
    latency_ms = int(1000 * a.queue_s)
    if setup:
        sh(f"tc qdisc del dev {dev} root", check=False)
        sh(f"tc qdisc add dev {dev} root handle 1: tbf rate {r:.3f}mbit burst {burst} latency {latency_ms}ms")
        sh(f"tc qdisc add dev {dev} parent 1:1 handle 10: prio bands 3 priomap 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2 2")
        for port, band in ((VOICE_PORT, 1), (ECHO_PORT, 1), (VIDEO_PORT, 2)):
            sh(f"tc filter add dev {dev} parent 10: protocol ip prio 1 u32 match ip dport {port} 0xffff flowid 10:{band}")
            sh(f"tc filter add dev {dev} parent 10: protocol ip prio 1 u32 match ip sport {port} 0xffff flowid 10:{band}")
    else:
        sh(f"tc qdisc change dev {dev} root handle 1: tbf rate {r:.3f}mbit burst {burst} latency {latency_ms}ms")


def b_state(k):
    """(usable, gateway rate, owd_ms, loss_pct) of bearer B at second k."""
    if TL_B is None:
        return True, (GEO["vehicle_mbit"] if a.role == "vehicle" else GEO["hq_mbit"]), GEO["owd_ms"], GEO["loss_pct"]
    r = tl_at(TL_B, k)
    rate = r["ul"] if a.role == "vehicle" else (r["dl"] or r["ul"])
    return r["usable"], (rate if r["usable"] else 0.0), r["owd"], r["loss"]


b_usable, rate_b, b_owd, b_loss = b_state(0)
bearer(dev_b, b_owd, b_loss, b_usable)
if a.role == "vehicle":
    for d in ("wga", "wgb", "wgm"):
        gateway_classes(d, 1000.0, setup=True)

os.makedirs("/app/logs", exist_ok=True)
log = open(f"/app/logs/link_{a.run}_{a.role}.csv", "w", buffering=1)
log.write("wall_t,k,trace_t,a_usable,a_rate_mbit,a_owd_ms,a_loss_pct,net_mode,rsrp_dbm,b_usable,b_rate_mbit,b_owd_ms,b_loss_pct\n")

while time.time() < a.t0:
    time.sleep(0.01)

for k in range(a.duration + 1):
    t = a.start + k
    i = bisect.bisect_right(ts, t) - 1
    usable, rate, owd, loss, mode, rsrp = False, None, a.base_owd_ms, 0.0, "", ""
    if TL_A is not None:                       # bearer A from a sim-exported timeline
        r = tl_at(TL_A, k)
        t = r["t"]
        usable = r["usable"] and (r["ul"] if a.role == "vehicle" else r["dl"]) > 0.0
        rate = r["ul"] if a.role == "vehicle" else r["dl"]
        owd, loss = r["owd"], r["loss"]
    elif i >= 0 and t - ts[i] <= a.hold_s:
        r = rows[i][1]
        dl = num(r["dl_mbps"])
        mode, rsrp = r.get("net_mode", ""), r.get("rsrp_dbm", "")
        j = i
        while dl is None and j > 0 and t - ts[j - 1] <= a.hold_s:   # zero-order hold
            j -= 1
            dl = num(rows[j][1]["dl_mbps"])
        if dl is not None:
            rate = dl if a.role == "hq" else dl * a.ul_ratio
            usable = dl >= 0.05
        rtt = num(r["rtt_ms"])
        if rtt is not None:
            owd = rtt / 2
        lo = num(r["loss"])
        if lo is not None:
            loss = 100.0 * lo
    bearer(dev_a, owd, loss, usable)
    if TL_B is not None:                       # bearer B follows its measured timeline too
        b_usable, rate_b, b_owd, b_loss = b_state(k)
        bearer(dev_b, b_owd, b_loss, b_usable)
        rate_b = max(rate_b, 0.01)
    if a.role == "vehicle":
        cap_a = rate if (usable and rate) else 0.01
        gateway_classes("wga", cap_a, setup=False)
        gateway_classes("wgb", rate_b, setup=False)
        ep = sh("wg show wgm endpoints", check=False)
        gateway_classes("wgm", rate_b if "10.10.2.20" in ep else cap_a, setup=False)
        with open("/app/run/modem_a.tmp", "w") as m:
            m.write(f'{{"t": {time.time():.3f}, "rsrp_dbm": {rsrp or "null"}, "net_mode": "{mode}"}}')
        os.replace("/app/run/modem_a.tmp", "/app/run/modem_a.json")
    log.write(f"{time.time():.3f},{k},{t},{int(usable)},{rate if rate is not None else ''},"
              f"{owd:.1f},{loss:.2f},{mode},{rsrp},{int(b_usable)},{rate_b:.3f},{b_owd:.1f},{b_loss:.2f}\n")
    nxt = a.t0 + k + 1
    while time.time() < nxt:
        time.sleep(0.01)

bearer(dev_a, 1.0, 0.0, True)
if a.role == "vehicle":
    for d in ("wga", "wgb", "wgm"):
        gateway_classes(d, 1000.0, setup=False)
