"""Vehicle-side continuity controller: four mechanisms on real WireGuard tunnels.

Observable inputs only (what a real gateway sees): its own keepalive probes on the
tunnels it runs (reply or miss, and RTT). No trace values, no event schedule.

Modes
  reactive  documented reactive: keepalive 1 s, dead after 3 misses, retry anchored 10 s
            after the break, then a full handshake on bearer B (WireGuard handshake +
            extra round trips carrying KB-scale key material = classical+PQ stand-in).
  mobike    IKEv2 MOBIKE / WireGuard roaming: same session, endpoint moved to bearer B
            after dead-peer detection; no new handshake.
  atsss     3GPP ATSSS active-standby: both tunnels up all the time; switch when the
            active leg's RTT > 2x base or a probe is lost; switch back after 5 s healthy.
  p2        ours (v0 heuristic trigger, replaced by the learned predictor later): warm
            the standby when the active leg shows early degradation, cut over before it
            breaks, tear the standby down when the active leg has been healthy again.
"""
import argparse
import os
import socket
import statistics
import threading
import time
from net import sh, pub, inner_ip, UNDERLAY, PORT, SERVICE, ECHO_PORT, underlay_dev

KA_S, DPD_MISS, RETRY_S = 1.0, 3, 10.0          # SPEC §4.2 (KA, DPD [I4]; retry [D])
HS_ROUNDS, HS_BYTES, HS_TIMEOUT_S = 3, 4096, 10.0  # SPEC §4.3 [I5,I6]; attempt timeout = retry period
HQ = "hq"


