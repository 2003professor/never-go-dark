"""mobike_roaming.py — IKEv2 MOBIKE-style session mobility (SPEC §11.1).

RFC 4555: when the active path dies or the address changes, the EXISTING
session migrates to another available path WITHOUT a new key exchange (an
UPDATE_SA_ADDRESSES-style exchange + a return-routability check ~= 1 RTT on
the new path). WireGuard endpoint roaming behaves similarly.

Still reactive: migration starts only after dead-peer detection (same DPD as
§4.2). Single active path; no standby pre-warmed; no freshness logic (static
plane scheduling). This is the "KATIM already roams" case written as a named,
citable mechanism (SPEC §4.4). Our P2 must beat it on outage by prediction;
P1 must beat it on freshness.
"""
from __future__ import annotations

from policies.interface import Action, Policy


class MobikeRoaming(Policy):
    name = "mobike_roaming"
    reads_messages = False
    # The migration-without-rekey capability lives in the gateway recovery
    # mode (SPEC §4.4 SESSIONS_ROAM / §11.1); the policy adds no per-tick logic.
    gateway_config = {"sessions_roam": True,
                      "recovery_mode": "mobike_migrate"}

    def decide(self, obs) -> Action:
        return Action()


POLICY = MobikeRoaming()
