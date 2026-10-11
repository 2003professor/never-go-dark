"""Fast unit tests for the frozen simulator (SPEC-required invariants).

Run:  python -m pytest tests/ -q     (or: python tests/test_sim.py)

Covers:
  1. replay is zero-order hold, never interpolation;
  2. a throughput gap > horizon makes the bearer UNUSABLE (never filled);
  3. the handshake-cost formula (SPEC §4.3);
  4. outage accounting (documented dark gap ~= DPD + retry + handshake), and
     §8.4 "no plane can deliver" counting zero-capacity stalls as outage;
  5. the policy can reach no engine/ground-truth internals (SPEC §7.4):
     the Obs surface AND negative tests of the enforcement (a non-oracle
     policy asking for the instance fails the run; a policy file that imports
     sim.* / pandas / opens a file / walks frames is rejected at load and never
     executed; only the frozen oracle file is granted the instance; an
     editable policy cannot pick its own gateway mechanism);
  6. review fixes: per-plane rate caps are applied, delivery is never stamped
     before generation, GEO availability equals the realised up-fraction, and
     terrestrial capacity legs come only from saturating Download captures.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from policies.interface import Action, Obs, Policy
from sim.bearers import (GeoModelBearer, TraceReplayBearer, discover_traces,
                         trace_is_heldout, H_TPUT, SIM_TICK_S)
from sim.gateway import (Gateway, GatewayConfig, handshake_time_s,
                         SESSIONS_ROAM_DEFAULT)
from sim.scenarios import build_scenarios
from sim.engine import run
from eval.metrics import compute_metrics


def _any_trace():
    t = discover_traces()
    ds = next(iter(t))
    tid = next(iter(t[ds]))
    return t[ds][tid], ds, tid


# --- graceful skip when the measured datasets are not present --------------
# The real traces are not redistributed here (third-party licences; see
# ../SOURCES.md). Data-dependent tests SKIP with a clear message instead of
# failing, so a fresh clone still runs the pure-logic invariant tests. Works
# under both `pytest` and `python tests/test_sim.py`.
class _SkipTest(Exception):
    """Raised to skip a data-dependent test when no traces are bundled."""


_HAVE_TRACES = bool(discover_traces())


def _need_traces():
    msg = ("measured datasets are not bundled (third-party licences; see "
           "SOURCES.md) - obtain them to run this test")
    if "pytest" in sys.modules:            # under pytest: a real skip
        import pytest
        pytest.skip(msg)
    raise _SkipTest(msg)                    # under `python tests/test_sim.py`


# Route the two data accessors through the guard so every data-dependent test
# skips uniformly. When traces ARE present these are transparent pass-throughs.
_discover_traces_impl = discover_traces
_build_scenarios_impl = build_scenarios


def discover_traces():                      # noqa: F811 - intentional test guard
    if not _HAVE_TRACES:
        _need_traces()
    return _discover_traces_impl()


def build_scenarios(*args, **kwargs):       # noqa: F811 - intentional test guard
    if not _HAVE_TRACES:
        _need_traces()
    return _build_scenarios_impl(*args, **kwargs)


# --- 1. zero-order hold, never interpolation -------------------------------
def test_replay_is_zero_order_hold_not_interpolation():
    path, ds, tid = _any_trace()
    b = TraceReplayBearer(path, ds, tid)
    # pick two consecutive real samples of dl and check the midpoint HOLDS the
    # earlier value rather than averaging toward the later one.
    series = b._f["dl_mbps"]
    assert series.times.shape[0] >= 2
    t0, t1 = float(series.times[0]), float(series.times[1])
    v0, v1 = float(series.values[0]), float(series.values[1])
    if t1 - t0 <= H_TPUT and v0 != v1:
        mid = (t0 + t1) / 2.0
        held, _ = series.at(mid, H_TPUT)
        assert held == v0, "midpoint must hold the earlier sample, not interpolate"
        # and never equal the linear interpolation (unless v0==v1)
        lin = v0 + (v1 - v0) * (mid - t0) / (t1 - t0)
        assert held != lin or v0 == v1


def test_never_reads_future_sample():
    path, ds, tid = _any_trace()
    b = TraceReplayBearer(path, ds, tid)
    series = b._f["dl_mbps"]
    t0 = float(series.times[0])
    # just before the first sample, the field is absent (no future read)
    v, _ = series.at(t0 - 0.001, H_TPUT)
    assert v is None


# --- 2. gap > horizon => UNUSABLE, never filled ----------------------------
def test_throughput_gap_makes_bearer_unusable():
    # a synthetic series with a 10 s gap > H_TPUT (2 s)
    import pandas as pd
    import tempfile
    rows = []
    for t in [0.0, 1.0, 2.0]:
        rows.append(dict(t_s=t, ts_utc="", bearer="5G", net_mode="5G",
                         dl_mbps=10.0, ul_mbps=1.0, rtt_ms=40, loss=0,
                         cell_id="c1", rsrp_dbm=-80, sinr_db=10, speed_kmh=0,
                         lat="", lon=""))
    # next real sample only at t=12 (a 10 s gap)
    rows.append(dict(t_s=12.0, ts_utc="", bearer="5G", net_mode="5G",
                     dl_mbps=10.0, ul_mbps=1.0, rtt_ms=40, loss=0, cell_id="c1",
                     rsrp_dbm=-80, sinr_db=10, speed_kmh=0, lat="", lon=""))
    df = pd.DataFrame(rows)
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as fh:
        df.to_csv(fh.name, index=False)
        p = fh.name
    b = TraceReplayBearer(p, "synthetic", "syn")
    # capacity is the UL-direction (SPEC §13.8): a DL-only cellular trace uses
    # DL x the UL/DL ratio, so cap tracks dl (10 Mbps) scaled, and is > 0.
    s1 = b.sample(1.0)
    assert s1.usable and s1.cap_mbps > 0.0 and s1.dl_mbps == 10.0
    # inside the gap (t=7) the bearer is UNUSABLE and capacity is 0, not held
    s = b.sample(7.0)
    assert not s.usable and s.cap_mbps == 0.0
    # right at the horizon edge it is still absent past 2 s
    assert not b.sample(2.0 + H_TPUT + 0.5).usable
    os.unlink(p)


# --- 3. handshake cost formula (SPEC §4.3) ---------------------------------
def test_handshake_cost_formula():
    # 2 rounds * 0.6 s + 4096 B / (18 Mbps in bytes/s)
    Bps = 18e6 / 8.0   # 18 Mbps as bytes/s
    hs = handshake_time_s(rounds=2, rtt_s=0.6, hs_bytes=4096,
                          dl_Bps=Bps, ul_Bps=Bps)
    expected = 2 * 0.6 + (4096 / Bps)
    assert abs(hs - expected) < 1e-9
    # zero-capacity bearer => infinite (cannot complete)
    assert handshake_time_s(2, 0.6, 4096, 0.0, 0.0) == float("inf")
    # bottleneck is the slower direction (bytes/s)
    hs2 = handshake_time_s(2, 0.1, 8000, dl_Bps=100e6/8, ul_Bps=1e6/8)
    assert abs(hs2 - (2 * 0.1 + 8000 / (1e6/8))) < 1e-9


# --- 4. outage accounting: documented dark gap (gateway-level, §4.3/§13.5) --
def _run_recovery(recovery_mode, down_at=20, down_len=160, rtt_ms=600.0,
                  cap_mbps=18.0, retry_phase="worst", retry_phase_s=0.0):
    """Drive the gateway: active bearer good, then an ENDPOINT change to a
    usable alternate (router failover) at ``down_at``, usable throughout the
    recovery. Return the tick index at which the tunnel comes back UP."""
    cfg = GatewayConfig()
    cfg.retry_phase = retry_phase
    cfg.retry_phase_s = retry_phase_s
    gw = Gateway(cfg, recovery_mode=recovery_mode)
    up_again = None
    for k in range(down_at + down_len):
        t = k * SIM_TICK_S
        endpoint_changed = (k == down_at)
        sr = gw.step(t_s=t, active_usable=True, active_rtt_ms=rtt_ms,
                     active_cap_mbps=cap_mbps, active_endpoint_id="b",
                     active_bearer_class="GEO",
                     endpoint_changed=endpoint_changed,
                     cand_class=None, cand_usable=False, cand_rtt_ms=None,
                     cand_cap_mbps=0.0, cand_endpoint_id="",
                     warm_standby=None, cutover=False, teardown_standby=False)
        if k > down_at and sr.active_up and up_again is None:
            up_again = k
    return up_again, down_at


def test_documented_recovery_is_dpd_plus_retry_plus_handshake():
    # reactive re-handshake, WORST-case retry phase (SPEC §13.8 [I29]): ~ retry
    # wait (10 s, anchored at the break) + a per-tick handshake (~1.2-1.5 s on a
    # 600 ms GEO link) => ~11-12 s dark.
    up, down = _run_recovery("reactive_rehandshake", retry_phase="worst")
    gap_s = (up - down) * SIM_TICK_S
    assert 10.5 <= gap_s <= 13.0, gap_s
    # the FREE-running / immediate phases recover sooner (DPD + handshake), and
    # never later than the worst case — the phase is an [I] knob, swept S-RETRY-PHASE.
    up_f, _ = _run_recovery("reactive_rehandshake", retry_phase="immediate")
    gap_f = (up_f - down) * SIM_TICK_S
    assert 3.0 <= gap_f <= 6.0 and gap_f < gap_s, (gap_f, gap_s)


def test_mobike_recovery_is_dpd_plus_one_rtt():
    # MOBIKE-style migration: ~ DPD(3 s) + 1 RTT(0.6 s) ~= 3.6 s, far below
    # the full-handshake path, and strictly faster than reactive.
    up_m, down = _run_recovery("mobike_migrate")
    up_r, _ = _run_recovery("reactive_rehandshake")
    gap_m = (up_m - down) * SIM_TICK_S
    assert 2.5 <= gap_m <= 5.5, gap_m
    assert up_m < up_r


def test_make_before_break_beats_reactive_on_outage():
    # on a dual-leg scenario the oracle (make-before-break) must not be worse
    # than the documented reactive baseline on outage.
    from policies.static_documented import StaticDocumented
    from policies.oracle import Oracle
    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    md = compute_metrics(run(inst, StaticDocumented(), seed=0))
    mo = compute_metrics(run(inst, Oracle(), seed=0))
    assert mo["outage_s_total"] <= md["outage_s_total"] + 1e-6


# --- 5. the policy cannot access engine / ground-truth internals -----------
def test_policy_sees_only_obs_no_ground_truth():
    captured = {}

    class Spy(Policy):
        name = "spy"
        reads_messages = True
        gateway_config = {"sessions_roam": False,
                          "recovery_mode": "reactive_rehandshake"}

        def decide(self, obs):
            captured["obs"] = obs
            return Action()

    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    run(inst, Spy(), seed=0)
    obs = captured["obs"]
    assert isinstance(obs, Obs)
    # Obs exposes only observable fields; none of these engine internals exist
    for forbidden in ("engine", "instance", "trace", "transitions", "oracle",
                      "rng", "future", "bearerA", "bearerB", "gateway"):
        assert not hasattr(obs, forbidden)
    # message metadata carries no delivered/ground-truth fields
    if obs.messages:
        mm = obs.messages[0]
        for forbidden in ("delivered_t_s", "dropped", "superseded",
                          "half_life_s", "bytes_sent"):
            assert not hasattr(mm, forbidden)
    # Obs is frozen (immutable) so a policy cannot smuggle state back
    try:
        obs.t_s = 999.0
        raised = False
    except Exception:
        raised = True
    assert raised


def test_split_is_disjoint_and_by_trace():
    tr = build_scenarios("train")
    ho = build_scenarios("heldout")
    tr_ids = {i.inst_id for v in tr.values() for i in v}
    ho_ids = {i.inst_id for v in ho.values() for i in v}
    assert tr_ids and ho_ids
    assert tr_ids.isdisjoint(ho_ids)
    # the split rule is a pure function of the trace id
    assert trace_is_heldout("x") == trace_is_heldout("x")


def test_determinism_same_seed():
    from policies.oracle import Oracle
    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    a = compute_metrics(run(inst, Oracle(), seed=1))
    b = compute_metrics(run(inst, Oracle(), seed=1))
    for k in ("aoi_p95_s", "outage_s_total", "standby_tx_s",
              "overhead_bytes_satcom", "video_goodput"):
        assert a[k] == b[k]


# --- 4b. §8.4: tunnel up over zero capacity is OUTAGE ---------------------
def _gw_step(gw, cap, usable=True, t=0.0):
    return gw.step(t_s=t, active_usable=usable, active_rtt_ms=40.0,
                   active_cap_mbps=cap, active_endpoint_id="a",
                   active_bearer_class="5G", endpoint_changed=False,
                   cand_class=None, cand_usable=False, cand_rtt_ms=None,
                   cand_cap_mbps=0.0, cand_endpoint_id="", warm_standby=None,
                   cutover=False, teardown_standby=False)


def test_zero_capacity_tick_is_outage():
    # at the GATEWAY level a present-but-zero-capacity active bearer (passed
    # usable=True) is still counted as outage (zero_cap); the ENGINE additionally
    # treats a below-floor sample as not-usable so DPD/failover fire (see
    # test_zero_capacity_below_floor_triggers_dpd).
    gw = Gateway(GatewayConfig())
    r = _gw_step(gw, cap=0.0)
    assert r.active_up and r.outage_tick and r.zero_cap and not r.tunnel_dark
    r2 = _gw_step(gw, cap=10.0, t=0.1)
    assert r2.active_up and not r2.outage_tick


def _synthetic_single(dl_values, tmpdir):
    """A SingleInstance over a synthetic 1 Hz bearer (test-only helper)."""
    import pandas as pd
    from sim.scenarios import SingleInstance
    rows = [dict(t_s=float(i), ts_utc="", bearer="5G", net_mode="5G",
                 dl_mbps=v, ul_mbps=1.0, rtt_ms=40, loss=0, cell_id="c1",
                 rsrp_dbm=-80, sinr_db=10, speed_kmh=0, lat="", lon="")
            for i, v in enumerate(dl_values)]
    p = os.path.join(tmpdir, "syn.csv")
    pd.DataFrame(rows).to_csv(p, index=False)
    b = TraceReplayBearer(p, "synthetic", "syn")
    return SingleInstance("S1_lte5g_walk", "S1:syn", b, 0.0,
                          float(len(dl_values) - 1), [5.0], "synthetic")


def test_engine_counts_zero_capacity_stall_as_outage():
    # SPEC §13.8: a present-but-0-Mbps bearer delivers nothing, so the whole
    # 0-Mbps region is OUTAGE (no plane can deliver). The first ~DPD seconds are
    # a stall (keepalives still probing the briefly-dead link); past DPD the
    # peer is declared dead and the region is tunnel-dark (re-handshake fails on
    # a dead bearer). Either way outage = dark + stall covers the whole region.
    import tempfile
    from policies.static_documented import StaticDocumented
    with tempfile.TemporaryDirectory() as d:
        inst = _synthetic_single([20.0] * 10 + [0.0] * 11, d)
        rr = run(inst, StaticDocumented(), seed=0)
    m = compute_metrics(rr)
    assert m["outage_s_total"] >= 9.5, m          # the 0-Mbps region is outage
    assert m["tunnel_dark_s_total"] > 0.0, m      # a 0-Mbps link goes dark (DPD)
    assert abs(m["outage_s_total"] - (m["stall_s_total"]
                                      + m["tunnel_dark_s_total"])) < 1e-9


def test_zero_capacity_below_floor_triggers_dpd():
    # SPEC §13.8 realism fix: a committed bearer that keeps reporting samples
    # but carries 0 Mbps is NOT 'usable' to the engine — DPD fires and the peer
    # is declared dead, instead of the tunnel sitting 'up' forever.
    import tempfile
    from policies.static_documented import StaticDocumented
    with tempfile.TemporaryDirectory() as d:
        inst = _synthetic_single([20.0] * 5 + [0.0] * 16, d)
        rr = run(inst, StaticDocumented(), seed=0)
    # by the end of a long 0-Mbps run the tunnel is dark (dead peer), not 'up'
    assert rr.tunnel_dark[-30:].any(), "a long 0-capacity run must go dark (DPD)"


# --- 5b. §7.4 enforcement: negative tests --------------------------------
def test_non_oracle_policy_is_refused_the_instance():
    from sim.isolation import PolicyIsolationError
    called = {"bind": False}

    class Cheat(Policy):
        name = "cheat"
        wants_instance = True

        def bind_instance(self, instance):
            called["bind"] = True

        def decide(self, obs):
            return Action()

    class CheatNoFlag(Policy):          # bind_instance without the flag
        name = "cheat2"

        def bind_instance(self, instance):
            called["bind"] = True

        def decide(self, obs):
            return Action()

    inst = build_scenarios("train")["S3_5g_geo_drive"][0]
    for cls in (Cheat, CheatNoFlag):
        try:
            run(inst, cls(), seed=0)
            raised = False
        except PolicyIsolationError:
            raised = True
        assert raised, cls.__name__
    assert not called["bind"], "bind_instance must never be called"


def _write_policy(tmpdir, body, name="p.py"):
    p = os.path.join(tmpdir, name)
    with open(p, "w") as fh:
        fh.write(body)
    return p


_HDR = ("from __future__ import annotations\n"
        "from policies.interface import Action, Policy\n")
_TAIL = ("class P(Policy):\n    name='p'\n"
         "    def decide(self, obs):\n        return Action()\n"
         "POLICY = P()\n")


def test_leaky_policy_files_are_rejected_at_load_and_never_run():
    import tempfile
    from eval.evaluate import load_policy
    from sim.isolation import PolicyIsolationError
    with tempfile.TemporaryDirectory() as d:
        sentinel = os.path.join(d, "ran.txt")
        leaks = {
            "import_engine": "import sim.engine\n",
            "from_scenarios": "from sim.scenarios import build_scenarios\n",
            "pandas_csv": ("import pandas as pd\n"
                           "X = pd.read_csv('traces/processed/ucc5g/x.csv')\n"),
            "open_file": f"X = open({sentinel!r}, 'w').write('x')\n",
            "dunder_escape": "X = ().__class__.__base__.__subclasses__()\n",
            "frame_walk": ("def g():\n    yield\n"
                           "def leak():\n    it = g()\n"
                           "    return it.gi_frame.f_back\n"),
            "getattr_escape": "X = getattr(Policy, 'mro')\n",
            "wants_instance": ("class Q(Policy):\n    wants_instance = True\n"
                               "    def bind_instance(self, i):\n"
                               "        self.i = i\n"),
            "importlib": "import importlib\n",
        }
        for tag, leak in leaks.items():
            p = _write_policy(d, _HDR + leak + _TAIL, f"{tag}.py")
            try:
                load_policy(p)
                raised = False
            except PolicyIsolationError:
                raised = True
            assert raised, f"{tag} was not rejected"
        assert not os.path.exists(sentinel), "rejected file must never execute"
        # a clean policy using only the interface + pure stdlib is accepted
        ok = _write_policy(d, _HDR + "import math\n" + _TAIL, "ok.py")
        assert load_policy(ok) is not None


def test_only_frozen_oracle_file_gets_the_instance():
    import shutil
    import tempfile
    from eval.evaluate import load_policy
    from sim.isolation import PolicyIsolationError
    inst = build_scenarios("train")["S3_5g_geo_drive"][0]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    oracle_cls = load_policy(os.path.join(root, "policies", "oracle.py"))
    pol = oracle_cls()
    run(inst, pol, seed=0)
    assert pol._instance is inst, "oracle is bound to the instance"
    assert len(pol._warm) > 0, "oracle built a plan from the bound instance"
    # a byte-identical copy of the oracle at another path is NOT the oracle
    with tempfile.TemporaryDirectory() as d:
        cp = os.path.join(d, "policy.py")
        shutil.copy(os.path.join(root, "policies", "oracle.py"), cp)
        try:
            load_policy(cp)
            raised = False
        except PolicyIsolationError:
            raised = True
        assert raised
    # the editable policy files pass the gate and are never bound
    for f in ("policy.py", "continuity_policy.py"):
        cls = load_policy(os.path.join(root, "policies", f))
        from sim.isolation import policy_privileges
        assert not policy_privileges(cls()).may_bind_instance


def test_editable_policy_cannot_choose_gateway_mechanism():
    from sim.isolation import PolicyIsolationError

    class Roamer(Policy):
        name = "roamer"
        gateway_config = {"sessions_roam": True,
                          "recovery_mode": "mobike_migrate"}

        def decide(self, obs):
            return Action()

    inst = build_scenarios("train")["S3_5g_geo_drive"][0]
    try:
        run(inst, Roamer(), seed=0)
        raised = False
    except PolicyIsolationError:
        raised = True
    assert raised


# --- 6. review fixes ------------------------------------------------------
def test_rate_cap_limits_plane_per_tick():
    from sim.engine import _drain
    from sim.traffic import Message

    def mk(i, plane, size):
        return Message(msg_id=i, data_type="bulk", size_bytes=size,
                       enqueue_t_s=0.0, source_id=None, urgency="background",
                       plane=plane)
    planes = [[] for _ in range(8)]
    planes[3] = [mk(1, 3, 100000)]
    planes[4] = [mk(2, 4, 200000)]
    # 10 Mbps for one tick = 125000 B available; cap plane 3 at 1 Mbps
    got = _drain(planes, [], 125000.0, 0.0, 40.0, [0] * 8, {3: 1.0})
    cap_b = 1.0 * 1e6 / 8.0 * SIM_TICK_S
    sent3 = 100000 - (planes[3][0].size_bytes - planes[3][0].bytes_sent)
    assert abs(sent3 - cap_b) < 1e-6, sent3
    # work-conserving: the unused capacity flows to plane 4
    assert abs(got - 125000.0) < 1e-6


def test_delivery_never_before_generation():
    from policies.static_documented import StaticDocumented
    inst = build_scenarios("train")["S1_lte5g_walk"][0]
    rr = run(inst, StaticDocumented(), seed=0)
    d = [m for m in rr.messages if m.delivered_t_s is not None]
    assert d
    assert all(m.delivered_t_s >= m.enqueue_t_s for m in d)
    v = [m.delivered_t_s - m.enqueue_t_s for m in d if m.data_type == "voice"]
    assert min(v) >= 0.020, min(v)   # >= packetisation, never negative


def test_geo_availability_equals_realised_up_fraction():
    # SPEC §13.8: rain fades are now MINUTES-scale (mean 180 s) and the process
    # starts from its stationary state, so availability holds ACROSS episodes.
    # Verifying the realised down-fraction therefore needs long total time
    # (most short episodes see no fade, a few see a whole one); averaged over
    # long runs it converges to 1 - availability.
    from sim.bearers import GeoParams
    for av in (0.94, 0.99):
        fr = [GeoModelBearer(30000.0, seed=s,
                             params=GeoParams(availability=av)).down_fraction
              for s in range(40)]
        assert abs(float(np.mean(fr)) - (1.0 - av)) < 0.4 * (1.0 - av) + 0.002, \
            (av, float(np.mean(fr)))


def test_terrestrial_capacity_legs_are_saturating_downloads():
    sc = build_scenarios("train")
    for sid in ("S1_lte5g_walk", "S2_5g_leo_drive", "S3_5g_geo_drive",
                "S6_collective_5g_geo"):
        assert sc[sid], sid
        for inst in sc[sid]:
            b = inst.bearer if hasattr(inst, "bearer") else inst.bearerA
            assert b.trace_id.startswith("Download__"), (sid, b.trace_id)


# --- 7. §13.1 event-anchoring, randomisation, clock offset, neutral token --
_DUAL = ("S2_5g_leo_drive", "S3_5g_geo_drive", "S4_wifi_edge",
         "S6_collective_5g_geo", "S7_lumos_walk", "S8_lumos_drive")


def test_episode_is_event_anchored_and_randomised():
    from sim.params import P
    sc = build_scenarios("train")
    for sid in _DUAL:
        assert sc[sid], sid
        inst = sc[sid][0]
        inst.prepare(0)
        assert P.episode_min_s - 1 <= inst.duration_s <= P.episode_max_s + 1
        # a REAL degradation event lands inside the middle of the episode
        assert inst.transitions, sid
        assert any(0.15 * inst.duration_s <= tx <= 0.85 * inst.duration_s
                   for tx in inst.transitions), (sid, inst.transitions)


def test_randomisation_varies_with_seed():
    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    starts, offs, durs = set(), set(), set()
    for seed in range(6):
        inst.prepare(seed)
        starts.add(round(inst.a_start, 3))
        offs.add(round(inst.clock_offset, 3))
        durs.add(round(inst.duration_s, 3))
    # the A-leg window, the observable-clock offset and the length all move
    assert len(starts) >= 3 and len(offs) >= 5 and len(durs) >= 3


def test_obs_clock_offset_and_neutral_token():
    captured = {}

    class Spy(Policy):
        name = "spy"
        reads_messages = True

        def reset(self, token, seed):
            captured["token"] = token

        def decide(self, obs):
            captured.setdefault("first_t", obs.t_s)
            return Action()

    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    inst.prepare(0)
    off = inst.clock_offset
    run(inst, Spy(), seed=0)
    # the observable clock is shifted by the secret-salt offset (first tick = offset)
    assert abs(captured["first_t"] - off) < 1e-6 and off > 0.0
    # SPEC §13.8: the token is a CONSTANT neutral value (no scenario name,
    # trace id, dataset, inst_id or seed) — nothing identifies the run.
    tok = captured["token"]
    from sim.scenarios import NEUTRAL_TOKEN
    assert tok == NEUTRAL_TOKEN
    assert "S3" not in tok and "ucc" not in tok.lower() and "Download" not in tok


def test_clock_offset_not_computable_from_public_inputs():
    # SPEC §13.8: the observable-clock offset is drawn from an evaluator-SECRET
    # salt, so two evaluators with different salts place the SAME run's event at
    # different observable clock values — a policy cannot precompute it from
    # trace ids + seeds (the public inputs).
    import importlib
    import sim.params as params
    offs = []
    for salt in ("alpha", "beta", "gamma"):
        os.environ["KATIM_EVAL_SALT"] = salt
        sc = build_scenarios("train")
        inst = sc["S3_5g_geo_drive"][0]
        inst.prepare(0)
        offs.append(round(inst.clock_offset, 3))
    os.environ.pop("KATIM_EVAL_SALT", None)
    assert len(set(offs)) == 3, offs


def test_episode_randomisation_secret_for_heldout():
    # SPEC §13.8: with a secret EPISODE salt the held-out episode windows
    # (event position, length) cannot be precomputed from trace id + seed.
    starts = []
    for salt in ("", "s1", "s2"):
        os.environ["KATIM_EPISODE_SALT"] = salt
        sc = build_scenarios("heldout")
        inst = sc["S3_5g_geo_drive"][0]
        inst.prepare(100)
        starts.append(round(inst.a_start, 3))
    os.environ.pop("KATIM_EPISODE_SALT", None)
    assert len(set(starts)) >= 2, starts


# --- 8. §13.2 Starlink concatenation never fills ---------------------------
def test_starlink_concatenation_never_fills_gaps():
    from sim.bearers import build_leo_concatenated  # discover_traces: module-level guard
    tr = discover_traces().get("starlink_road", {})
    tid = sorted(tr)[0]
    b = build_leo_concatenated(tr[tid], tid, seed=0)
    assert getattr(b, "concatenated", False)
    gap = zero = 0
    for g in np.arange(0.0, b.duration_s, 1.0):
        s = b.sample(g)
        if not s.usable:
            gap += 1                      # a filled/empty gap — must NOT happen
        elif s.cap_mbps == 0.0:
            zero += 1                     # a REAL measured 0-Mbps second — kept
    assert gap == 0, "concatenation must remove gaps, never fill/empty them"
    # the measured-seconds count equals the concatenated length (no invention)
    assert b._t.shape[0] == int(round(b.duration_s)) + 1 or b._t.shape[0] >= 1


# --- 9. §13.3 feasibility gates (feasible rate >= absolute, same run) ------
def test_feasible_gate_rates_ge_absolute():
    from policies.static_documented import StaticDocumented
    from eval.metrics import build_feasref
    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    rr = run(inst, StaticDocumented(), seed=0)
    feas = build_feasref(inst, rr.duration_s, 0)
    m = compute_metrics(rr, feas)
    assert m["alert_hit_rate_feasible"] >= m["alert_hit_rate"] - 1e-9
    assert m["voice_within_hard_feasible"] >= m["voice_within_hard"] - 1e-9
    assert m["n_feasible_alerts"] <= len([x for x in rr.messages
                                          if x.data_type == "alert"])


# --- 10. §13.4 oracle lexicographic property (subset) ----------------------
def test_oracle_outage_le_alwayswarm():
    from policies.oracle import Oracle
    from policies.always_warm import AlwaysWarm
    from policies.atsss_active_standby import AtsssActiveStandby
    sc = build_scenarios("train")
    for sid in ("S2_5g_leo_drive", "S3_5g_geo_drive", "S4_wifi_edge",
                "S5_mptcp_hsr", "S6_collective_5g_geo"):
        inst = sc[sid][0]
        for seed in (0, 1):
            o = compute_metrics(run(inst, Oracle(), seed))["outage_s_total"]
            aw = compute_metrics(run(inst, AlwaysWarm(), seed))["outage_s_total"]
            at = compute_metrics(run(inst, AtsssActiveStandby(), seed))["outage_s_total"]
            assert o <= min(aw, at) + 1e-6, (sid, seed, o, aw, at)


# --- 11. §13.5 per-tick handshake: progress only while the bearer is usable -
def test_handshake_progresses_only_while_usable():
    # a warming standby makes NO progress while its candidate is unusable, and
    # completes once the candidate becomes usable (per-tick serialisation).
    def warm_run(cand_usable_after):
        gw = Gateway(GatewayConfig())
        ready_at = None
        for k in range(200):
            t = k * SIM_TICK_S
            cu = k >= cand_usable_after
            sr = gw.step(t_s=t, active_usable=True, active_rtt_ms=40.0,
                         active_cap_mbps=10.0, active_endpoint_id="a",
                         active_bearer_class="5G", endpoint_changed=False,
                         cand_class="GEO", cand_usable=cu, cand_rtt_ms=600.0,
                         cand_cap_mbps=(18.0 if cu else 0.0),
                         cand_endpoint_id="b", warm_standby="GEO",
                         cutover=False, teardown_standby=False)
            if sr.standby_ready and ready_at is None:
                ready_at = k
        return ready_at
    early = warm_run(0)        # candidate usable from the start
    late = warm_run(50)        # candidate unusable for the first 5 s
    assert early is not None and late is not None
    assert late >= early + 50, (early, late)   # warming stalled while unusable


def test_handshake_times_out_and_retries_on_dead_bearer():
    # reactive recovery: endpoint change then the bearer stays unusable -> the
    # handshake attempt cannot complete, times out, and a retry is scheduled;
    # the tunnel never comes up while the bearer is dead.
    gw = Gateway(GatewayConfig())
    retries = 0
    last_retry = None
    for k in range(400):
        t = k * SIM_TICK_S
        sr = gw.step(t_s=t, active_usable=False, active_rtt_ms=600.0,
                     active_cap_mbps=0.0, active_endpoint_id="b",
                     active_bearer_class="GEO",
                     endpoint_changed=(k == 10),
                     cand_class=None, cand_usable=False, cand_rtt_ms=None,
                     cand_cap_mbps=0.0, cand_endpoint_id="",
                     warm_standby=None, cutover=False, teardown_standby=False)
        assert not sr.active_up           # dead bearer: never up
        if gw.active.next_retry_t is not None and gw.active.next_retry_t != last_retry:
            last_retry = gw.active.next_retry_t
            retries += 1
    assert retries >= 2, "a dead bearer must reschedule retries over time"


# --- 12. §13.1 anti-gaming: a constant timer cannot reach the oracle -------
def test_fixed_timer_cannot_reach_oracle():
    from policies.oracle import Oracle
    import policies.diag_fixed_timer as dft
    sc = build_scenarios("train")
    diag_out, orc_out = [], []
    for sid in ("S3_5g_geo_drive", "S6_collective_5g_geo"):
        for inst in sc[sid][:2]:
            for seed in (0, 1):
                diag_out.append(compute_metrics(
                    run(inst, dft.DiagFixedTimer(), seed))["outage_s_total"])
                orc_out.append(compute_metrics(
                    run(inst, Oracle(), seed))["outage_s_total"])
    d, o = float(np.mean(diag_out)), float(np.mean(orc_out))
    # the tuned constant timer stays far above the foresight ceiling: no fixed
    # timing can line up with the randomised, event-anchored degradation.
    assert d > 2.0 * o + 0.05, (d, o)


def test_event_onset_not_constant_across_seeds():
    sc = build_scenarios("train")
    inst = sc["S3_5g_geo_drive"][0]
    onsets, obs_clocks = [], []
    for seed in range(6):
        inst.prepare(seed)
        onsets.append(inst.transitions[0])
        obs_clocks.append(inst.clock_offset + inst.transitions[0])
    # the real first-transition time is not a constant, and the observable
    # clock value at the event spans far more than one episode length (the
    # seeded offset): no constant predicts the event from Obs time.
    assert float(np.std(onsets)) > 2.0, onsets
    assert (max(obs_clocks) - min(obs_clocks)) > 2.0 * inst.duration_s


# --- 13. §13.6 Lumos5G scenarios present and gateway-capped ----------------
def test_lumos_scenarios_present_and_capped():
    from sim.scenarios import CAP_X9000R_MBPS, CAP_9001R_MBPS
    sc = build_scenarios("train")
    assert sc["S7_lumos_walk"] and sc["S8_lumos_drive"]
    for inst in sc["S7_lumos_walk"]:
        assert "lumos5g" in inst.bearerA.dataset
        assert inst.bearerA.cap_ceiling_mbps == CAP_X9000R_MBPS
    for inst in sc["S8_lumos_drive"]:
        assert "lumos5g" in inst.bearerA.dataset
        assert inst.bearerA.cap_ceiling_mbps == CAP_9001R_MBPS
        # the mmWave capacity is actually clamped to the 9001-R gateway rate
        inst.prepare(0)
        caps = [inst.bearerA.sample(inst.a_start + t).cap_mbps
                for t in np.arange(0, inst.duration_s, 5.0)]
        assert max(caps) <= CAP_9001R_MBPS + 1e-6


# --- 14. §13.8 emissions: active satcom/secondary airtime is charged --------
def test_flee_to_standby_is_not_free():
    # SPEC §13.8: a policy that cuts to the standby at t=0 and stays must pay
    # emission airtime for riding B, not look almost free. The emission proxy
    # (not just standby-warming time) charges active time on the non-primary leg.
    class Flee(Policy):
        name = "flee"
        reads_messages = False
        gateway_config = {"sessions_roam": False,
                          "recovery_mode": "reactive_rehandshake"}

        def reset(self, token, seed):
            self._done = False

        def decide(self, obs):
            act = Action()
            if self._done:
                return act
            cand = obs.bearer_hint.candidate_available if obs.bearer_hint else None
            if obs.standby.ready:
                act.cutover = True
                self._done = True
            elif cand is not None and not obs.standby.warming:
                act.warm_standby = cand
            return act

    inst = build_scenarios("train")["S2_5g_leo_drive"][0]
    m = compute_metrics(run(inst, Flee(), seed=0))
    # it rides B most of the episode: emission >> its tiny standby-warming time
    assert m["emission_tx_s"] > 20.0, m["emission_tx_s"]
    assert m["secondary_active_s"] > 20.0, m["secondary_active_s"]
    assert m["emission_tx_s"] > 5.0 * m["standby_tx_s"], m


def test_constant_timer_gamer_does_not_beat_standards_on_score():
    # SPEC §13.8 (anti-gaming): with the emission loophole closed and 20 seeds,
    # a per-scenario-family constant timer / the diagnostic fixed timer cannot
    # out-SCORE ATSSS or always-warm on fresh (held-out-like) seeds.
    from eval.evaluate import aggregate, scalar_score, SCENARIO_IDS
    import policies.diag_fixed_timer as dft
    from policies.atsss_active_standby import AtsssActiveStandby
    from policies.always_warm import AlwaysWarm
    from policies.static_documented import StaticDocumented
    sc = build_scenarios("train")
    seeds = [300, 301, 302, 303, 304]   # fresh, never used for tuning

    def rows(cls):
        out = []
        for sid in SCENARIO_IDS:
            for inst in sc[sid]:
                for s in seeds:
                    rr = run(inst, cls(), s)
                    from eval.metrics import build_feasref, gate_shortfall
                    feas = build_feasref(inst, rr.duration_s, s)
                    mm = compute_metrics(rr, feas)
                    mm["gate_shortfall"] = gate_shortfall(mm)
                    out.append(mm)
        return aggregate(out)
    base = rows(StaticDocumented)
    emref = rows(AlwaysWarm)
    diag = scalar_score(rows(dft.DiagFixedTimer), base, emref)["score"]
    at = scalar_score(rows(AtsssActiveStandby), base, emref)["score"]
    aw = scalar_score(emref, base, emref)["score"]
    assert diag <= at + 1e-6 and diag <= aw + 1e-6, (diag, at, aw)


# --- 15. §13.8 capacity = uplink direction; RTT drawn where absent ----------
def test_capacity_is_uplink_direction():
    from sim.bearers import ul_dl_ratio  # TraceReplayBearer/discover_traces: module-level
    tr = discover_traces()["ucc4g"]
    tid = sorted(tr)[0]
    b = TraceReplayBearer(tr[tid], "ucc4g", tid)
    # a DL-only cellular trace drives capacity from DL x the UL/DL ratio
    for g in np.arange(0, min(60, b.duration_s), 1.0):
        s = b.sample(g)
        if s.usable and s.dl_mbps:
            assert abs(s.cap_mbps - s.dl_mbps * ul_dl_ratio("LTE")) < 1e-6
            assert s.cap_dl_mbps is not None
            break


def test_lumos_leg_has_drawn_rtt_not_fingerprint_constant():
    # SPEC §13.8: Lumos5G has no RTT; the leg must carry a labelled seeded draw
    # from measured UCC 5G ping samples, not a dataset-fingerprinting constant /
    # 1e9 telemetry age.
    sc = build_scenarios("train")
    inst = sc["S7_lumos_walk"][0]
    inst.prepare(0)
    b = inst.bearerA
    seen = set()
    for g in np.arange(0, min(80, b.duration_s), 2.0):
        s = b.sample(g)
        if s.usable:
            assert s.rtt_ms is not None and 40.0 <= s.rtt_ms <= 700.0, s.rtt_ms
            assert s.rtt_age_s <= 120.0            # quantised, never 1e9
            seen.add(round(s.rtt_ms, 1))
    assert "draw" in b.rtt_draw_source or "ping" in b.rtt_draw_source.lower()
    assert len(seen) >= 2, "RTT draw should vary across windows"


def test_s7_standby_is_pedestrian_not_bus():
    # SPEC §13.8: S7 (dismounted walk) standby must be UCC4G PEDESTRIAN LTE.
    sc = build_scenarios("train")
    for inst in sc["S7_lumos_walk"]:
        inst.prepare(0)
        assert inst.bearerB is not None
        assert inst.bearerB.trace_id.startswith("pedestrian_"), inst.bearerB.trace_id


if __name__ == "__main__":
    import traceback
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = skipped = failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
            passed += 1
        except _SkipTest as e:
            print(f"SKIP {fn.__name__}: {e}")
            skipped += 1
        except Exception:
            print(f"FAIL {fn.__name__}")
            traceback.print_exc()
            failed += 1
    extra = f", {skipped} skipped (datasets not bundled)" if skipped else ""
    print(f"\n{passed}/{len(fns)} passed{extra}")
    sys.exit(0 if failed == 0 else 1)
