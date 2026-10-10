"""Traffic model — SPEC §5 (frozen).

N = 15 responders + 1 command vehicle at an industrial incident. All red-side
flows enter the team gateway, cross the tunnel, exit at command
(uplink-dominant). Message objects carry the metadata a policy may read
(SPEC §7.2); ground-truth value/half-life is hidden and used only by metrics.

Discrete messages (need per-message delivery / deadline / age tracking):
PLI, hazard alerts, voice frames. Fluid rate sources (goodput accounting
only): video, bulk. This split keeps the AoI / alert / voice headline metrics
exact while avoiding millions of tiny objects for elastic traffic.

Message semantics (data_type -> urgency, supersedes, half-life) come from the
deterministic rule table in §5.6 — computable offline today, no Jev / reply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from policies.interface import STATIC_PLANE_MAP

N_RESPONDERS = 15


@dataclass
class TrafficConfig:
    """All rates/sizes cited or [I]+sweep in SPEC §5 / Appendix A."""
    pli_hz: float = 1.0               # [I10] sweep S-PLI {0.2,0.5,1}
    pli_bytes: int = 200              # [I10] sweep 0.1-1 KB
    alert_rate_hz: float = 1.0 / 30   # [I11] sweep S-ALERT-RATE
    alert_bytes: int = 500            # [I11] sweep 0.2-2 KB
    alert_deadline_s: float = 2.0     # [I11] HARD GATE; sweep 1-5 s
    voice_kbps: float = 23.85         # AMR-WB [I12] sweep {MELPe 2.4, AMR-WB}
    voice_talk_s: float = 1.5         # mean talk-spurt
    voice_silence_s: float = 2.0      # mean silence (VAF ~0.4)
    voice_frame_s: float = 0.020      # 20 ms packetisation
    n_voice_nets: int = 1             # half-duplex PTT net, one talker at a time
    video_streams_mbps: tuple = (1.5, 3.0)   # [I13] bodycam + drone
    bulk_offered_mbps: float = 50.0   # greedy background fill (lowest plane)

    # message-semantics rule table (SPEC §5.6) — deterministic, offline.
    @staticmethod
    def semantics(data_type: str):
        return {
            "pli":   dict(urgency="normal",   supersedes=True,  half_life_s=3.0),
            "alert": dict(urgency="high",     supersedes=False, half_life_s=60.0),
            "voice": dict(urgency="realtime", supersedes=False, half_life_s=0.3),
            "video": dict(urgency="normal",   supersedes=False, half_life_s=1.0),
            "bulk":  dict(urgency="background", supersedes=False, half_life_s=1e9),
        }[data_type]


@dataclass
class Message:
    """Engine-side message. Policy sees only the §7.2 subset via MsgMeta."""
    msg_id: int
    data_type: str
    size_bytes: int
    enqueue_t_s: float
    source_id: Optional[int]
    urgency: str
    plane: int                        # current plane (static map; policy remaps)
    deadline_t_s: Optional[float] = None
    supersedes_id: Optional[int] = None
    half_life_s: float = 1e9          # HIDDEN ground truth (metrics only)
    # delivery state (engine only)
    bytes_sent: int = 0
    delivered_t_s: Optional[float] = None
    dropped: bool = False
    superseded: bool = False          # made obsolete by a fresher same-source msg


@dataclass
class FluidSource:
    """Elastic rate source (video / bulk) — goodput accounting only."""
    name: str
    data_type: str
    plane: int
    offered_mbps: float
    offered_bytes: float = 0.0
    delivered_bytes: float = 0.0


def _plane(dtype: str) -> int:
    """Plane for a data type given how many QoS planes the operator configured
    (SPEC §14.4, P.planes_configured): 8 = documented map; 2 = {voice, alert} vs
    rest; 1 = single FIFO plane."""
    from sim.params import P
    n = int(P.planes_configured)
    if n >= 5:
        return STATIC_PLANE_MAP[dtype]
    if n == 1:
        return 0
    return 0 if dtype in ("voice", "alert") else 1


def build_traffic(duration_s: float, seed: int, cfg: TrafficConfig = None):
    """Return (messages, fluid_sources).

    ``messages`` are PLI/alert/voice, sorted by enqueue time; ``fluid_sources``
    are the video streams + one bulk source. Deterministic given (seed).
    """
    cfg = cfg or TrafficConfig()
    rng = np.random.Generator(np.random.PCG64(seed))
    msgs: list[Message] = []
    mid = 0

    # --- PLI: 1 Hz per responder, supersedes same responder's previous ----
    pli_period = 1.0 / cfg.pli_hz
    sem = cfg.semantics("pli")
    last_pli = {r: None for r in range(N_RESPONDERS)}
    for r in range(N_RESPONDERS):
        # small per-responder phase so they don't all fire on the same tick
        phase = rng.random() * pli_period
        t = phase
        while t < duration_s:
            m = Message(msg_id=mid, data_type="pli",
                        size_bytes=cfg.pli_bytes, enqueue_t_s=t, source_id=r,
                        urgency=sem["urgency"], plane=_plane("pli"),
                        supersedes_id=last_pli[r], half_life_s=sem["half_life_s"])
            msgs.append(m)
            last_pli[r] = mid
            mid += 1
            t += pli_period

    # --- hazard alerts: Poisson aggregate ---------------------------------
    sem = cfg.semantics("alert")
    t = 0.0
    while True:
        gap = rng.exponential(1.0 / cfg.alert_rate_hz)
        t += gap
        if t >= duration_s:
            break
        src = int(rng.integers(0, N_RESPONDERS))
        m = Message(msg_id=mid, data_type="alert", size_bytes=cfg.alert_bytes,
                    enqueue_t_s=t, source_id=src, urgency=sem["urgency"],
                    plane=_plane("alert"),
                    deadline_t_s=t + cfg.alert_deadline_s,
                    half_life_s=sem["half_life_s"])
        msgs.append(m)
        mid += 1

    # --- voice: on/off talk-spurts on a single half-duplex PTT net --------
    sem = cfg.semantics("voice")
    frame_bytes = int(round(cfg.voice_kbps * 1000.0 * cfg.voice_frame_s / 8.0))
    frame_bytes = max(frame_bytes, 1)
    t = rng.exponential(cfg.voice_silence_s)   # starts silent
    while t < duration_s:
        talk = rng.exponential(cfg.voice_talk_s)
        talker = int(rng.integers(0, N_RESPONDERS))
        tf = t
        end = min(t + talk, duration_s)
        while tf < end:
            m = Message(msg_id=mid, data_type="voice", size_bytes=frame_bytes,
                        enqueue_t_s=tf, source_id=talker,
                        urgency=sem["urgency"], plane=_plane("voice"),
                        half_life_s=sem["half_life_s"])
            msgs.append(m)
            mid += 1
            tf += cfg.voice_frame_s
        t = end + rng.exponential(cfg.voice_silence_s)

    msgs.sort(key=lambda m: m.enqueue_t_s)

    # --- fluid sources: video streams + bulk ------------------------------
    fluids = []
    for i, rate in enumerate(cfg.video_streams_mbps):
        fluids.append(FluidSource(name=f"video{i}", data_type="video",
                                  plane=_plane("video"),
                                  offered_mbps=float(rate)))
    fluids.append(FluidSource(name="bulk", data_type="bulk",
                              plane=_plane("bulk"),
                              offered_mbps=cfg.bulk_offered_mbps))
    return msgs, fluids
