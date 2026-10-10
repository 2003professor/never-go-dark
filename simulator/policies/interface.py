"""Policy interface — SPEC §7 (frozen).

The policy is the only editable code. It is called every tick with an
``Obs`` and returns an ``Action``. It may read ONLY what a real 9001-R (plus
its NMS/CNM config hooks) could observe. The engine passes a frozen ``Obs``
and nothing else: no handle to the engine, trace arrays, transition schedule,
future samples, oracle, RNG or metrics (SPEC §7.4). Any attempt to reach
engine internals fails because they are simply not referenced here.

This module is imported by every baseline policy and by the editable
``policy.py`` / ``continuity_policy.py``. It is pure data + a base class; it
has no dependency on ``sim/`` so a policy physically cannot import engine
state through it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Bearer classes the external router MAY expose as a hint (SPEC §7.1).
# A real gateway does not see the radio; it only ever sees a coarse class
# flag the black-side router may surface. Nothing finer than this.
# ---------------------------------------------------------------------------
class BearerClass(str, Enum):
    FIVEG = "5G"
    LTE = "LTE"
    LEO = "LEO"
    GEO = "GEO"
    WIFI = "WiFi"
    CELLULAR = "cellular-subflow"
    UNKNOWN = "unknown"


# Fixed set of QoS planes (SPEC §4.1): up to 8.
N_PLANES = 8

# Documented static plane assignment by data_type (SPEC §9.1, §5.x).
# plane 0 = highest priority (voice), ascending = lower priority.
PLANE_VOICE = 0
PLANE_ALERT = 1
PLANE_PLI = 2       # telemetry / position reports
PLANE_VIDEO = 3
PLANE_BULK = 4

STATIC_PLANE_MAP = {
    "voice": PLANE_VOICE,
    "alert": PLANE_ALERT,
    "pli": PLANE_PLI,
    "video": PLANE_VIDEO,
    "bulk": PLANE_BULK,
}


@dataclass(frozen=True)
class PlaneState:
    """Per-plane queue snapshot the gateway can read on its own red side."""
    plane_idx: int
    depth_pkts: int
    depth_bytes: int
    head_age_s: float      # age of the oldest queued message in this plane
    drops: int             # cumulative drops on this plane so far


@dataclass(frozen=True)
class LinkTelemetry:
    """Estimates the gateway computes from its OWN keepalives and traffic.

    These are NOT the raw trace. ``tput_est_mbps`` is an EWMA of delivered
    goodput, ``keepalive_rtt_ms`` comes from keepalive echoes, etc. All are
    quantities a real device measures for itself (SPEC §7.1).
    """
    keepalive_rtt_ms: Optional[float]
    rtt_trend: float          # +ve = rising RTT (sign of a worsening link)
    loss_est: float           # 0..1 estimate from keepalive/ack loss
    tput_est_mbps: Optional[float]
    telemetry_age_s: float    # staleness of the freshest telemetry (SPEC §2.3)
    dpd_missed: int           # consecutive missed keepalives right now


@dataclass(frozen=True)
class MsgMeta:
    """Red-side plaintext metadata the gateway legitimately has (SPEC §7.2).

    The policy may NOT read delivered/undelivered ground truth, true AoI
    contribution, or anything about the future.
    """
    msg_id: int
    data_type: str            # 'pli'|'alert'|'voice'|'video'|'bulk'
    size_bytes: int
    enqueue_t_s: float
    urgency: str              # 'high'|'realtime'|'normal'|'background'
    deadline_s: Optional[float] = None   # absolute deadline time if any
    supersedes_id: Optional[int] = None  # a previous msg this one supersedes
    source_id: Optional[int] = None      # which responder (for PLI supersede)


@dataclass(frozen=True)
class BearerHint:
    """Optional coarse flag the external router MAY expose (SPEC §7.1)."""
    current_bearer_class: Optional[BearerClass] = None
    candidate_available: Optional[BearerClass] = None


@dataclass(frozen=True)
class ModemStatus:
    """Modem status the external router exports for the bearer the tunnel rides
    (SPEC §14.1). Cellular modems report RSRP/SINR via 3GPP AT+CESQ (TS 27.007);
    vehicle routers expose it. The gateway's crypto core still never sees the
    radio — this is a router-side hint consumed only by the continuity policy.
    ``kind``: 'cellular' (signal_dbm = RSRP/RSCP as logged), 'wifi' (signal_dbm =
    RSSI) or 'none' (satellite links export nothing here). Signal values are not
    comparable across access modes; read them together with ``access_mode``."""
    kind: str
    access_mode: str              # e.g. '5G', 'LTE', 'HSPA+', 'n', 'ac', '' if unknown
    signal_dbm: Optional[float]   # None when not reported / sentinel / stale
    sinr_db: Optional[float]


@dataclass(frozen=True)
class StandbyState:
    """Per-candidate standby tunnel status (SPEC §7.1)."""
    candidate: Optional[BearerClass]
    warming: bool
    ready: bool
    bytes_spent: int
    age_s: float


@dataclass(frozen=True)
class Obs:
    """The ONLY thing a policy receives each tick (SPEC §7.1)."""
    t_s: float
    planes: tuple               # tuple[PlaneState]  (immutable)
    link: LinkTelemetry
    messages: tuple             # tuple[MsgMeta] currently queued (red side)
    bearer_hint: Optional[BearerHint]
    standby: StandbyState
    modem: Optional[ModemStatus] = None   # SPEC §14.1 router-exported hint


@dataclass
class Action:
    """What the policy MAY do (SPEC §7.3). All fields optional / defaulted."""
    # (re)map traffic to planes. Keys may be a msg_id (int) or a data_type (str).
    plane_map: dict = field(default_factory=dict)
    drop: list = field(default_factory=list)                  # msg_ids to drop
    rate_cap_mbps: dict = field(default_factory=dict)         # plane_idx -> cap
    warm_standby: Optional[BearerClass] = None                # begin warming
    cutover: bool = False                                     # switch to ready standby
    teardown_standby: bool = False                            # release standby
    timer_override: dict = field(default_factory=dict)        # {ka_s?,retry_s?,rehandshake_s?}


class Policy:
    """Base policy. ``decide`` is called every tick. Stateless by default;
    subclasses may keep their OWN state (an EWMA, a predictor) but never a
    reference to engine internals."""

    name = "base"

    def reset(self, scenario_name: str, seed: int) -> None:
        """Called once at the start of each run. No engine handle is passed."""
        pass

    def decide(self, obs: Obs) -> Action:
        raise NotImplementedError
