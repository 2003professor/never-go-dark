"""Simulation engine — SPEC §2 time model, drives everything (frozen).

Fixed 100 ms tick. Each tick:
  1. enqueue newly-generated red-side messages (mark PLI supersession);
  2. build the §7 Obs (observable inputs ONLY) and call policy.decide;
  3. apply the Action (remap / drop / rate-cap / warm / cutover / timers);
  4. step the gateway state machine (outage, handshake, standby, emissions);
  5. if the active tunnel is up, drain planes in priority order within the
     bearer's capacity (minus on-wire overhead, minus realised loss), each
     plane additionally bounded by the policy's per-plane rate cap (§7.3);
  6. log per-tick state for the frozen metrics. A tick counts as OUTAGE
     (§8.4 "no plane can deliver") when the tunnel is dark OR the deliverable
     goodput after overhead and loss is below STALL_GOODPUT_MBPS.

The policy receives a frozen Obs and nothing else (SPEC §7.4): no engine
handle, trace array, transition schedule, future sample, oracle or RNG. The
scenario instance is bound ONLY to the frozen oracle file, identified by its
verified source path (sim/isolation.py), never by a settable attribute; any
other policy that asks for it makes the run fail.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from policies.interface import (Action, BearerClass, BearerHint, LinkTelemetry,
                                 ModemStatus, MsgMeta, Obs, PlaneState, N_PLANES)
from sim.gateway import Gateway, GatewayConfig, SIM_TICK_S
from sim.isolation import PolicyIsolationError, policy_privileges
from sim.params import P
from sim.bearers import default_rtt_ms
from sim.scenarios import NEUTRAL_TOKEN, link_ok
from sim.traffic import TrafficConfig, build_traffic

_SATCOM = {"GEO", "LEO"}
PROC_DELAY_S = 0.005
# SPEC §8.4 / §12.2 [I23]: a tick is OUTAGE when the tunnel is dark OR the
# deliverable goodput this tick (capacity minus on-wire overhead, minus loss)
# is below this floor (2.4 kbps = MELPe, the lowest-rate real-time flow).
# Read from the named knob so S-STALL can sweep it.
STALL_GOODPUT_MBPS = P.stall_goodput_mbps
# telemetry age is quantised (SPEC §13.8): a raw 1e9 "never measured" would
# fingerprint a dataset; the gateway only ever knows age up to this ceiling.
TELEMETRY_AGE_CEIL_S = 120.0


@dataclass
class RunResult:
    inst_id: str
    scenario: str
    seed: int
    policy: str
    duration_s: float
    transitions: list
    messages: list          # all Message objects (with delivery state)
    fluids: list            # FluidSource objects
    # per-tick logs (numpy arrays)
    t_grid: np.ndarray
    outage: np.ndarray      # bool per tick: SPEC §8.4 (dark OR stall)
    active_up: np.ndarray   # bool per tick
    standby_tx: np.ndarray  # bool per tick
    active_class: list
    standby_ready: np.ndarray
    standby_warming: np.ndarray
    overhead_bytes_total: int
    overhead_bytes_satcom: int
    standby_tx_s: float
    invalid_reason: Optional[str]
    tunnel_dark: np.ndarray = None   # subset of outage: tunnel down/unusable
    stall: np.ndarray = None         # subset of outage: up, ~0 deliverable
    # SPEC §13.8 emissions: seconds a tunnel transmits on a NON-PRIMARY bearer
    # (a standby warming/ready on B, OR the active tunnel riding the B leg /
    # satcom). This is the metered/EMCON-visible airtime; it makes "flee to the
    # standby at t=0 and stay" cost its real emissions, closing the loophole
    # where only standby-warming time was charged.
    emission_tx_s: float = 0.0
    secondary_active_s: float = 0.0  # subset: active tunnel on the B leg
    committed_b: np.ndarray = None   # bool per tick: active committed to B


def _msgmeta(m, clock_offset=0.0):
    # The policy sees the whole observable clock shifted by the episode's
    # seeded offset (SPEC §13.1): enqueue/deadline are shifted with obs.t_s so
    # the policy's view is internally consistent (durations/head-ages are
    # invariant) while a policy that keys on ABSOLUTE time cannot locate the
    # transition. Metrics use the TRUE (unshifted) engine times.
    return MsgMeta(msg_id=m.msg_id, data_type=m.data_type,
                   size_bytes=m.size_bytes,
                   enqueue_t_s=m.enqueue_t_s + clock_offset,
                   urgency=m.urgency,
                   deadline_s=(m.deadline_t_s + clock_offset)
                   if m.deadline_t_s is not None else None,
                   supersedes_id=m.supersedes_id, source_id=m.source_id)


def run(instance, policy, seed: int, traffic_cfg: TrafficConfig = None,
        gateway_cfg: GatewayConfig = None) -> RunResult:
    """Execute one (instance, policy, seed) run deterministically."""
    # SPEC §7.4 enforcement (outside the editable files): resolve what this
    # policy is allowed BEFORE touching the instance. Raises
    # PolicyIsolationError (the run fails) if a non-oracle policy asks for the
    # instance or a non-frozen policy tries to pick its own gateway mechanism.
    priv = policy_privileges(policy)
    instance.prepare(seed)
    traffic_cfg = traffic_cfg or TrafficConfig()
    duration = instance.duration_s
    msgs, fluids = build_traffic(duration, seed, traffic_cfg)

    cfg = gateway_cfg or GatewayConfig()
    # mechanism configuration from the policy (SPEC §4.4/§11), set once.
    # Only the frozen baseline files may choose a non-default mechanism; an
    # editable policy always runs on the pessimistic default (checked above).
    gconf = priv.gateway_config
    cfg.sessions_roam = bool(gconf.get("sessions_roam", cfg.sessions_roam))
    recovery_mode = gconf.get("recovery_mode", "reactive_rehandshake")
    # per-run realism knobs (SPEC §13.8): the documented-retry phase (seeded
    # per episode) and the attempt timeout, from the named sweep register.
    cfg.retry_phase = str(P.retry_phase)
    cfg.retry_phase_s = float(getattr(instance, "retry_phase_s", 0.0))
    cfg.hs_attempt_timeout_s = float(P.hs_attempt_timeout_s)
    gw = Gateway(cfg, recovery_mode=recovery_mode)
    # SPEC §13.1/§13.8: a non-privileged policy is given a CONSTANT neutral
    # token and NO run-identifying value (no scenario name / trace id / seed),
    # so it cannot key on run identity — only on observable link signals. The
    # privileged oracle alone receives the real seed (it needs it to evaluate
    # candidate plans through the engine under the same seeded traffic).
    clock_offset = float(getattr(instance, "clock_offset", 0.0))
    reset_seed = seed if priv.may_bind_instance else 0
    policy.reset(NEUTRAL_TOKEN, reset_seed)
    # The perfect-foresight oracle (SPEC §9.3) is the explicitly privileged
    # ceiling: it alone may see the transition schedule. It is identified by
    # its verified frozen source file, not by a settable class attribute.
    if priv.may_bind_instance:
        policy.bind_instance(instance)
    reads_messages = getattr(policy, "reads_messages", False)

    n_ticks = int(round(duration / SIM_TICK_S))
    t_grid = np.arange(n_ticks) * SIM_TICK_S

    # plane queues (FIFO lists of Message), per-plane drop counters
    planes = [[] for _ in range(N_PLANES)]
    plane_drops = [0] * N_PLANES
    by_id = {m.msg_id: m for m in msgs}
    last_pli_by_src = {}

    # telemetry estimators (gateway's own, SPEC §7.1). Updated only on ticks
    # where a keepalive echo returns (the tunnel is up and delivering): a dark
    # tunnel freezes them (no echo) and ``last_ka_t`` ages the telemetry.
    tput_ewma = None
    rtt_ewma = None
    prev_rtt_ewma = None
    loss_ewma = 0.0
    last_ka_t = 0.0
    rtt_trend = 0.0
    alpha = 0.3

    outage = np.zeros(n_ticks, dtype=bool)
    dark_arr = np.zeros(n_ticks, dtype=bool)
    stall_arr = np.zeros(n_ticks, dtype=bool)
    active_up_arr = np.zeros(n_ticks, dtype=bool)
    standby_tx_arr = np.zeros(n_ticks, dtype=bool)
    standby_ready_arr = np.zeros(n_ticks, dtype=bool)
    standby_warming_arr = np.zeros(n_ticks, dtype=bool)
    active_class_log = []
    committed_b_arr = np.zeros(n_ticks, dtype=bool)
    emission_tx_arr = np.zeros(n_ticks, dtype=bool)
    secondary_active_arr = np.zeros(n_ticks, dtype=bool)
    overhead_satcom = 0
    invalid_reason = None
    primary_class = getattr(instance, "classA", None)

    mi = 0  # pointer into sorted msgs for enqueueing
    nmsg = len(msgs)

    # which bearer role the crypto tunnel is committed to (engine-owned)
    committed_role = "A"
    standby_role = None
    failover_active = False
    failover_target = None
    failover_emitted = False

    for k in range(n_ticks):
        t = k * SIM_TICK_S
        t_next = t + SIM_TICK_S

        # 1. enqueue messages generated in [t, t_next)
        while mi < nmsg and msgs[mi].enqueue_t_s < t_next:
            m = msgs[mi]
            mi += 1
            planes[m.plane].append(m)
            if m.data_type == "pli":
                prev = last_pli_by_src.get(m.source_id)
                if prev is not None and prev.delivered_t_s is None \
                        and not prev.dropped:
                    prev.superseded = True
                last_pli_by_src[m.source_id] = m

        env = instance.env(t)
        roles = {"A": env.roleA, "B": env.roleB}

        def role_usable(r):
            # SPEC §13.8: "usable" for routing/commit = the link can actually
            # deliver (deliverable goodput >= the stall floor), not merely that
            # a sample is present. A bearer reporting 0 Mbps (a Lumos mmWave
            # blockage, a drained download) is NOT usable, so a stalled
            # committed leg triggers failover / DPD just like a dark one.
            rs = roles.get(r)
            return rs is not None and link_ok(rs.sample)

        # --- resolve which bearer the crypto tunnel's active path rides -----
        # The engine owns the committed bearer; the policy moves it only via a
        # successful cutover (make-before-break). A committed bearer that is
        # gone/unusable forces a reactive failover to the router's default.
        endpoint_changed = False
        if role_usable(committed_role):
            active_role = committed_role
            failover_active = False
            failover_emitted = False
        else:
            router = env.router_role
            if router != committed_role and role_usable(router):
                active_role = router
                if not failover_active or failover_target != router:
                    failover_active = True
                    failover_target = router
                    failover_emitted = False
                if not failover_emitted:
                    endpoint_changed = True
                    failover_emitted = True
            else:
                active_role = committed_role  # pure outage on committed bearer

        arole = roles.get(active_role)
        a_sample = arole.sample if arole else None
        a_class = arole.bclass if arole else env.roleA.bclass
        a_ep = arole.endpoint if arole else ""
        a_usable = link_ok(a_sample)
        a_cap = a_sample.cap_mbps if (a_sample and a_sample.usable) else 0.0
        a_cap_dl = (a_sample.cap_dl_mbps if (a_sample and a_sample.cap_dl_mbps
                                             is not None) else a_cap)
        a_rtt = (a_sample.rtt_ms if (a_sample and a_sample.rtt_ms is not None)
                 else default_rtt_ms(a_class))
        a_rtt_age = a_sample.rtt_age_s if a_sample else TELEMETRY_AGE_CEIL_S
        a_loss = a_sample.loss if (a_sample and a_sample.loss is not None) else None

        # candidate bearer offered to the policy (a usable role != committed)
        cand_role = None
        for r in (env.router_role, "A", "B"):
            if r != committed_role and role_usable(r):
                cand_role = r
                break
        crole = roles.get(cand_role) if cand_role else None
        c_sample = crole.sample if crole else None
        c_class = crole.bclass if crole else None
        c_ep = crole.endpoint if crole else ""
        c_rtt = ((c_sample.rtt_ms if c_sample.rtt_ms is not None
                  else default_rtt_ms(c_class)) if c_sample else 600.0)
        c_cap = c_sample.cap_mbps if (c_sample and c_sample.usable) else 0.0
        c_cap_dl = (c_sample.cap_dl_mbps if (c_sample and c_sample.cap_dl_mbps
                                             is not None) else c_cap)

        # 2. build Obs (observable inputs only)
        plane_states = []
        for pi in range(N_PLANES):
            q = planes[pi]
            depth_bytes = sum(mm.size_bytes - mm.bytes_sent for mm in q)
            head_age = (t - q[0].enqueue_t_s) if q else 0.0
            plane_states.append(PlaneState(pi, len(q), depth_bytes, head_age,
                                           plane_drops[pi]))
        # SPEC §13.8: link telemetry is what the gateway measures from its OWN
        # delivered keepalives, never the raw trace. rtt_trend is the change in
        # the gateway's keepalive-RTT EWMA (not trace_rtt - ewma, which leaked
        # the exact current trace RTT even while the tunnel was dark). While the
        # tunnel is dark no keepalive returns, so RTT telemetry is FROZEN (the
        # EWMA stops updating) and reported stale via telemetry_age_s.
        link = LinkTelemetry(
            keepalive_rtt_ms=rtt_ewma,
            rtt_trend=rtt_trend,
            loss_est=loss_ewma,
            tput_est_mbps=tput_ewma,
            telemetry_age_s=min(TELEMETRY_AGE_CEIL_S, t - last_ka_t),
            dpd_missed=1 if gw.active.dead_declared else 0,
        )
        msg_list = tuple(_msgmeta(mm, clock_offset) for pi in range(N_PLANES)
                         for mm in planes[pi]) if reads_messages else tuple()
        hint = BearerHint(
            current_bearer_class=_as_class(a_class),
            candidate_available=_as_class(c_class) if c_class else None,
        )
        standby_state = _standby_obs(gw, c_class, t)
        obs = Obs(t_s=t + clock_offset, planes=tuple(plane_states), link=link,
                  messages=msg_list, bearer_hint=hint, standby=standby_state,
                  modem=_modem_status(a_class, a_sample))

        # 3. policy decides
        action = policy.decide(obs)

        # 4. apply action (remap / drop / rate caps / timers)
        if action.timer_override:
            cfg.apply_timer_override(action.timer_override)
        if action.plane_map:
            _apply_plane_map(planes, action.plane_map)
        if action.drop:
            _apply_drop(planes, by_id, plane_drops, action.drop, t)

        # 5. gateway step
        warm = _class_str(action.warm_standby)
        if warm is not None and cand_role is not None:
            standby_role = cand_role
        sr = gw.step(
            t_s=t,
            active_usable=a_usable, active_rtt_ms=a_rtt,
            active_cap_mbps=a_cap, active_endpoint_id=a_ep,
            active_bearer_class=a_class,
            endpoint_changed=endpoint_changed,
            cand_class=c_class, cand_usable=role_usable(cand_role) if cand_role
            else False,
            cand_rtt_ms=c_rtt, cand_cap_mbps=c_cap,
            cand_endpoint_id=c_ep,
            warm_standby=warm, cutover=action.cutover,
            teardown_standby=action.teardown_standby,
            active_cap_dl_mbps=a_cap_dl, cand_cap_dl_mbps=c_cap_dl,
            active_loss=(a_loss or 0.0),
            cand_loss=(c_sample.loss if (c_sample and c_sample.loss is not None)
                       else 0.0),
        )
        if sr.invalid_reason and invalid_reason is None:
            invalid_reason = sr.invalid_reason

        # commit-role bookkeeping
        if sr.cutover_done and standby_role is not None:
            committed_role = standby_role
            standby_role = None
            failover_active = False
            failover_emitted = False
        elif sr.active_up:
            committed_role = active_role
            failover_active = False
            failover_emitted = False

        active_up_arr[k] = sr.active_up
        standby_tx_arr[k] = sr.standby_tx_tick
        standby_ready_arr[k] = sr.standby_ready
        standby_warming_arr[k] = sr.standby_warming
        active_class_log.append(a_class if sr.active_up else "")

        # SPEC §13.8 emissions: charge airtime on the NON-PRIMARY leg whatever
        # its role. The active tunnel riding the B leg (committed_role=='B',
        # e.g. flee-to-standby) is metered airtime, not free; so is a standby
        # warming/ready on B. Primary-leg (A) active airtime is the baseline
        # and costs nothing.
        on_b = (committed_role == "B")
        secondary_active = sr.active_up and on_b
        secondary_active_arr[k] = secondary_active
        committed_b_arr[k] = on_b
        emission_tx_arr[k] = secondary_active or sr.standby_tx_tick

        # overhead satcom split (active overhead on a_class, standby on c_class)
        if a_class in _SATCOM:
            overhead_satcom += (sr.overhead_bytes - sr.standby_bytes)
        if c_class in _SATCOM:
            overhead_satcom += sr.standby_bytes

        # fluid offered load accrues every tick (even during outage), so
        # video/bulk goodput = delivered/offered is not flattered by outages.
        for fs in fluids:
            fs.offered_bytes += fs.offered_mbps * 1e6 / 8.0 * SIM_TICK_S

        # 6. deliver bytes if active tunnel up
        stalled = False
        if sr.active_up:
            cap_bytes = a_cap * 1e6 / 8.0 * SIM_TICK_S
            on_wire_overhead = sr.overhead_bytes - sr.standby_bytes
            avail = max(0.0, cap_bytes - on_wire_overhead)
            loss = a_loss if a_loss is not None else 0.0
            avail *= (1.0 - min(0.99, max(0.0, loss)))
            deliverable_mbps = avail * 8.0 / 1e6 / SIM_TICK_S
            stalled = (avail <= 0.0) or (deliverable_mbps < STALL_GOODPUT_MBPS)
            delivered_goodput = _drain(planes, fluids, avail, t, a_rtt,
                                       plane_drops, action.rate_cap_mbps)
            # telemetry EWMAs from the gateway's OWN keepalive echoes — updated
            # only when a keepalive actually returns (tunnel up AND delivering
            # above the floor this tick). A stalled/dark tick returns no echo,
            # so RTT/tput telemetry freezes and ages (SPEC §13.8).
            gp_mbps = delivered_goodput * 8.0 / 1e6 / SIM_TICK_S
            if not stalled:
                last_ka_t = t
                tput_ewma = gp_mbps if tput_ewma is None else \
                    (1 - alpha) * tput_ewma + alpha * gp_mbps
                prev = rtt_ewma if rtt_ewma is not None else a_rtt
                rtt_ewma = a_rtt if rtt_ewma is None else \
                    (1 - alpha) * rtt_ewma + alpha * a_rtt
                rtt_trend = rtt_ewma - prev
                loss_ewma = (1 - alpha) * loss_ewma + alpha * loss
            else:
                loss_ewma = (1 - alpha) * loss_ewma + alpha * \
                    (loss if loss > 0 else 1.0)
        else:
            loss_ewma = (1 - alpha) * loss_ewma + alpha * 1.0

        # SPEC §8.4: outage = no plane can deliver = tunnel dark OR stall
        dark_arr[k] = sr.tunnel_dark
        stall_arr[k] = (not sr.tunnel_dark) and (stalled or sr.zero_cap)
        outage[k] = dark_arr[k] or stall_arr[k]

    return RunResult(
        inst_id=instance.inst_id, scenario=instance.scenario, seed=seed,
        policy=getattr(policy, "name", "policy"), duration_s=duration,
        transitions=list(instance.transitions), messages=msgs, fluids=fluids,
        t_grid=t_grid, outage=outage, active_up=active_up_arr,
        standby_tx=standby_tx_arr, active_class=active_class_log,
        standby_ready=standby_ready_arr, standby_warming=standby_warming_arr,
        overhead_bytes_total=gw.overhead_total,
        overhead_bytes_satcom=overhead_satcom,
        standby_tx_s=gw.standby_tx_s, invalid_reason=invalid_reason,
        tunnel_dark=dark_arr, stall=stall_arr,
        emission_tx_s=float(emission_tx_arr.sum()) * SIM_TICK_S,
        secondary_active_s=float(secondary_active_arr.sum()) * SIM_TICK_S,
        committed_b=committed_b_arr,
    )


_CELLULAR_CLASSES = {"5G", "LTE", "cellular-subflow"}
SIGNAL_SENTINEL_DBM = -140.0   # G-NetTrack / Lumos floor = "no reading" (data cards)


def _modem_status(a_class, sample):
    """SPEC §14.1: router-exported modem status of the bearer the tunnel rides.
    Built from the same zero-order-held sample (H_POSE horizon); sentinels and
    stale values are reported as None, never invented. Satellite: kind 'none'."""
    cls = _class_str(a_class)
    if sample is None or cls in ("LEO", "GEO", ""):
        return ModemStatus(kind="none", access_mode="", signal_dbm=None, sinr_db=None)
    kind = "wifi" if cls == "WiFi" else ("cellular" if cls in _CELLULAR_CLASSES else "none")
    sig = sample.rsrp_dbm
    if sig is not None and (sig != sig or sig <= SIGNAL_SENTINEL_DBM):
        sig = None
    sinr = sample.sinr_db if (sample.sinr_db is None or sample.sinr_db == sample.sinr_db) else None
    return ModemStatus(kind=kind, access_mode=str(sample.net_mode or ""),
                       signal_dbm=None if sig is None else float(sig),
                       sinr_db=None if sinr is None else float(sinr))


def _class_str(x):
    """Normalise a BearerClass or string to its plain class string ('GEO')."""
    if x is None:
        return None
    return x.value if isinstance(x, BearerClass) else str(x)


def _as_class(name):
    try:
        return BearerClass(name)
    except (ValueError, TypeError):
        return BearerClass.UNKNOWN


def _standby_obs(gw, cand_class, t):
    from policies.interface import StandbyState
    st = gw.standby
    if st is None:
        return StandbyState(candidate=_as_class(cand_class) if cand_class
                            else None, warming=False, ready=False,
                            bytes_spent=0, age_s=0.0)
    return StandbyState(
        candidate=_as_class(st.bearer_class),
        warming=st.handshake_until is not None,
        ready=st.established,
        bytes_spent=int(st.bytes_spent),
        age_s=(t - st.warm_started_t) if st.warm_started_t is not None else 0.0,
    )


def _apply_plane_map(planes, plane_map):
    """Remap queued messages by msg_id or by data_type to a new plane."""
    moves = []
    for pi in range(len(planes)):
        for m in planes[pi]:
            target = plane_map.get(m.msg_id, plane_map.get(m.data_type))
            if target is not None and 0 <= target < len(planes) and target != pi:
                moves.append((m, pi, target))
    for m, pi, target in moves:
        try:
            planes[pi].remove(m)
        except ValueError:
            continue
        m.plane = target
        planes[target].append(m)


def _apply_drop(planes, by_id, plane_drops, drop_ids, t):
    drop_set = set(drop_ids)
    for pi in range(len(planes)):
        keep = []
        for m in planes[pi]:
            if m.msg_id in drop_set and m.delivered_t_s is None:
                m.dropped = True
                plane_drops[pi] += 1
            else:
                keep.append(m)
        planes[pi][:] = keep


def _plane_budgets(rate_caps, n_planes):
    """Per-plane byte budget for this tick from Action.rate_cap_mbps (SPEC
    §7.3). Missing / None / non-numeric => uncapped; negatives clamp to 0.
    Caps are per tick: a policy re-asserts them every tick it wants them."""
    budgets = [float("inf")] * n_planes
    if not rate_caps:
        return budgets
    for k, v in rate_caps.items():
        try:
            pi = int(k)
            cap = float(v)
        except (TypeError, ValueError):
            continue
        if 0 <= pi < n_planes and cap == cap:   # skip NaN
            budgets[pi] = max(0.0, cap) * 1e6 / 8.0 * SIM_TICK_S
    return budgets


def _drain(planes, fluids, avail_bytes, t, rtt_ms, plane_drops,
           rate_caps=None):
    """Drain planes in priority order (0 highest). Discrete messages first
    within a plane (FIFO), then any fluid sources assigned to that plane.
    Each plane is bounded by its per-plane rate cap (SPEC §7.3); capacity a
    capped plane leaves unused flows on to the next plane (work-conserving).
    Returns total delivered goodput bytes this tick.

    Delivery time: a message generated inside this tick (enqueue_t_s > t)
    cannot leave before it exists, so its departure is max(t, enqueue_t_s)
    (review fix: stamping from the tick start produced negative one-way
    delays and alerts 'delivered' before they were generated)."""
    one_way = (rtt_ms / 2.0) / 1000.0 + PROC_DELAY_S
    remaining = avail_bytes
    delivered = 0.0
    fluid_by_plane = {}
    for fs in fluids:
        fluid_by_plane.setdefault(fs.plane, []).append(fs)
    budgets = _plane_budgets(rate_caps, len(planes))

    # SPEC §14.4 [I39]: with the documented plane set (>= 5 planes) messages are
    # served before fluid video/bulk inside a plane (unchanged behaviour). When the
    # operator configured fewer planes, a plane is ONE FIFO shared by messages and
    # video: approximate FIFO interleaving by sharing the plane's bytes in
    # proportion to demand (queued message bytes vs fluid bytes offered this tick).
    shared_fifo = int(P.planes_configured) < 5

    for pi in range(len(planes)):
        if remaining <= 0:
            break
        budget = budgets[pi]
        q = planes[pi]
        msg_budget = float("inf")
        if shared_fifo and q and pi in fluid_by_plane:
            here = min(remaining, budget)
            d_m = sum(mm.size_bytes - mm.bytes_sent for mm in q)
            d_f = sum(fs.offered_mbps * 1e6 / 8.0 * SIM_TICK_S for fs in fluid_by_plane[pi])
            if d_m + d_f > here and d_m + d_f > 0:
                msg_budget = here * d_m / (d_m + d_f)
        # discrete messages FIFO
        idx = 0
        while idx < len(q) and remaining > 0 and budget > 0 and msg_budget > 0:
            m = q[idx]
            need = m.size_bytes - m.bytes_sent
            take = min(need, remaining, budget, msg_budget)
            m.bytes_sent += take
            remaining -= take
            budget -= take
            msg_budget -= take
            delivered += take
            if m.bytes_sent >= m.size_bytes:
                extra = (0.020 if m.data_type == "voice" else 0.0)
                m.delivered_t_s = max(t, m.enqueue_t_s) + one_way + extra
                idx += 1
            else:
                break
        if idx > 0:
            del q[:idx]
        # fluid sources on this plane
        if remaining > 0 and budget > 0 and pi in fluid_by_plane:
            for fs in fluid_by_plane[pi]:
                offered = fs.offered_mbps * 1e6 / 8.0 * SIM_TICK_S
                if fs.data_type == "bulk":
                    take = remaining   # greedy fill
                else:
                    take = min(offered, remaining)
                take = max(0.0, min(take, remaining, budget))
                fs.delivered_bytes += take
                remaining -= take
                budget -= take
                delivered += take
    # account offered for fluids even when nothing delivered (for goodput)
    return delivered
