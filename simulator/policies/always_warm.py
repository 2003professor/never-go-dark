"""always_warm.py — naive make-before-break (SPEC §9.2, frozen).

Always keeps a standby tunnel warm on every available alternate bearer and
cuts over the instant the active leg stops delivering. Zero outage ideally,
but maximal standby-transmit-seconds and satcom overhead — the
emissions-expensive extreme P2 must beat on the trade-off, not just on outage.
No freshness logic (static plane scheduling).
"""
from __future__ import annotations

from policies.interface import Action, Policy


class AlwaysWarm(Policy):
    name = "always_warm"
    reads_messages = False
    gateway_config = {"sessions_roam": False,
                      "recovery_mode": "reactive_rehandshake"}

    def reset(self, scenario_name, seed):
        self._on_standby = False
        self._low_since = None

    def decide(self, obs) -> Action:
        act = Action()
        link = obs.link
        cand = obs.bearer_hint.candidate_available if obs.bearer_hint else None
        # keep a standby warm on the available alternate bearer at all times
        if cand is not None and not obs.standby.ready and not obs.standby.warming:
            act.warm_standby = cand
        # the active leg has stopped delivering: tunnel dark, or a capacity
        # stall (near-zero goodput while traffic backs up) held briefly.
        max_head_age = max((p.head_age_s for p in obs.planes), default=0.0)
        hard = link.loss_est > 0.5 or link.dpd_missed >= 1
        low = (link.tput_est_mbps is not None and link.tput_est_mbps < 0.05
               and max_head_age > 2.0)
        if low:
            self._low_since = self._low_since or obs.t_s
        else:
            self._low_since = None
        stalled = hard or (self._low_since is not None
                           and obs.t_s - self._low_since >= 0.5)
        if obs.standby.ready and not self._on_standby and stalled:
            act.cutover = True
            self._on_standby = True
        return act


POLICY = AlwaysWarm()
