"""diag_fixed_timer.py — DIAGNOSTIC gaming policy (SPEC §13.1, fix A).

A deliberately naive policy that tries to GAME the benchmark by betting the
transition happens at a single CONSTANT time-since-episode-start: it warms the
standby briefly just before that time and cuts over AT that time, then tears
the standby down — the minimum-emission move that WOULD be near-optimal if the
transition were at a fixed instant (as it was under the old fixed-t_x design).
The constant is the one that is optimal on the TRAIN split (grid search).

It can recover time-since-start despite the observable-clock offset (the offset
is constant within a run, so ``obs.t_s - first_obs.t_s`` is the true episode
time). The clock offset only defeats ABSOLUTE-time gaming; the defence that
defeats a narrow constant timer is the randomised EVENT POSITION (uniform
25-75% of a randomised-length episode): the bet misses, so the policy either
cuts over early onto the worse bearer or too late into the dark gap. The frozen
tests show (a) under a constant schedule (randomisation off) this gamer reaches
near-oracle outage at low emissions, but (b) under the randomised design it
cannot — its low-emission bet lands near the documented reactive baseline, and
reaching low outage forces always-warm-level emissions. So the benchmark cannot
be gamed by memorising a schedule. NOT a shippable mechanism.
"""
from __future__ import annotations

from policies.interface import Action, Policy

# train-optimal constant transition time-since-start (grid search over TRAIN).
CUTOVER_SINCE_START_S = 50.0
WARM_LEAD_S = 8.0                 # warm this long before the bet
TEARDOWN_AFTER_S = 6.0            # drop the standby if the bet did not pay off


class DiagFixedTimer(Policy):
    name = "diag_fixed_timer"
    reads_messages = False
    gateway_config = {"sessions_roam": False,
                      "recovery_mode": "reactive_rehandshake"}

    def reset(self, token, seed):
        self._t0 = None
        self._cut = False

    def decide(self, obs) -> Action:
        act = Action()
        if self._t0 is None:
            self._t0 = obs.t_s
        elapsed = obs.t_s - self._t0
        cand = obs.bearer_hint.candidate_available if obs.bearer_hint else None
        warm_at = CUTOVER_SINCE_START_S - WARM_LEAD_S
        if not self._cut:
            if warm_at <= elapsed <= CUTOVER_SINCE_START_S + TEARDOWN_AFTER_S \
                    and cand is not None and not obs.standby.ready \
                    and not obs.standby.warming:
                act.warm_standby = cand
            if elapsed >= CUTOVER_SINCE_START_S and obs.standby.ready:
                act.cutover = True
                self._cut = True
            elif elapsed > CUTOVER_SINCE_START_S + TEARDOWN_AFTER_S \
                    and not obs.standby.ready:
                act.teardown_standby = True       # bet missed; stop emitting
        return act


POLICY = DiagFixedTimer()
