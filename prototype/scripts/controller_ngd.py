"""Never Go Dark on real WireGuard: runs the SHIPPED policy file unmodified.

The exact file the simulator evaluates (katim-sim/policies/policy.py, mounted read-only
at /katim/policies) is loaded and called every 100 ms with an Obs built from what this
gateway measures on the real tunnels (observable inputs only, as in SPEC §7):
  keepalive RTT EWMA / trend / loss / staleness / dead-peer flag  <- its own probes
  delivered goodput EWMA                                           <- HQ receiver report
  queue backlog + head age                                         <- its own tc queues
  bearer class hint + candidate availability                       <- router (health probe)
Its Actions are executed as real WireGuard operations: warm_standby -> configure the
standby peer + handshake rounds; cutover -> move the route; teardown -> remove the peer.
If the active tunnel dies with no standby ready, the gateway falls back to the documented
reactive recovery (dead-peer detection, 10 s retry, handshake), exactly as in the simulator.
"""
import argparse
import importlib.util
import json
import re
import sys
import threading
import time

sys.path.insert(0, "/katim")
from policies.interface import (BearerClass, BearerHint, LinkTelemetry, N_PLANES,  # noqa: E402
                                PlaneState, Obs, StandbyState)
from controller import Ctl, KA_S, DPD_MISS, RETRY_S  # noqa: E402
from net import sh  # noqa: E402

TICK = 0.1
PROBE_S = 0.25              # telemetry probe cadence: sub-KA_OVERDUE_S, so a healthy link's
                           # telemetry stays "fresh" exactly as the simulator feeds it (100 ms ticks).
DPD_DEAD_S = DPD_MISS * KA_S  # dead peer only after KATIM's documented 3x1 s keepalives,
                           # independent of the (faster) telemetry probe rate.
A_READY_S = 5.0            # router hold-down (interface dampening): re-advertise a recovered
                           # primary as a ready candidate only after it has been CONTINUOUSLY
                           # reachable this long. A single keepalive answering is not a link
                           # ready to carry load; without this the policy flaps on a marginal,
                           # still-recovering 5G link (liveness != capacity).
CLS = {"a": BearerClass.FIVEG, "b": BearerClass.GEO}


