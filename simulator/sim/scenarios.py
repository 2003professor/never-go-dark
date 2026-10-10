"""Scenarios — SPEC §6 + §3.7 stitching + §6.3 event rule + §13.1/§13.6/§13.8.

A scenario instance resolves to a concrete choice of real traces (plus, for
GEO, the measurement-parameterised model) and exposes, per tick, the
environment the engine needs: the two bearer legs' samples and which leg the
external black-side router currently selects.

§13.1 (fix A) — EVENT-ANCHORED, RANDOMISED, NO FORCED CUT.
  * a scenario instance pins a primary A-leg trace and (for a dual-leg
    scenario) a standby B-leg trace/model;
  * the A-leg replays its REAL data for the whole episode and becomes bad only
    when its own measurements say so; the external router (common to every
    mechanism) reactively steers A->B after the A-leg is genuinely bad for a
    detection hold and (§13.8) fails back to A once A has been good again for
    a longer hold;
  * ``prepare(seed)`` randomises, per (instance, seed): the episode length,
    the position of a REAL anchor event inside the episode, the A-leg and
    B-leg start offsets, and the phase of the gateway's free-running 10 s
    retry timer. ``transitions`` (real event times, metrics-only) is never
    handed to any policy except the frozen oracle.

§13.8 (red-team round) — NO RUN IDENTITY REACHES THE POLICY.
  * the observable-clock offset is drawn from an EVALUATOR-SECRET salt
    (``sim.params.eval_salt``), not from public inputs, so it cannot be
    precomputed from trace ids + seeds;
  * the policy's ``reset`` receives a constant (``NEUTRAL_TOKEN``, seed 0):
    no token, no seed, nothing that identifies the run;
  * anchor events are extracted from the capacity the gateway actually sees
    (UL direction, gateway ceiling, ping-second artefacts removed) and must
    PERSIST (``P.anchor_min_persist_s``), so logger artefacts and
    capped-away dips cannot anchor an episode.

Train / held-out split is by whole trace id (SPEC §6.4): an instance's split is
decided by its primary trace id (or HSR group id), independent of the seed.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from sim.bearers import (SIM_TICK_S, Bearer, BearerSample, GeoModelBearer,
                         GeoParams, TraceReplayBearer, build_leo_concatenated,
                         discover_traces, trace_is_heldout, H_TPUT,
                         PROCESSED_ROOT)
from sim.params import P, eval_salt, episode_salt

N_PER_SCENARIO = 4        # bounded instances per scenario (documented runtime)
RETRY_PERIOD_S = 10.0     # documented retry period (WP:323) for the phase draw

# The ONLY identity the policy's reset() ever receives (SPEC §13.8).
NEUTRAL_TOKEN = "episode"

SCENARIO_IDS = [
    "S1_lte5g_walk", "S2_5g_leo_drive", "S3_5g_geo_drive",
    "S4_wifi_edge", "S5_mptcp_hsr", "S6_collective_5g_geo",
    "S7_lumos_walk", "S8_lumos_drive",
]

# How each scenario is constructed — surfaced verbatim in every results table
# and figure (GEO = model, stitched = stitched, Starlink = concatenated).
SCENARIO_CONSTRUCTION = {
    "S1_lte5g_walk": "single real replay (UCC 5G Download capture), anchored on a real persistent degradation event",
    "S2_5g_leo_drive": "STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned",
    "S3_5g_geo_drive": "UCC 5G replay + GEO measurement-parameterised MODEL (not a replay)",
    "S4_wifi_edge": "STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned",
    "S5_mptcp_hsr": "real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching",
    "S6_collective_5g_geo": "UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load",
    "S7_lumos_walk": "STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned",
    "S8_lumos_drive": "STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED)",
}
CONSTRUCTION_COMMON = (
    "Capacity = UPLINK (red->black) direction: measured UL for Starlink and "
    "the GEO model; DL x UL/DL ratio 0.53 (Berlin V2X measured LTE) for "
    "cellular DL-only traces. RTT where a trace has none = labelled seeded "
    "draw from measured UCC 5G ping samples (Wi-Fi: class default).")

# event-rule thresholds (SPEC §6.3) [I15]  sweep S-EVENT
RTT_STEP = 2.0            # xN jump AND >= 100 ms
RTT_STEP_MIN_MS = 100.0
TPUT_DROP = 5.0           # xN drop within <= 2 s

# gateway throughput ceilings (SPEC §13.6): X9000R <=100 Mbps IMIX (X:41);
# 9001-R <=1 Gbps (R:53).
CAP_X9000R_MBPS = 100.0
CAP_9001R_MBPS = 1000.0

# bearer tier rank for "downgrade" detection (lower = worse)
_MODE_RANK = {"5G": 5, "CONNECTED": 5, "NR": 5,
              "LTE": 3, "NOT_RESTRICTED": 3, "4G": 3,
              "HSPA+": 2, "HSDPA": 2, "HSUPA": 2, "UMTS": 1, "WCDMA": 1,
              "EDGE": 0, "GPRS": 0, "NONE": 0, "": 3}


def link_ok(sample) -> bool:
    """A bearer can carry the lowest-rate real-time flow (and therefore
    keepalive echoes) this tick: present AND capacity x (1 - loss) >= the
    §12.2 floor (MELPe 2.4 kbps). The ONE deliverability rule shared by the
    router, DPD, the engine, the feasibility reference and the oracle."""
    if sample is None or not sample.usable:
        return False
    loss = sample.loss if sample.loss is not None else 0.0
    return sample.cap_mbps * (1.0 - min(0.99, max(0.0, loss))) \
        >= float(P.stall_goodput_mbps) and sample.cap_mbps > 0.0


@dataclass
class RoleState:
    bclass: str
    sample: Optional[BearerSample]   # None if this bearer is not present now
    endpoint: str


@dataclass
class EnvTick:
    """Two stable bearer roles + which one the external router routes by
    default THIS tick (used only for reactive re-establishment)."""
    roleA: RoleState
    roleB: Optional[RoleState]
    router_role: str


# ---------------------------------------------------------------------------
# metadata helpers
# ---------------------------------------------------------------------------
def _load_manifest(dataset: str) -> dict:
    p = os.path.join(PROCESSED_ROOT, dataset, "_manifest.json")
    if os.path.isfile(p):
        with open(p) as fh:
            return json.load(fh)
    return {}


# Capacity-source rule (SPEC §12.1) [I21]: terrestrial 5G capacity legs use
# saturating file-Download captures only.
UCC5G_CAPACITY_APPS = ("Download",)
APP_START_MIN_MBPS = 1.0   # transfer considered started at >= 1 Mbps   [I21]


def _ucc5g_capacity_row(r: dict) -> bool:
    return r.get("app") in UCC5G_CAPACITY_APPS


def _ucc5g_traces(split: str):
    m = _load_manifest("ucc5g")
    out = []
    for r in m.get("traces", []):
        if "5G" in r.get("modes", []) and _ucc5g_capacity_row(r):
            tid = r["trace_id"]
            if _split_ok(tid, split):
                out.append((tid, r))
    out.sort(key=lambda x: x[0])
    return out


def _split_ok(trace_id: str, split: str) -> bool:
    return (split == "heldout") == trace_is_heldout(trace_id)


def _hash_int(*parts) -> int:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h, 16) & ((1 << 63) - 1)


def _inst_rng(inst_id: str, seed: int):
    """Per-(instance, seed) generator for EPISODE randomisation. Mixed with
    the (optional, secret for held-out) episode salt (SPEC §13.8)."""
    return np.random.Generator(np.random.PCG64(
        _hash_int(episode_salt(), inst_id, seed)))


def _clock_offset(inst_id: str, seed: int) -> float:
    """Observable-clock offset from the EVALUATOR-SECRET salt (SPEC §13.8):
    not computable from trace ids, seeds or source code."""
    u = _hash_int("clk", eval_salt(), inst_id, seed) / float(1 << 63)
    return float(u * P.clock_offset_max_s)


# ---------------------------------------------------------------------------
# §6.3 handover-event extraction rule (documented, reproducible)
# ---------------------------------------------------------------------------
def extract_events(df: pd.DataFrame):
    """OR-combined handover-event rule on a processed CSV (SPEC §6.3), used
    by the predictor/validation. Returns sorted (t_s, reason)."""
    ev = []
    t = df["t_s"].to_numpy(dtype=float)
    nm = df.get("net_mode", pd.Series([""] * len(df))).astype(str).to_numpy()
    cid = df.get("cell_id", pd.Series([""] * len(df))).astype(str).to_numpy()
    rtt = pd.to_numeric(df.get("rtt_ms", pd.Series([np.nan] * len(df))),
                        errors="coerce").to_numpy()
    dl = pd.to_numeric(df.get("dl_mbps", pd.Series([np.nan] * len(df))),
                       errors="coerce").to_numpy()
    for i in range(1, len(t)):
        if nm[i] and nm[i - 1] and nm[i] != nm[i - 1]:
            ev.append((float(t[i]), f"mode {nm[i-1]}->{nm[i]}"))
        if cid[i] and cid[i - 1] and cid[i] != cid[i - 1]:
            ev.append((float(t[i]), "cell/AP change"))
        if not np.isnan(rtt[i]) and not np.isnan(rtt[i - 1]) and rtt[i - 1] > 0:
            if rtt[i] >= RTT_STEP * rtt[i - 1] and (rtt[i] - rtt[i - 1]) >= RTT_STEP_MIN_MS:
                ev.append((float(t[i]), "rtt step"))
        if not np.isnan(dl[i]) and dl[i - 1] > 0:
            if dl[i - 1] >= TPUT_DROP * max(dl[i], 1e-6) and (t[i] - t[i - 1]) <= 2.0:
                ev.append((float(t[i]), "tput collapse"))
    ev.sort()
    return ev


def degradation_events(bearer: TraceReplayBearer, with_reason=False):
    """Anchor events (SPEC §13.1 + §13.8): the subset of §6.3 events where the
    link the GATEWAY SEES got worse and STAYED worse:
      * a throughput collapse (>= TPUT_DROP x within <= 2 s) of the
        UL-direction, gateway-capped capacity that persists for
        ``P.anchor_min_persist_s`` (a Lumos5G 0-Mbps blockage is one);
      * the onset of an unusable gap at least that long;
      * a net_mode DOWNGRADE after which the capped capacity actually drops.
    Extracted on the bearer's own cleaned capacity series (UCC ping-second
    artefacts removed), never handed to a policy. Returns sorted times (or
    (t, reason) with ``with_reason``)."""
    t_all, cap_all = bearer.capacity_series()
    ok = ~np.isnan(cap_all)
    t = t_all[ok]
    c = cap_all[ok]
    persist = float(P.anchor_min_persist_s)
    ev = []
    n = len(t)
    for i in range(1, n):
        dt = t[i] - t[i - 1]
        # gap onset (bearer unusable from t[i-1]+H_TPUT until t[i])
        if dt > H_TPUT and (dt - H_TPUT) >= persist:
            ev.append((float(t[i - 1]) + H_TPUT, "gap onset"))
        if c[i - 1] > 0 and dt <= 2.0 and c[i - 1] >= TPUT_DROP * max(c[i], 1e-6):
            thr = c[i - 1] / TPUT_DROP
            j = i
            held = True
            while j < n and t[j] < t[i] + persist:
                if c[j] > thr:
                    held = False
                    break
                j += 1
            if held and (j < n or persist == 0.0):
                ev.append((float(t[i]), "tput collapse (persistent)"))
    nm_s = bearer._f["net_mode"]
    if nm_s.times.size > 1:
        times, vals = nm_s.times, nm_s.values
        for i in range(1, len(times)):
            a, b = str(vals[i - 1]), str(vals[i])
            if a and b and a != b and _MODE_RANK.get(b, 3) < _MODE_RANK.get(a, 3):
                tt = float(times[i])
                pre = c[(t >= tt - 2.0) & (t < tt)]
                post = c[(t >= tt) & (t < tt + max(2.0, persist))]
                if pre.size and post.size and float(np.mean(post)) < float(np.mean(pre)):
                    ev.append((tt, f"downgrade {a}->{b}"))
    ev.sort()
    out = []
    for e in ev:
        if not out or e[0] - out[-1][0] > 1.0:
            out.append(e)
    return out if with_reason else [e[0] for e in out]


# ---------------------------------------------------------------------------
# instance types
# ---------------------------------------------------------------------------
class ScenarioInstance:
    scenario = "base"

    def __init__(self, inst_id: str, duration_s: float, transitions: list,
                 label: str):
        self.inst_id = inst_id
        self.duration_s = duration_s
        self.transitions = transitions   # list of sim-time events (metrics only)
        self.label = label
        self.clock_offset = 0.0          # SPEC §13.1/§13.8 observable-clock offset
        self.token = NEUTRAL_TOKEN
        self.retry_phase_s = 0.0
        self.prepared_seed = None
        self._env = None

    def prepare(self, seed: int):
        return self

    def env(self, t_s: float) -> EnvTick:  # pragma: no cover
        raise NotImplementedError

    def env_list(self):
        """All per-tick EnvTicks for the prepared episode (cached; the env is
        a pure function of the prepared instance)."""
        if self._env is None:
            n = int(round(self.duration_s / SIM_TICK_S))
            self._env = [self.env(k * SIM_TICK_S) for k in range(n)]
        return self._env


class SingleInstance(ScenarioInstance):
    """One replayed bearer, no standby (used by unit tests)."""

    def __init__(self, scenario, inst_id, bearer, local_start, duration_s,
                 transitions, label):
        super().__init__(inst_id, duration_s, transitions, label)
        self.scenario = scenario
        self.bearer = bearer
        self.local_start = local_start
        self.bclass = bearer.bclass
        self.classA = bearer.bclass
        self.classB = None
        self.endpoint = f"{inst_id}:ep0"

    def prepare(self, seed: int):
        self.clock_offset = _clock_offset(self.inst_id, seed)
        self.retry_phase_s = float(_inst_rng(self.inst_id, seed).uniform(
            0.0, RETRY_PERIOD_S))
        self.prepared_seed = seed
        self._env = None
        return self

    def env(self, t_s):
        s = self.bearer.sample(self.local_start + t_s)
        return EnvTick(roleA=RoleState(self.bclass, s, self.endpoint),
                       roleB=None, router_role="A")


class EpisodeInstance(ScenarioInstance):
    """Event-anchored, randomised dual-leg (or single-leg) episode (SPEC
    §13.1/§13.8). The A-leg replays its real data for the whole episode; the
    B-leg (candidate standby) is reachable throughout. The router steers A->B
    after A is bad for ``P.router_failover_hold_s`` and back to A after A is
    good for ``P.router_failback_hold_s`` (``P.router_failback``)."""

    def __init__(self, scenario, inst_id, bearerA, classA, events_local,
                 a_bounds, b_factory, classB, label, router_mode="failover"):
        super().__init__(inst_id, P.episode_max_s, [], label)
        self.scenario = scenario
        self.bearerA = bearerA
        self.classA = classA
        self.events_local = sorted(events_local)
        self.a_bounds = a_bounds
        self.b_factory = b_factory
        self.classB = classB
        self.router_mode = router_mode
        self.epA = f"{inst_id}:A"
        self.epB = f"{inst_id}:B"
        self.bearerB = None
        self.b_offset = 0.0
        self.a_start = a_bounds[0]
        self._router = None

    def prepare(self, seed: int):
        if self.prepared_seed == seed and self._router is not None:
            return self
        rng = _inst_rng(self.inst_id, seed)
        no_rand = bool(getattr(self, "_no_randomize", False))
        self.clock_offset = 0.0 if no_rand else _clock_offset(self.inst_id, seed)
        self.retry_phase_s = float(rng.uniform(0.0, RETRY_PERIOD_S))
        t0, t1 = self.a_bounds
        span = t1 - t0
        if no_rand:
            dur = min(120.0, span)
            pos = 0.5
        else:
            dur = float(rng.uniform(P.episode_min_s, min(P.episode_max_s, max(
                P.episode_min_s, span))))
            dur = min(dur, span)
            pos = float(rng.uniform(P.event_pos_lo, P.event_pos_hi))
        feasible = [e for e in self.events_local
                    if (e - pos * dur) >= t0 and (e + (1 - pos) * dur) <= t1]
        if feasible:
            idx = len(feasible) // 2 if no_rand else int(rng.integers(0, len(feasible)))
            anchor = float(feasible[idx])
            a_start = anchor - pos * dur
        elif self.events_local:
            idx = len(self.events_local) // 2 if no_rand else int(
                rng.integers(0, len(self.events_local)))
            anchor = float(self.events_local[idx])
            a_start = min(max(t0, anchor - pos * dur), t1 - dur)
        else:
            a_start = float(rng.uniform(t0, max(t0, t1 - dur)))
        self.a_start = a_start
        self.duration_s = dur
        self.transitions = sorted(e - a_start for e in self.events_local
                                  if a_start <= e <= a_start + dur)
        if not self.transitions:
            self.transitions = [dur / 2.0]
        if self.b_factory is not None:
            self.bearerB, self.b_offset = self.b_factory(rng, seed, dur)
        else:
            self.bearerB, self.b_offset = None, 0.0
        self._env = None
        self._router = self._compute_router_timeline()
        self.prepared_seed = seed
        return self

    def _a_bad(self, t_s):
        return not link_ok(self.bearerA.sample(self.a_start + t_s))

    def _compute_router_timeline(self):
        """Reactive router (SPEC §13.1 + §13.8 [I25, I26]): on A; after A is
        bad continuously for the failover hold it steers to B; with fail-back
        ON it returns to A after A is good continuously for the fail-back hold.
        Data-driven, no scenario clock. Tunnels already pinned to a bearer by
        policy-based routing stay on it; the router's selection only decides
        where a REACTIVE re-establishment goes."""
        n = int(round(self.duration_s / SIM_TICK_S)) + 1
        router = np.array(["A"] * n, dtype=object)
        if self.bearerB is None or self.router_mode == "sticky_a":
            return router
        hold = max(1, int(round(P.router_failover_hold_s / SIM_TICK_S)))
        back = max(1, int(round(P.router_failback_hold_s / SIM_TICK_S)))
        cur, bad_run, good_run = "A", 0, 0
        for k in range(n):
            bad = self._a_bad(k * SIM_TICK_S)
            if bad:
                bad_run += 1
                good_run = 0
            else:
                good_run += 1
                bad_run = 0
            if cur == "A" and bad_run >= hold:
                cur = "B"
            elif cur == "B" and P.router_failback and good_run >= back:
                cur = "A"
            router[k] = cur
        return router

    def _router_at(self, t_s):
        if self._router is None:
            return "A"
        k = int(round(t_s / SIM_TICK_S))
        k = min(max(k, 0), len(self._router) - 1)
        return str(self._router[k])

    def env(self, t_s):
        a_sample = self.bearerA.sample(self.a_start + t_s)
        b_sample = (self.bearerB.sample(self.b_offset + t_s)
                    if self.bearerB is not None else None)
        roleB = (RoleState(self.classB, b_sample, self.epB)
                 if self.bearerB is not None else None)
        return EnvTick(roleA=RoleState(self.classA, a_sample, self.epA),
                       roleB=roleB, router_role=self._router_at(t_s))


class HSRInstance(EpisodeInstance):
    """Two real simultaneous subflows (SPEC §6.2 S5). Active = strong carrier
    (U4); the weak carrier (M3) is a permanently reachable candidate."""

    def __init__(self, inst_id, bearer_strong, bearer_weak, events_local,
                 a_bounds, label):
        super().__init__("S5_mptcp_hsr", inst_id, bearer_strong,
                         "cellular-subflow", events_local, a_bounds,
                         b_factory=None, classB="cellular-subflow",
                         label=label, router_mode="sticky_a")
        self.weak = bearer_weak
        self.epA = f"{inst_id}:U4"
        self.epB = f"{inst_id}:M3"

    def prepare(self, seed):
        if self.prepared_seed == seed and self._router is not None:
            return self
        super().prepare(seed)
        self.bearerB = self.weak
        self.b_offset = self.a_start
        self._env = None
        return self

    def env(self, t_s):
        a = self.bearerA.sample(self.a_start + t_s)
        c = self.weak.sample(self.a_start + t_s)
        return EnvTick(roleA=RoleState("cellular-subflow", a, self.epA),
                       roleB=RoleState("cellular-subflow", c, self.epB),
                       router_role="A")


# ---------------------------------------------------------------------------
# bearer / event helpers
# ---------------------------------------------------------------------------
def _read_df(path):
    return pd.read_csv(path, dtype={"net_mode": str, "cell_id": str,
                                    "bearer": str, "ts_utc": str},
                       low_memory=False)


def _bearerA_from_path(path, dataset, tid, cap_ceiling=None):
    b = TraceReplayBearer(path, dataset, tid, cap_ceiling_mbps=cap_ceiling,
                          rtt_seed=_hash_int("rttA", tid) & 0xFFFF)
    evs = degradation_events(b)
    t = b._t
    # the leg starts at the first second the saturating transfer is flowing
    # (SPEC §12.1 [I21]) so an app-start ramp is never read as an outage
    bounds = ((_usable_start(b), float(t[-1])) if len(t) else (0.0, 0.0))
    evs = [e for e in evs if e > bounds[0] + 1.0]
    return b, evs, bounds


def _usable_start(bearer: TraceReplayBearer, min_mbps=APP_START_MIN_MBPS):
    t = bearer._t
    if len(t) == 0:
        return 0.0
    g = float(t[0])
    while g < float(t[-1]):
        s = bearer.sample(g)
        if s.usable and (s.dl_mbps or 0) >= min_mbps:
            return g
        g += 1.0
    return float(t[0])


# ---------------------------------------------------------------------------
# B-leg factories
# ---------------------------------------------------------------------------
def _geo_factory(seed_offset=0):
    def make(rng, seed, dur):
        b = GeoModelBearer(dur + 5.0, seed=seed + seed_offset, params=GeoParams())
        return b, 0.0
    return make


_LEO_CACHE = {}


def _concat_leo_factory(path, tid, seed_offset=0):
    def make(rng, seed, dur):
        key = (path, P.leo_rtt_mode, seed + seed_offset if P.leo_rtt_mode
               != "adjacent" else 0)
        b = _LEO_CACHE.get(key)
        if b is None:
            b = build_leo_concatenated(path, tid, seed=seed + seed_offset)
            _LEO_CACHE[key] = b
        span = max(0.0, b.duration_s - dur - 1.0)
        off = float(rng.uniform(0.0, span)) if span > 0 else 0.0
        return b, off
    return make


_REPLAY_CACHE = {}


def _replay_factory(path, dataset, tid):
    def make(rng, seed, dur):
        b = _REPLAY_CACHE.get(path)
        if b is None:
            b = TraceReplayBearer(path, dataset, tid,
                                  rtt_seed=_hash_int("rttB", tid) & 0xFFFF)
            _REPLAY_CACHE[path] = b
        start = _usable_start(b)
        span = max(0.0, (b.duration_s - start) - dur - 1.0)
        off = start + (float(rng.uniform(0.0, span)) if span > 0 else 0.0)
        return b, off
    return make


# ---------------------------------------------------------------------------
# scenario builders
# ---------------------------------------------------------------------------
def build_scenarios(split: str, traces=None, limit_per=N_PER_SCENARIO):
    """Return {scenario_id: [ScenarioInstance,...]} for the given split."""
    traces = traces or discover_traces()
    out = {sid: [] for sid in SCENARIO_IDS}

    ucc5g = traces.get("ucc5g", {})
    ucc4g = traces.get("ucc4g", {})
    starlink = traces.get("starlink_road", {})
    meireles = traces.get("meireles_wifi", {})
    hsr = traces.get("hsr_mptcp", {})
    lumos = traces.get("lumos5g", {})

    a_list = _ucc5g_traces(split)
    leo_ids = sorted(t for t in starlink if _split_ok(t, split)
                     and _starlink_long_enough(starlink[t]))
    # B-leg mobility must match the scenario (SPEC §13.8): a dismounted
    # responder carries a pedestrian LTE leg; a vehicle a car LTE leg.
    ped_ids = sorted(t for t in ucc4g if t.startswith("pedestrian_")
                     and _split_ok(t, split))
    car_ids = sorted(t for t in ucc4g if t.startswith("car_")
                     and _split_ok(t, split))

    # --- S1: UCC 5G single leg, anchored on a real persistent degradation ---
    m = _load_manifest("ucc5g")
    switch_rows = [r for r in m.get("traces", [])
                   if r.get("switch_5g_lte", 0) > 0 and _ucc5g_capacity_row(r)
                   and _split_ok(r["trace_id"], split)]
    switch_rows.sort(key=lambda r: r["trace_id"])
    for r in switch_rows:
        if len(out["S1_lte5g_walk"]) >= limit_per:
            break
        tid = r["trace_id"]
        path = ucc5g.get(tid)
        if not path:
            continue
        bA, evs, bounds = _bearerA_from_path(path, "ucc5g", tid)
        if not evs:
            continue
        out["S1_lte5g_walk"].append(EpisodeInstance(
            "S1_lte5g_walk", f"S1:{tid}", bA, "5G", evs, bounds, None, None,
            f"UCC5G {tid} (persistent degradation)", router_mode="sticky_a"))

    # --- S2: UCC5G(5G) + CONCATENATED Starlink LEO --------------------------
    i = 0
    for (tid, _r) in a_list:
        if len(out["S2_5g_leo_drive"]) >= limit_per or not leo_ids:
            break
        bA, evs, bounds = _bearerA_from_path(ucc5g[tid], "ucc5g", tid)
        if not evs:
            continue
        leo_id = leo_ids[i % len(leo_ids)]
        i += 1
        out["S2_5g_leo_drive"].append(EpisodeInstance(
            "S2_5g_leo_drive", f"S2:{tid}", bA, "5G", evs, bounds,
            _concat_leo_factory(starlink[leo_id], leo_id, seed_offset=100),
            "LEO",
            f"stitched (independently measured, time-aligned): UCC5G {tid} -> LEO {leo_id} (concatenated)"))

    # --- S3: UCC5G(5G) + GEO model ------------------------------------------
    for (tid, _r) in a_list:
        if len(out["S3_5g_geo_drive"]) >= limit_per:
            break
        bA, evs, bounds = _bearerA_from_path(ucc5g[tid], "ucc5g", tid)
        if not evs:
            continue
        out["S3_5g_geo_drive"].append(EpisodeInstance(
            "S3_5g_geo_drive", f"S3:{tid}", bA, "5G", evs, bounds,
            _geo_factory(seed_offset=7000), "GEO",
            f"UCC5G {tid} -> GEO (measurement-parameterised model)"))

    # --- S4: Wi-Fi coverage cliff + UCC 4G LTE (car) ------------------------
    wifi_ids = sorted(t for t in meireles if _split_ok(t, split)
                      and t.endswith(("_ac", "_n")))
    wi = 0
    for tid in wifi_ids:
        if len(out["S4_wifi_edge"]) >= limit_per or not car_ids:
            break
        bA, evs, bounds = _bearerA_from_path(meireles[tid], "meireles_wifi", tid)
        if not evs:
            continue
        cell_id = car_ids[wi % len(car_ids)]
        wi += 1
        out["S4_wifi_edge"].append(EpisodeInstance(
            "S4_wifi_edge", f"S4:{tid}", bA, "WiFi", evs, bounds,
            _replay_factory(ucc4g[cell_id], "ucc4g", cell_id),
            "cellular-subflow",
            f"stitched (independently measured, time-aligned): Meireles {tid} cliff -> UCC4G {cell_id}"))

    # --- S5: HSR two simultaneous subflows ----------------------------------
    groups = _hsr_groups(hsr, split)
    for gid, (strong, weak) in list(groups.items()):
        if len(out["S5_mptcp_hsr"]) >= limit_per:
            break
        bS = TraceReplayBearer(hsr[strong], "hsr_mptcp", strong,
                               rtt_seed=_hash_int("rttA", strong) & 0xFFFF)
        bW = TraceReplayBearer(hsr[weak], "hsr_mptcp", weak,
                               rtt_seed=_hash_int("rttB", weak) & 0xFFFF)
        evs = degradation_events(bS)
        t = bS._t
        bounds = (float(t[0]), float(min(bS.duration_s, bW.duration_s)))
        if not evs:
            continue
        out["S5_mptcp_hsr"].append(HSRInstance(f"S5:{gid}", bS, bW, evs,
                                               bounds, f"HSR {gid} U4+M3"))

    # --- S6: collective 5G -> GEO -------------------------------------------
    for (tid, _r) in a_list:
        if len(out["S6_collective_5g_geo"]) >= limit_per:
            break
        bA, evs, bounds = _bearerA_from_path(ucc5g[tid], "ucc5g", tid)
        if not evs:
            continue
        out["S6_collective_5g_geo"].append(EpisodeInstance(
            "S6_collective_5g_geo", f"S6:{tid}", bA, "5G", evs, bounds,
            _geo_factory(seed_offset=9000), "GEO",
            f"UCC5G {tid} -> GEO (measurement-parameterised model), collective"))

    # --- S7: Lumos5G walking mmWave (X9000R cap) + UCC 4G LTE pedestrian -----
    i = 0
    for tid in _lumos_ids(lumos, split, "walking"):
        if len(out["S7_lumos_walk"]) >= limit_per or not ped_ids:
            break
        bA, evs, bounds = _bearerA_from_path(lumos[tid], "lumos5g", tid,
                                             cap_ceiling=CAP_X9000R_MBPS)
        if not evs:
            continue
        cell_id = ped_ids[i % len(ped_ids)]
        i += 1
        out["S7_lumos_walk"].append(EpisodeInstance(
            "S7_lumos_walk", f"S7:{tid}", bA, "5G", evs, bounds,
            _replay_factory(ucc4g[cell_id], "ucc4g", cell_id),
            "cellular-subflow",
            f"stitched (independently measured, time-aligned): Lumos5G {tid} (<=100 Mbps X9000R) -> UCC4G {cell_id}"))

    # --- S8: Lumos5G driving mmWave (9001-R cap) + CONCATENATED Starlink -----
    i = 0
    for tid in _lumos_ids(lumos, split, "driving"):
        if len(out["S8_lumos_drive"]) >= limit_per or not leo_ids:
            break
        bA, evs, bounds = _bearerA_from_path(lumos[tid], "lumos5g", tid,
                                             cap_ceiling=CAP_9001R_MBPS)
        if not evs:
            continue
        leo_id = leo_ids[i % len(leo_ids)]
        i += 1
        out["S8_lumos_drive"].append(EpisodeInstance(
            "S8_lumos_drive", f"S8:{tid}", bA, "5G", evs, bounds,
            _concat_leo_factory(starlink[leo_id], leo_id, seed_offset=200),
            "LEO",
            f"stitched (independently measured, time-aligned): Lumos5G {tid} (<=1 Gbps 9001-R) -> LEO {leo_id} (concatenated)"))

    return out


def _lumos_ids(lumos: dict, split: str, mobility: str):
    return [tid for tid in sorted(lumos)
            if f"__{mobility}__" in tid and _split_ok(tid, split)]


def _starlink_long_enough(path):
    try:
        with open(path) as fh:
            n = sum(1 for _ in fh)
        return n > 60
    except OSError:
        return False


def _hsr_groups(hsr: dict, split: str):
    groups = {}
    for tid in hsr:
        parts = tid.split("_")
        if len(parts) < 5:
            continue
        gid = "_".join(parts[:3])
        groups.setdefault(gid, {})[parts[3]] = tid
    out = {}
    for gid, d in sorted(groups.items()):
        if "U4" in d and "M3" in d and _split_ok(gid, split):
            out[gid] = (d["U4"], d["M3"])
    return out
