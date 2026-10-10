"""atsss_active_standby.py — 3GPP ATSSS-style multi-access steering (SPEC §11.2).

TS 23.501 §5.32 Active-Standby steering mode: a multi-access session keeps
BOTH access legs established at all times and steers traffic to the standby
when the active leg is unavailable or its measured RTT/loss crosses a
threshold, with hysteresis.

Thresholds [I] (sweep S-ATSSS): RTT > 2x the bearer's base RTT, or loss > 5%,
held for 1 s; switch back after 5 s of recovery. Because both legs are kept
up, standby transmit-seconds run continuously wherever a second leg exists —
the emissions-expensive extreme P2 must match on outage while cutting
emissions. Plane scheduling is static (ATSSS steers flows; it does not drop
superseded data).
"""
from __future__ import annotations

from policies.interface import Action, Policy

RTT_MULT = 2.0
LOSS_THRESH = 0.05
HOLD_S = 1.0
RECOVER_S = 5.0


class AtsssActiveStandby(Policy):
    name = "atsss_active_standby"
    reads_messages = False
    gateway_config = {"sessions_roam": False,
                      "recovery_mode": "reactive_rehandshake"}

    def reset(self, scenario_name, seed):
        self.base_rtt = None
        self._bad_since = None
        self._good_since = None
        self._on_standby = False

    def decide(self, obs) -> Action:
        act = Action()
        cand = obs.bearer_hint.candidate_available if obs.bearer_hint else None
        link = obs.link
        rtt = link.keepalive_rtt_ms
        # establish a base RTT from the first healthy observations
        if rtt is not None:
            if self.base_rtt is None:
                self.base_rtt = rtt
            else:
                self.base_rtt = min(self.base_rtt, rtt)

        # keep both legs up: always warm the available candidate
        if cand is not None and not obs.standby.ready and not obs.standby.warming:
            act.warm_standby = cand
        elif cand is not None and not self._on_standby:
            act.warm_standby = cand   # keep it warm (idempotent)

        t = obs.t_s
        max_head_age = max((p.head_age_s for p in obs.planes), default=0.0)
        # Hard trigger: the active leg is effectively unavailable (keepalive
        # loss near-total or peer declared dead) -> steer immediately, no
        # hysteresis (ATSSS switches at once on unavailability).
        hard = (link.loss_est > 0.5) or (link.dpd_missed >= 1)
        # Soft trigger: performance-measurement threshold crossed, held 1 s.
        soft = False
        if self.base_rtt is not None and rtt is not None and \
                rtt > RTT_MULT * self.base_rtt:
            soft = True
        if 0.0 < link.loss_est <= 0.5 and link.loss_est > LOSS_THRESH:
            soft = True
        # throughput measurement: the active leg is delivering ~nothing while
        # traffic is backing up (a capacity stall the PMF would observe).
        if link.tput_est_mbps is not None and link.tput_est_mbps < 0.05 \
                and max_head_age > 2.0:
            soft = True

        if soft:
            if self._bad_since is None:
                self._bad_since = t
        else:
            self._bad_since = None

        trigger = hard or (self._bad_since is not None
                           and (t - self._bad_since) >= HOLD_S)
        if trigger and obs.standby.ready and not self._on_standby:
            act.cutover = True
            self._on_standby = True
        return act


POLICY = AtsssActiveStandby()