def load_shipped(path="/katim/policies/policy.py"):
    spec = importlib.util.spec_from_file_location("shipped_policy", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return type(mod.POLICY)


class NGD(Ctl):
    def __init__(self, run):
        super().__init__(run, "ngd")
        self.pol = load_shipped()()
        self.pol.reset("prototype", 0)
        self.lk = threading.Lock()
        self.rtt_ewma, self.prev_ewma, self.trend, self.loss = None, None, 0.0, 0.0
        self.last_ok, self.misses, self.broke_t = time.time(), 0, None
        self.a_ok_t = time.time()           # last time the primary answered a health probe
        self.a_up_since = time.time()       # start of the primary's current continuous-up run
        self.gp, self.last_rx = 0.0, None
        self.warming = self.ready = False
        self.sb_t = None
        self.recovering = False

    # ---- telemetry --------------------------------------------------------------
    def ka_loop(self, until):
        while time.time() < until:
            t = time.time()
            n = self.active
            rtt = self.probe(n)
            with self.lk:
                if rtt is None:
                    self.misses += 1
                    self.broke_t = self.broke_t or t
                    self.loss = 0.7 * self.loss + 0.3
                else:
                    self.misses, self.broke_t, self.last_ok = 0, None, time.time()
                    self.loss = 0.7 * self.loss
                    self.prev_ewma = self.rtt_ewma
                    self.rtt_ewma = rtt if self.rtt_ewma is None else 0.7 * self.rtt_ewma + 0.3 * rtt
                    self.trend = (self.rtt_ewma - self.prev_ewma) if self.prev_ewma is not None else 0.0
            # Primary (bearer A) liveness for the router's candidate-ready signal: its own
            # probe when A is active, else a dedicated health probe while riding the standby.
            a_alive = (rtt is not None) if n == "a" else (self.probe("a", timeout=0.8) is not None)
            with self.lk:
                if a_alive:
                    self.a_ok_t = time.time()
                    self.a_up_since = self.a_up_since or time.time()
                else:
                    self.a_up_since = None
            time.sleep(max(0.0, PROBE_S - (time.time() - t)))

    def goodput(self, dt):
        try:
            rep = json.load(open("/app/run/hq_rx.json"))
        except (OSError, ValueError):
            return self.gp
        dev = f"wg{self.active}"
        if self.last_rx is not None and dev in rep and dev in self.last_rx:
            mbps = max(0.0, (rep[dev] - self.last_rx[dev]) * 8 / 1e6 / max(dt, 1e-3))
            self.gp = 0.7 * self.gp + 0.3 * mbps
        self.last_rx = rep
        return self.gp

    def backlog_bytes(self):
        out = sh(f"tc -s qdisc show dev wg{self.active}", check=False)
        m = re.search(r"backlog (\d+)b", out)
        return int(m.group(1)) if m else 0

    def obs(self, dt):
        now = time.time()
        with self.lk:
            rtt, trend, loss = self.rtt_ewma, self.trend, self.loss
            age = now - self.last_ok
            dead = age >= DPD_DEAD_S
        gp = self.goodput(dt)
        bl = self.backlog_bytes()
        head = min(30.0, bl * 8 / max(gp, 0.01) / 1e6)
        planes = tuple(PlaneState(i, 1 if (i == 3 and bl) else 0, bl if i == 3 else 0,
                                  head if i == 3 else 0.0, 0) for i in range(N_PLANES))
        cand = None
        if self.active == "a":
            cand = BearerClass.GEO                   # satellite always offered by the router
        elif self.a_up_since is not None and now - self.a_up_since >= A_READY_S:
            cand = BearerClass.FIVEG                 # primary re-advertised only after the hold-down
        sb = StandbyState(candidate=cand, warming=self.warming, ready=self.ready, bytes_spent=0,
                          age_s=(now - self.sb_t) if self.sb_t else 0.0)
        link = LinkTelemetry(keepalive_rtt_ms=rtt, rtt_trend=trend, loss_est=loss, tput_est_mbps=gp,
                             telemetry_age_s=min(age, 120.0), dpd_missed=1 if dead else 0)
        return Obs(t_s=now, planes=planes, link=link, messages=tuple(),
                   bearer_hint=BearerHint(current_bearer_class=CLS[self.active], candidate_available=cand),
                   standby=sb, modem=None)

    # ---- actions --------------------------------------------------------------------
    def warm(self):
        target = "b" if self.active == "a" else "a"
        self.warming, self.sb_t = True, time.time()
        self.log("predict", f"warm_{target}")
        ok = self.warm_b() if target == "b" else self.warm_a()
        self.warming, self.ready = False, ok

    def warm_a(self):
        """Re-establish the primary tunnel: handshake rounds on wga (same cost model)."""
        self.log("hs_start", "wga")
        t, done = time.time(), 0
        while done < 3 and time.time() - t < RETRY_S:
            if self.probe("a", timeout=2.0, payload=b"k" * 1200) is not None:
                done += 1
        self.log("hs_done" if done >= 3 else "hs_fail", f"{time.time() - t:.2f}s")
        return done >= 3

    def cutover(self):
        target = "b" if self.active == "a" else "a"
        if target == "b":
            self.mark_carrying_b()
        self.route(target)
        with self.lk:
            self.misses, self.broke_t, self.last_ok = 0, None, time.time()
            self.rtt_ewma = self.prev_ewma = None
        self.ready, self.sb_t = False, None
        if target == "a":
            with self.lk:
                if self.standby_up_t is None:
                    self.standby_up_t = time.time()      # wgb stays up as a warm standby

    def teardown(self):
        if self.active == "a":
            self.cool_b()
        self.ready = self.warming = False
        self.sb_t = None
        self.log("teardown", "standby")

    def reactive_recover(self, until):
        """No standby ready and the tunnel is dead: documented reactive path."""
        self.recovering = True
        self.log("dead", self.active)
        with self.lk:
            attempt = (self.broke_t or time.time()) + RETRY_S
        while time.time() < until:
            time.sleep(max(0.0, attempt - time.time()))
            if self.warm_b():
                self.mark_carrying_b()
                self.route("b")
                break
            attempt += RETRY_S
        self.recovering = False

    def run_ngd(self, until):
        self.route("a")
        threading.Thread(target=self.ka_loop, args=(until,), daemon=True).start()
        last = time.time()
        k = 0
        while time.time() < until:
            t = time.time()
            o = self.obs(t - last)
            last = t
            act = self.pol.decide(o)
            k += 1
            if k % 5 == 0:
                self.log("p", f"{self.pol.last_p:.3f}")
            if act.warm_standby is not None and not self.warming and not self.ready:
                threading.Thread(target=self.warm, daemon=True).start()
            if act.cutover and self.ready:
                self.log("cutover", "policy")
                self.cutover()
            if act.teardown_standby and (self.ready or self.warming):
                self.teardown()
            dead = o.link.dpd_missed > 0
            if dead and not self.ready and not self.recovering and self.active == "a":
                threading.Thread(target=self.reactive_recover, args=(until,), daemon=True).start()
            time.sleep(max(0.0, TICK - (time.time() - t)))
        self.idle(until)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--duration", type=float, required=True)
    ap.add_argument("--t0", type=float, required=True)
    # bearer classes the router reports (default = the original Cork 5G + GEO setup)
    ap.add_argument("--class-a", default=BearerClass.FIVEG.value, choices=[c.value for c in BearerClass])
    ap.add_argument("--class-b", default=BearerClass.GEO.value, choices=[c.value for c in BearerClass])
    a = ap.parse_args()
    CLS = {"a": BearerClass(a.class_a), "b": BearerClass(a.class_b)}
    while time.time() < a.t0:
        time.sleep(0.01)
    NGD(a.run).run_ngd(a.t0 + a.duration)