class Ctl:
    def __init__(self, run, mode):
        self.run, self.mode = run, mode
        self.t0 = time.time()
        self.lock = threading.Lock()
        self.ev = open(f"/app/logs/ctl_{run}.csv", "w", buffering=1)
        self.ev.write("t,event,detail\n")
        self.active = None
        self.standby_up_t = None
        self.standby_s = 0.0
        self.devb = underlay_dev("10.10.2.")
        self.txb0 = self.txb()

    def txb(self):
        return int(open(f"/sys/class/net/{self.devb}/statistics/tx_bytes").read())

    def log(self, event, detail=""):
        self.ev.write(f"{time.time() - self.t0:.3f},{event},{detail}\n")

    def route(self, n):
        sh(f"ip route replace {SERVICE[HQ]}/32 dev wg{n} src {SERVICE['vehicle']}")
        self.active = n
        self.log("route", f"wg{n}")

    def probe(self, n, timeout=None, payload=b""):
        """UDP echo through tunnel n. Returns RTT in ms, or None on loss."""
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((inner_ip(n, "vehicle"), 0))
            s.settimeout(timeout or KA_S)
            t = time.time()
            tag = f"{self.run}|{t:.6f}|".encode()
            s.sendto(tag + payload, (inner_ip(n, HQ), ECHO_PORT))
            while True:
                data, _ = s.recvfrom(2048)
                if data.startswith(tag):
                    return 1000 * (time.time() - t)
        except (socket.timeout, OSError):
            return None
        finally:
            s.close()

    # ---- standby tunnel on bearer B --------------------------------------------
    def warm_b(self):
        """Configure the B peer and run the handshake. True when the tunnel is ready."""
        sh(f"wg set wgb peer {pub(HQ, 'b')} endpoint {UNDERLAY[HQ]['b']}:{PORT['b']} "
           f"allowed-ips {inner_ip('b', HQ)}/32,{SERVICE[HQ]}/32 persistent-keepalive 1")
        with self.lock:
            if self.standby_up_t is None:
                self.standby_up_t = time.time()
        self.log("hs_start", "wgb")
        t = time.time()
        chunk = HS_BYTES // HS_ROUNDS
        done = 0
        while done < HS_ROUNDS and time.time() - t < HS_TIMEOUT_S:
            # round 1 also triggers the real WireGuard handshake
            if self.probe("b", timeout=2.0, payload=b"k" * min(chunk, 1200)) is not None:
                for _ in range(max(0, chunk // 1200 - 1)):
                    self.probe("b", timeout=2.0, payload=b"k" * 1200)
                done += 1
        ok = done >= HS_ROUNDS
        self.log("hs_done" if ok else "hs_fail", f"{time.time() - t:.2f}s")
        return ok

    def cool_b(self):
        sh(f"wg set wgb peer {pub(HQ, 'b')} remove", check=False)
        with self.lock:
            if self.standby_up_t is not None:
                self.standby_s += time.time() - self.standby_up_t
                self.standby_up_t = None
        self.log("standby_down", "wgb")

    def mark_carrying_b(self):
        """Once B carries the traffic it is no longer standby: stop the standby clock."""
        with self.lock:
            if self.standby_up_t is not None:
                self.standby_s += time.time() - self.standby_up_t
                self.standby_up_t = None

    # ---- modes -----------------------------------------------------------------
    def run_reactive(self, until):
        self.route("a")
        misses, broke_t = 0, None
        while time.time() < until:
            t = time.time()
            rtt = self.probe("a")
            self.log("ka", f"a,{rtt if rtt is not None else -1:.1f}")
            if rtt is None:
                misses += 1
                broke_t = broke_t or t
            else:
                misses, broke_t = 0, None
            if misses >= DPD_MISS:
                self.log("dead", "a")
                attempt_t = broke_t + RETRY_S
                while time.time() < until:
                    time.sleep(max(0.0, attempt_t - time.time()))
                    if self.warm_b():
                        self.mark_carrying_b()
                        self.route("b")
                        break
                    attempt_t += RETRY_S
                break
            time.sleep(max(0.0, KA_S - (time.time() - t)))
        self.idle(until)

    def run_mobike(self, until):
        self.route("m")
        misses = 0
        while time.time() < until:
            t = time.time()
            rtt = self.probe("m")
            self.log("ka", f"m,{rtt if rtt is not None else -1:.1f}")
            misses = misses + 1 if rtt is None else 0
            if misses >= DPD_MISS:
                self.log("dead", "m")
                sh(f"wg set wgm peer {pub(HQ, 'm')} endpoint {UNDERLAY[HQ]['b']}:{PORT['m']}")
                self.log("roam", "wgm->bearer_b")
                break
            time.sleep(max(0.0, KA_S - (time.time() - t)))
        self.idle(until)

    def baseline_rtt(self, n, k=5):
        vals = [v for v in (self.probe(n) for _ in range(k)) if v is not None]
        return statistics.median(vals) if vals else 75.0

    def run_atsss(self, until):
        self.route("a")
        base = self.baseline_rtt("a")
        self.log("base_rtt", f"{base:.1f}")
        self.warm_b()                                   # both legs established, always
        healthy_since = None
        while time.time() < until:
            t = time.time()
            rtt = self.probe("a")
            self.log("ka", f"a,{rtt if rtt is not None else -1:.1f}")
            bad = rtt is None or rtt > 2 * base
            if self.active == "a" and bad:
                self.route("b")
            elif self.active == "b":
                healthy_since = (healthy_since or t) if not bad else None
                if healthy_since and t - healthy_since >= 5.0:
                    self.route("a")
                    healthy_since = None
            time.sleep(max(0.0, KA_S - (time.time() - t)))
        self.idle(until)

    def run_p2(self, until):
        self.route("a")
        base = self.baseline_rtt("a")
        self.log("base_rtt", f"{base:.1f}")
        warming = threading.Event()
        ready = threading.Event()
        healthy_since = None
        hist = []

        def warm():
            if self.warm_b():
                ready.set()
            warming.clear()

        rsrp_hist = []
        while time.time() < until:
            t = time.time()
            rtt = self.probe("a")
            self.log("ka", f"a,{rtt if rtt is not None else -1:.1f}")
            hist = (hist + [rtt])[-3:]
            modem = read_modem()
            if modem.get("rsrp_dbm") is not None:
                rsrp_hist = (rsrp_hist + [modem["rsrp_dbm"]])[-20:]
            rsrp_drop = len(rsrp_hist) >= 5 and \
                statistics.median(rsrp_hist) - rsrp_hist[-1] >= 6 and rsrp_hist[-1] <= -105
            early = rtt is None or rtt > 1.5 * base or rsrp_drop or \
                (len(hist) == 3 and None not in hist and hist[0] < hist[1] < hist[2] and hist[2] > 1.2 * base)
            if rsrp_drop:
                self.log("modem", f"rsrp {rsrp_hist[-1]} median {statistics.median(rsrp_hist)}")
            bad = rtt is None or rtt > 2 * base
            cold = self.standby_up_t is None and not ready.is_set() and self.active == "a"
            if early and cold and not warming.is_set():
                self.log("predict", "warm_b")
                warming.set()
                threading.Thread(target=warm, daemon=True).start()
            if self.active == "a" and bad and ready.is_set():
                self.mark_carrying_b()
                self.route("b")
            healthy = rtt is not None and rtt < 1.3 * base
            healthy_since = (healthy_since or t) if healthy else None
            if self.active == "b" and healthy_since and t - healthy_since >= 10.0:
                self.route("a")
                with self.lock:
                    self.standby_up_t = time.time()      # b is standby again
            if self.active == "a" and ready.is_set() and healthy_since and t - healthy_since >= 15.0:
                self.cool_b()
                ready.clear()
            time.sleep(max(0.0, KA_S - (time.time() - t)))
        self.idle(until)

    def idle(self, until):
        while time.time() < until:
            time.sleep(0.2)
        self.mark_carrying_b()
        self.log("summary", f"standby_s={self.standby_s:.2f},bearer_b_tx_bytes={self.txb() - self.txb0}")


def read_modem():
    """Bearer-A modem status (signal, access mode) as exported by the vehicle router."""
    import json
    try:
        m = json.load(open("/app/run/modem_a.json"))
        return m if time.time() - m.get("t", 0) < 3 else {}
    except (OSError, ValueError):
        return {}


def reset():
    sh(f"wg set wgb peer {pub(HQ, 'b')} remove", check=False)
    sh(f"wg set wgm peer {pub(HQ, 'm')} endpoint {UNDERLAY[HQ]['a']}:{PORT['m']}")
    sh(f"ip route replace {SERVICE[HQ]}/32 dev wga src {SERVICE['vehicle']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["reactive", "mobike", "atsss", "p2"])
    ap.add_argument("--run")
    ap.add_argument("--duration", type=float)
    ap.add_argument("--t0", type=float)
    ap.add_argument("--reset", action="store_true")
    a = ap.parse_args()
    if a.reset:
        reset()
        raise SystemExit(0)
    while time.time() < a.t0:
        time.sleep(0.01)
    c = Ctl(a.run, a.mode)
    getattr(c, f"run_{a.mode}")(a.t0 + a.duration)
