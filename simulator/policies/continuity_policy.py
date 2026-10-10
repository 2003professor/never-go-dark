"""continuity_policy.py — EDITABLE (SPEC §7, §10.1). P2 make-before-break.

The second of the two files that change after freeze. Right now a STUB that
behaves like the documented-static baseline (no standby, reactive recovery).

When P2 is implemented here it will, using ONLY the §7 Obs:
  * predict an imminent transition from link telemetry (rising RTT trend,
    falling throughput estimate, the candidate-available hint),
  * warm a standby tunnel on the predicted target bearer before the active
    one breaks, and cut over make-before-break,
  * tear the standby down afterwards to keep standby-transmit-seconds (and
    satcom emissions) far below the always-up mechanisms,
all without touching crypto (timing/triggers of standard handshakes only).
"""
from __future__ import annotations

from policies.interface import Action, Policy


class ContinuityP2(Policy):
    name = "continuity_p2_stub"
    reads_messages = False
    gateway_config = {"sessions_roam": False,
                      "recovery_mode": "reactive_rehandshake"}

    def decide(self, obs) -> Action:
        # STUB: documented-static behaviour (reactive recovery). P2 goes here.
        return Action()


POLICY = ContinuityP2()
