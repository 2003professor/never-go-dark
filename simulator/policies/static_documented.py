"""static_documented.py — the bar (SPEC §9.1, frozen).

Faithful emulation of documented 9001-R behaviour: fixed plane->traffic table,
internal discard priority within the gateway, NO bearer reaction, NO
superseded-drop, reactive recovery only (10 s retry, 60 s re-handshake,
131072-pkt ratchet, keepalive DPD). This is the bar that "beats a faithful
emulation of the documented design" is measured against.
"""
from __future__ import annotations

from policies.interface import Action, Policy


class StaticDocumented(Policy):
    name = "static_documented"
    reads_messages = False
    # pessimistic, P2-favouring default: endpoint change => full re-handshake.
    gateway_config = {"sessions_roam": False,
                      "recovery_mode": "reactive_rehandshake"}

    def decide(self, obs) -> Action:
        # Static mapping is applied at enqueue time; the documented design does
        # nothing adaptive per tick.
        return Action()


POLICY = StaticDocumented()
