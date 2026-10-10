"""Gateway model — SPEC §4 (+ §13.5 per-tick handshake physics).

Models one 9001-R pair (moving team gateway <-> command gateway), one
association carrying up to 8 QoS-aware encryption planes. One active cipher
port; bearer selection is external (SPEC §4, §1.1). This module owns:

  * the documented timers/counters (SPEC §4.2),
  * the handshake cost model (SPEC §4.3) — now advanced PER TICK (SPEC §13.5):
    each round needs one RTT on the bearer it runs over plus serialisation of
    the key material at the bearer's CURRENT capacity; a stalled/unusable
    bearer freezes handshake progress; an attempt that does not complete
    within a timeout FAILS and the documented 10 s retry applies,
  * the endpoint-roaming switch SESSIONS_ROAM (SPEC §4.4),
  * the tunnel state machine that turns "path broke" into outage seconds.

The 8 plane QUEUES and byte delivery live in the engine (it owns traffic and
metrics); this module owns tunnel liveness, recovery timing and emissions.

Mechanism configuration (how an existing mechanism recovers) is set once per
run from the policy's ``gateway_config`` — this is distinct from the per-tick
``Obs`` the policy reads (SPEC §7.4). It lets the five frozen baselines stand
for genuinely different *mechanisms* (documented re-handshake vs MOBIKE-style
migration vs ATSSS-style standby) while the editable P1/P2 policy runs on the
pessimistic default and must beat them through the Action channel alone.

The SAME per-tick handshake model runs for every mechanism, the oracle
included (SPEC §13.4/§13.5): the oracle's privilege is foresight, never a
cheaper handshake.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from sim.params import P

SIM_TICK_S = 0.1

# ---- documented timers / counters (SPEC §4.2) ----------------------------
KA_S = 1.0               # keepalive period            [I4] sweep S-DPD
DPD_MISS = 3             # missed keepalives => dead    [I4] sweep S-DPD
RETRY_S = 10.0           # handshake retry period       [D] WP:323
REHANDSHAKE_S = 60.0     # full re-handshake period     [D] WP:150-151,330-332
RATCHET_PKTS = 131072    # data-plane key ratchet       [D] WP:193-194

# ---- handshake cost model (SPEC §4.3) ------------------------------------
HS_ROUNDS = 2            # Noise-family stand-in; +1 if PQ  [I5] sweep S-HS-ROUNDS
HS_BYTES = 4096          # triple-KEM ML-KEM-class stand-in [I6] sweep S-HS-BYTES
KEEPALIVE_BYTES = 128    # per keepalive, both directions summed [I]
# A handshake ATTEMPT that cannot complete within this wall-clock window
# (bearer unusable/stalled too long, or capacity too low to serialise the key
# material) FAILS; the documented 10 s retry (RETRY_S) then schedules a fresh
# attempt. [I13.5] sweep part of S-HS. Chosen = the retry period, so a dead
# bearer burns exactly one retry cycle per failed attempt.
HS_ATTEMPT_TIMEOUT_S = RETRY_S

# ---- roaming switch (SPEC §4.4) ------------------------------------------
SESSIONS_ROAM_DEFAULT = False   # pessimistic, P2-favouring; the headline [I7]
MOBIKE_MIGRATE_ROUNDS = 1       # ~1 RTT return-routability (RFC 4555 / §11.1)

PROC_DELAY_S = 0.005     # fixed red/crypto/black processing per direction [I]


def handshake_time_s(rounds: int, rtt_s: float, hs_bytes: int,
                     dl_Bps: float, ul_Bps: float) -> float:
    """SPEC §4.3 closed-form handshake cost (the *idealised* duration at a
    CONSTANT capacity):

        handshake_time_s = rounds*rtt_s + hs_bytes / min(dl_Bps, ul_Bps)

    This is kept as a pure helper: the engine's gateway now advances the
    handshake per tick against the bearer's time-varying capacity (SPEC
    §13.5), but the oracle planner (SPEC §13.4) uses this closed form to
    estimate the *lead time* a warm-start needs, and unit tests pin the
    formula. Returns +inf if the bottleneck direction has ~no capacity.
    """
    rate = min(dl_Bps, ul_Bps)
    if rate <= 0:
        return float("inf")
    return rounds * rtt_s + (hs_bytes / rate)


@dataclass
class GatewayConfig:
    """Per-run mechanism configuration (SPEC §4.2/§4.4 + §11)."""
    ka_s: float = KA_S
    dpd_miss: int = DPD_MISS
    retry_s: float = RETRY_S
    rehandshake_s: float = REHANDSHAKE_S
    hs_rounds: int = HS_ROUNDS
    hs_bytes: int = HS_BYTES
    sessions_roam: bool = SESSIONS_ROAM_DEFAULT
    keepalive_bytes: int = KEEPALIVE_BYTES
    hs_attempt_timeout_s: float = HS_ATTEMPT_TIMEOUT_S
    # documented 10 s retry timer PHASE (SPEC §13.8 [I29], sweep S-RETRY-PHASE):
    # 'free' free-running timer (seeded phase in [0, retry_s)), 'immediate'
    # (attempt as soon as DPD declares the peer dead) or 'worst' (+retry_s after
    # the break). Default 'free'. retry_phase_s is the per-run seeded phase.
    retry_phase: str = "free"
    retry_phase_s: float = 0.0

    @property
    def dpd_timeout_s(self) -> float:
        return self.dpd_miss * self.ka_s

    def apply_timer_override(self, ov: dict):
        """Bounded timer tweaks from the policy (SPEC §7.3), clamped."""
        if not ov:
            return
        if "ka_s" in ov and ov["ka_s"] is not None:
            self.ka_s = float(min(5.0, max(0.25, ov["ka_s"])))
        if "retry_s" in ov and ov["retry_s"] is not None:
            self.retry_s = float(min(10.0, max(0.5, ov["retry_s"])))
        if "rehandshake_s" in ov and ov["rehandshake_s"] is not None:
            self.rehandshake_s = float(min(300.0, max(10.0, ov["rehandshake_s"])))


@dataclass
class Handshake:
    """Per-tick handshake progress state (SPEC §13.5).

    A handshake is ``rounds`` sequential round trips (each costing one RTT on
    the bearer it runs over, sampled at the moment the round starts) followed
    by serialising ``bytes_total`` of key material at the bearer's current
    capacity. Progress only advances on ticks where the bearer is usable; a
    stalled bearer freezes it. ``elapsed_s`` is wall time since the attempt
    began and drives the attempt timeout (even while stalled — a dead bearer
    cannot hold a handshake open forever)."""
    rounds_left: int
    bytes_left: float
    bytes_total: float
    round_timer_s: float = 0.0     # RTT remaining in the round in progress
    elapsed_s: float = 0.0

    def advance(self, usable: bool, rtt_s: float, cap_bps: float,
                dt: float, timeout_s: float, loss: float = 0.0) -> str:
        """Advance one tick. Returns 'done' | 'failed' | 'progress' | 'stall'.

        SPEC §13.8 [I28] packet-loss model (deterministic expected-cost, so the
        run stays bit-reproducible): a round over a link with loss ``p`` needs
        on expectation 1/(1-p)^2 attempts (a round-trip loses a packet in
        either direction), so its RTT cost is inflated by that factor; the key
        material serialises at the loss-reduced goodput cap*(1-p). An
        unusable/stalled tick freezes progress and ages toward the timeout."""
        self.elapsed_s += dt
        if not usable or cap_bps <= 0.0:
            return "failed" if self.elapsed_s > timeout_s else "stall"
        p = min(0.95, max(0.0, loss))
        inflate = 1.0 / ((1.0 - p) * (1.0 - p))
        eff_cap = cap_bps * (1.0 - p)
        remaining = dt
        while self.rounds_left > 0 and remaining > 1e-12:
            if self.round_timer_s <= 0.0:
                self.round_timer_s = max(rtt_s * inflate, 1e-9)
            step = min(remaining, self.round_timer_s)
            self.round_timer_s -= step
            remaining -= step
            if self.round_timer_s <= 1e-12:
                self.rounds_left -= 1
                self.round_timer_s = 0.0
        if self.rounds_left == 0 and remaining > 1e-12 and self.bytes_left > 0.0:
            self.bytes_left -= eff_cap * remaining
        if self.rounds_left == 0 and self.bytes_left <= 0.0:
            return "done"
        return "failed" if self.elapsed_s > timeout_s else "progress"


@dataclass
class Tunnel:
    """One encryption association (active or standby) over one bearer path.

    States are derived, not stored as an enum:
      * UP          : established & peer_alive & bearer usable & not handshaking
      * DARK        : path broke; recovering (DPD -> retry -> handshake)
      * WARMING     : a standby being brought up (handshake in progress)
      * READY       : standby established, not yet active
    """
    key_id: int                      # fresh independent key id (SPEC §7.3/§7.5)
    established: bool = False
    peer_alive: bool = False
    endpoint_id: str = ""            # black-side endpoint (address/NAT)
    bearer_class: str = ""
    # recovery clock
    down_since: Optional[float] = None
    down_reason: Optional[str] = None     # 'gap' | 'endpoint'
    dead_declared: bool = False
    next_retry_t: Optional[float] = None
    hs: Optional[Handshake] = None        # per-tick handshake in progress
    # emissions / overhead accounting
    bytes_spent: int = 0
    warm_started_t: Optional[float] = None
    is_standby: bool = False
    pkts_sent: int = 0
    ratchet_epoch: int = 0
    last_ka_t: float = 0.0
    last_rehs_t: float = 0.0

    @property
    def handshake_until(self):
        """Back-compat shim: truthy iff a handshake is in progress."""
        return self.hs

    def up(self) -> bool:
        # A tunnel whose path is broken (down_since set) is DARK until it is
        # resumed, even before DPD has formally declared the peer dead: at an
        # endpoint change or a throughput gap, packets stop arriving at once.
        return (self.down_since is None and self.established and self.peer_alive
                and self.hs is None)


@dataclass
class StepResult:
    """What the engine needs back from a gateway step."""
    active_up: bool
    outage_tick: bool           # SPEC §8.4: tunnel dark OR zero capacity (no
                                # plane can deliver). The engine refines the
                                # capacity half with deliverable goodput
                                # after overhead + loss (STALL_GOODPUT_MBPS).
    tunnel_dark: bool           # tunnel down / bearer unusable (subset)
    overhead_bytes: int         # keepalive + re-handshake + handshake this tick
    standby_bytes: int          # subset of overhead spent on a standby tunnel
    standby_tx_tick: bool       # standby actively transmitting this tick
    standby_warming: bool
    standby_ready: bool
    standby_bytes_total: int
    standby_age_s: float
    cutover_done: bool
    invalid_reason: Optional[str] = None
    zero_cap: bool = False      # tunnel up but bearer capacity <= 0 (subset)


class Gateway:
    """Manages the active tunnel + an optional standby, timers and emissions."""

    def __init__(self, cfg: GatewayConfig, recovery_mode: str = "reactive_rehandshake"):
        self.cfg = cfg
        # recovery_mode: 'reactive_rehandshake' | 'mobike_migrate'
        self.recovery_mode = recovery_mode
        self._next_key = 1
        self.active = Tunnel(key_id=self._new_key(), established=True,
                             peer_alive=True)
        self.standby: Optional[Tunnel] = None
        self._used_keys = set()
        self.overhead_total = 0
        self.standby_tx_s = 0.0
        self.outage_s = 0.0

    def _new_key(self) -> int:
        k = self._next_key
        self._next_key += 1
        return k

    def _start_handshake(self, rounds: int, hs_bytes: float) -> Handshake:
        return Handshake(rounds_left=int(rounds), bytes_left=float(hs_bytes),
                         bytes_total=float(hs_bytes))

    # ------------------------------------------------------------------
    def bind_active_endpoint(self, endpoint_id: str, bearer_class: str):
        self.active.endpoint_id = endpoint_id
        self.active.bearer_class = bearer_class

    def _break_active(self, t_s: float, reason: str):
        """Path under the active tunnel just broke. reason in {'gap','endpoint'}.
        A 'gap' (same endpoint) may be ridden through by keepalive if it
        recovers before DPD; an 'endpoint' change never is (it needs at least
        a migration round trip)."""
        if self.active.down_since is None:
            self.active.down_since = t_s
            self.active.down_reason = reason
            self.active.dead_declared = False
            self.active.hs = None
            # retry schedule (SPEC §13.8 [I29]): free-running phase, immediate
            # on DPD, or worst-case (+retry_s). The first attempt is further
            # gated by dead_declared, so DPD always precedes it.
            mode = getattr(self.cfg, "retry_phase", "worst")
            if mode == "immediate":
                self.active.next_retry_t = t_s
            elif mode == "free":
                self.active.next_retry_t = t_s + (self.cfg.retry_phase_s
                                                  % max(1e-9, self.cfg.retry_s))
            else:
                self.active.next_retry_t = t_s + self.cfg.retry_s

    def _resume_active(self, t_s: float):
        self.active.down_since = None
        self.active.down_reason = None
        self.active.dead_declared = False
        self.active.hs = None
        self.active.next_retry_t = None
        self.active.established = True
        self.active.peer_alive = True

    # ------------------------------------------------------------------
    def step(self, t_s: float,
             active_usable: bool, active_rtt_ms: Optional[float],
             active_cap_mbps: float, active_endpoint_id: str,
             active_bearer_class: str,
             endpoint_changed: bool,
             cand_class: Optional[str], cand_usable: bool,
             cand_rtt_ms: Optional[float], cand_cap_mbps: float,
             cand_endpoint_id: str,
             warm_standby: Optional[str], cutover: bool,
             teardown_standby: bool,
             active_cap_dl_mbps: float = None,
             cand_cap_dl_mbps: float = None,
             active_loss: float = 0.0, cand_loss: float = 0.0) -> StepResult:
        """Advance tunnel/standby state one tick and report deliverability."""
        cfg = self.cfg
        dt = SIM_TICK_S
        overhead = 0
        standby_bytes = 0
        cutover_done = False
        invalid = None

        migrate_mode = cfg.sessions_roam or self.recovery_mode == "mobike_migrate"
        a_rtt_s = (active_rtt_ms if active_rtt_ms is not None else 600.0) / 1000.0
        # handshake serialises at min(DL, UL) (SPEC §4.3): the capacity passed
        # is the UL-direction (SPEC §13.8); the DL ceiling bounds the round-trip
        # key exchange. Default DL = UL when not supplied.
        a_dl = active_cap_dl_mbps if active_cap_dl_mbps is not None else active_cap_mbps
        a_cap_bps = min(active_cap_mbps, a_dl) * 1e6 / 8.0

        # --- endpoint change on the active path (transition) --------------
        if endpoint_changed:
            self.active.endpoint_id = active_endpoint_id
            self.active.bearer_class = active_bearer_class
            self._break_active(t_s, reason="endpoint")
            if not migrate_mode:
                self.active.established = False   # full re-establishment needed

        # --- active path usability --------------------------------------
        if not active_usable:
            # throughput gap / availability outage under the active tunnel
            self._break_active(t_s, reason="gap")

        # --- recovery of the active tunnel ------------------------------
        if self.active.down_since is not None:
            dpd_to = cfg.dpd_timeout_s
            broke_dur = t_s - self.active.down_since
            if active_usable and self.active.down_reason == "gap" and not \
                    self.active.dead_declared and broke_dur < dpd_to \
                    and self.active.established:
                # short same-endpoint gap ridden through by keepalive
                self._resume_active(t_s)
            else:
                # DPD is time-based (missed keepalives) — it declares the peer
                # dead after the timeout whether or not the bearer is usable.
                if not self.active.dead_declared and broke_dur >= dpd_to:
                    self.active.dead_declared = True
                    self.active.peer_alive = False
                    if not migrate_mode:
                        self.active.established = False
                if self.active.dead_declared:
                    if migrate_mode:
                        if self.active.hs is None and active_usable:
                            self.active.hs = self._start_handshake(
                                MOBIKE_MIGRATE_ROUNDS, 0.0)
                    else:
                        # documented reactive: at each retry tick start a fresh
                        # handshake attempt (its key material hits the wire).
                        if self.active.hs is None and \
                                t_s >= self.active.next_retry_t:
                            self.active.hs = self._start_handshake(
                                cfg.hs_rounds, cfg.hs_bytes)
                            self.active.key_id = self._new_key()
                            overhead += cfg.hs_bytes
                    # advance the in-progress handshake PER TICK against the
                    # bearer's CURRENT capacity (§13.5); it stalls on an
                    # unusable/stalled bearer and fails if it cannot complete
                    # within the attempt timeout, after which the documented
                    # 10 s retry schedules the next attempt.
                    if self.active.hs is not None:
                        res = self.active.hs.advance(
                            usable=active_usable, rtt_s=a_rtt_s,
                            cap_bps=a_cap_bps, dt=dt,
                            timeout_s=cfg.hs_attempt_timeout_s,
                            loss=active_loss if P.hs_loss_model else 0.0)
                        if res == "done":
                            self._resume_active(t_s)
                        elif res == "failed":
                            self.active.hs = None
                            self.active.next_retry_t = t_s + cfg.retry_s
                    elif t_s >= self.active.next_retry_t:
                        # no attempt in progress (e.g. dead bearer) but the
                        # retry tick passed: keep the 10 s retry cadence ticking.
                        self.active.next_retry_t = t_s + cfg.retry_s

        active_up = self.active.up() and active_usable

        # --- standby management (Action channel: SPEC §7.3) --------------
        if teardown_standby and self.standby is not None:
            self.standby = None
        c_rtt_s = (cand_rtt_ms if cand_rtt_ms is not None else 600.0) / 1000.0
        c_dl = cand_cap_dl_mbps if cand_cap_dl_mbps is not None else cand_cap_mbps
        c_cap_bps = max(min(cand_cap_mbps, c_dl) * 1e6 / 8.0, 0.0)
        if warm_standby is not None:
            want = str(warm_standby)
            if cand_class is not None and want == cand_class and cand_usable:
                if self.standby is None or self.standby.bearer_class != want:
                    self.standby = Tunnel(key_id=self._new_key(),
                                          is_standby=True, bearer_class=want,
                                          endpoint_id=cand_endpoint_id,
                                          warm_started_t=t_s)
                    # begin the warming handshake on the candidate bearer; its
                    # key material hits the wire now (counted), and it advances
                    # per tick against the candidate's capacity below.
                    self.standby.hs = self._start_handshake(
                        cfg.hs_rounds, cfg.hs_bytes)
                    standby_bytes += cfg.hs_bytes
                    self.standby.bytes_spent += cfg.hs_bytes

        standby_tx_tick = False
        if self.standby is not None:
            st = self.standby
            cand_live = cand_usable and cand_class == st.bearer_class
            if not cand_live:
                # candidate's bearer is unusable: the warming handshake stalls,
                # and an ESTABLISHED standby loses liveness by the same
                # time-based DPD as the active tunnel (SPEC §13.8) — it is no
                # longer instantly usable and must re-handshake. Keepalives are
                # still emitted (a real gateway keeps probing), so the cost of
                # holding a standby over a flapping bearer is charged.
                if st.down_since is None:
                    st.down_since = t_s
                if st.established and (t_s - st.down_since) >= cfg.dpd_timeout_s:
                    st.established = False
                    st.peer_alive = False
                    if st.hs is None:
                        st.hs = self._start_handshake(cfg.hs_rounds, cfg.hs_bytes)
                        standby_bytes += cfg.hs_bytes
                        st.bytes_spent += cfg.hs_bytes
                if st.hs is not None:
                    res = st.hs.advance(usable=False, rtt_s=c_rtt_s,
                                        cap_bps=0.0, dt=dt,
                                        timeout_s=cfg.hs_attempt_timeout_s)
                    if res == "failed":
                        st.hs = self._start_handshake(cfg.hs_rounds, cfg.hs_bytes)
                        standby_bytes += cfg.hs_bytes
                        st.bytes_spent += cfg.hs_bytes
                if t_s - st.last_ka_t >= cfg.ka_s:
                    st.last_ka_t = t_s
                    standby_bytes += cfg.keepalive_bytes
                    st.bytes_spent += cfg.keepalive_bytes
                standby_tx_tick = True
            else:
                st.down_since = None
                # warming completion advances per tick against candidate cap
                if st.hs is not None:
                    res = st.hs.advance(usable=True, rtt_s=c_rtt_s,
                                        cap_bps=c_cap_bps, dt=dt,
                                        timeout_s=cfg.hs_attempt_timeout_s,
                                        loss=cand_loss if P.hs_loss_model else 0.0)
                    if res == "done":
                        st.hs = None
                        st.established = True
                        st.peer_alive = True
                    elif res == "failed":
                        st.hs = self._start_handshake(cfg.hs_rounds, cfg.hs_bytes)
                        standby_bytes += cfg.hs_bytes
                        st.bytes_spent += cfg.hs_bytes
                if t_s - st.last_ka_t >= cfg.ka_s:
                    st.last_ka_t = t_s
                    standby_bytes += cfg.keepalive_bytes
                    st.bytes_spent += cfg.keepalive_bytes
                standby_tx_tick = True

        # --- cutover (make-before-break) --------------------------------
        # SPEC §13.8: refuse a cut-over onto a standby whose bearer is unusable
        # this tick — a warmed tunnel over a dead bearer delivers nothing.
        if cutover and self.standby is not None and self.standby.established \
                and cand_usable and cand_class == self.standby.bearer_class:
            # swap: the warmed standby becomes active, already UP => ~0 outage
            old = self.active
            self.standby.is_standby = False
            self.active = self.standby
            self.standby = None
            self.active.down_since = None
            self.active.dead_declared = False
            self.active.hs = None
            active_up = True
            cutover_done = True
            # fresh independent keys invariant (SPEC §7.5)
            if self.active.key_id in self._used_keys:
                invalid = "standby reused an active key"
            self._used_keys.add(old.key_id)

        # --- keepalive + periodic re-handshake overhead on the active ----
        if active_up:
            if t_s - self.active.last_ka_t >= cfg.ka_s:
                self.active.last_ka_t = t_s
                overhead += cfg.keepalive_bytes
            if t_s - self.active.last_rehs_t >= cfg.rehandshake_s:
                self.active.last_rehs_t = t_s
                overhead += cfg.hs_bytes   # re-auth refreshes all keys (in-band)

        overhead += standby_bytes
        self.overhead_total += overhead
        if standby_tx_tick:
            self.standby_tx_s += SIM_TICK_S
        # SPEC §8.4 "no plane can deliver": the tunnel is dark, OR it is up
        # over a bearer whose capacity this tick is zero (a present, genuinely
        # zero throughput sample moves no bytes).
        tunnel_dark = not active_up
        zero_cap = active_up and not (active_cap_mbps > 0.0)
        outage_tick = tunnel_dark or zero_cap
        if outage_tick:
            self.outage_s += SIM_TICK_S

        st = self.standby
        return StepResult(
            active_up=active_up,
            outage_tick=outage_tick,
            tunnel_dark=tunnel_dark,
            zero_cap=zero_cap,
            overhead_bytes=overhead,
            standby_bytes=standby_bytes,
            standby_tx_tick=standby_tx_tick,
            standby_warming=bool(st and st.hs is not None),
            standby_ready=bool(st and st.established),
            standby_bytes_total=int(st.bytes_spent) if st else 0,
            standby_age_s=(t_s - st.warm_started_t) if st and st.warm_started_t
            is not None else 0.0,
            cutover_done=cutover_done,
            invalid_reason=invalid,
        )
