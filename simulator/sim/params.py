"""Named [I] modelling knobs (SPEC Appendix A, I24-I38; SPEC §13.8+).

Every inferred modelling choice introduced by the §13 fixes lives here as ONE
named field with its default and the sweep that brackets it, so the sweep
runner (``eval/sweeps.py``) can override any of them without editing frozen
code. Modules read ``P.<field>`` at use time (never copy it into a module
constant at import), so an override set before a run takes effect.

Nothing here is tuned to make any mechanism win; defaults come from a cited
measurement or are the documented midpoint of the sweep range.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, fields, replace


@dataclass
class Params:
    # ---- episode randomisation (SPEC §13.1) ----------------------- [I24]
    episode_min_s: float = 90.0          # S-EPISODE {60-120, 90-150, 120-240}
    episode_max_s: float = 150.0
    event_pos_lo: float = 0.25           # anchor event uniform in [lo, hi]
    event_pos_hi: float = 0.75
    clock_offset_max_s: float = 86400.0  # observable-clock offset range

    # ---- external router (SPEC §13.1/§13.8) ---------------------- [I25,I26]
    router_failover_hold_s: float = 2.0  # S-ROUTER-HOLD {1, 2, 5}
    router_failback: bool = True         # S-ROUTER-FAILBACK {off, on}
    router_failback_hold_s: float = 10.0 # A good this long -> router back to A

    # ---- QoS planes the operator configured (SPEC §14.4) ----------- [I39]
    # 8 = documented full plane set (default; all results so far). 2 = voice+alert
    # vs everything else; 1 = a single FIFO plane. S-PLANES {1, 2, 8}.
    planes_configured: int = 8

    # ---- anchor-event rule (SPEC §13.8) ---------------------------- [I35]
    anchor_min_persist_s: float = 2.0    # S-ANCHOR {0, 2, 5}

    # ---- handshake physics (SPEC §13.5/§13.8) ------------------ [I27,I28]
    hs_attempt_timeout_s: float = 10.0   # S-HS-TIMEOUT {5, 10, 20}
    hs_rto_s: float = 1.0                # S-HS-RTO {0.5, 1, 5(WireGuard REKEY_TIMEOUT)}
    hs_loss_model: bool = True           # round succeeds w.p. (1-loss)^2

    # ---- documented 10 s retry timer phase (SPEC §13.8) ------------ [I29]
    # 'free'      : free-running 10 s timer, seeded phase U[0, retry_s)
    # 'immediate' : first attempt right at DPD declaration, then every retry_s
    # 'worst'     : first attempt retry_s after the break (the §13.5 reading)
    retry_phase: str = "free"            # S-RETRY-PHASE {immediate, free, worst}

    # ---- capacity direction (SPEC §13.8) --------------------------- [I30]
    # Traffic is uplink-dominant (SPEC §5): capacity = the red->black (UL)
    # direction. Where UL was measured with a saturating test (Starlink iperf,
    # GEO model ul) it is used directly. DL-only / ACK-only-UL datasets apply a
    # UL/DL ratio. Cellular default = Berlin V2X measured saturated medians
    # (A3U UL 20.8 / A3D DL 39.0 Mbps = 0.53, same loop, same operators).
    ul_dl_ratio_cellular: float = 0.53   # S-CAPDIR {0.25, 0.53, 1.0}
    ul_dl_ratio_wifi: float = 1.0        # S-CAPDIR-WIFI {0.5, 1.0}

    # ---- RTT where a trace has none (SPEC §13.8) ------------------- [I31]
    rtt_draw_window_s: float = 14.0      # UCC ping cadence; S-RTT-DRAW {5,14,30}
    rtt_default_scale: float = 1.0       # remaining per-class constants x{0.5,1,2}

    # ---- Starlink RTT/loss attachment (SPEC §13.8) ------------------ [I32]
    # 'adjacent' : each measured second carries its OWN run's nearest
    #              preceding ping window (real, same run, original order)
    # 'pooled'   : seeded draw from the in-session windows of all runs
    leo_rtt_mode: str = "adjacent"       # S-LEO-RTT {adjacent, pooled}

    # ---- GEO rain-fade duration (SPEC §13.8) ----------------------- [I33]
    geo_mean_fade_s: float = 180.0       # S-GEO-FADEDUR {60, 180, 600}
    geo_availability: float = 0.94       # S-GEO {0.94, 0.99, 0.996}

    # ---- UCC G-NetTrack ping-second artefact (SPEC §13.8) ---------- [I34]
    ucc_drop_ping_second: bool = True    # S-UCC-PINGSEC {drop, keep}

    # ---- stall floor (SPEC §12.2) ---------------------------------- [I23]
    stall_goodput_mbps: float = 0.0024   # S-STALL {0, 0.0024, 0.05}


P = Params()
_DEFAULTS = Params()


def set_params(**kw):
    """Override knobs in place (used by eval/sweeps.py workers and tests)."""
    names = {f.name for f in fields(Params)}
    for k, v in kw.items():
        if k not in names:
            raise KeyError(f"unknown param {k!r}")
        setattr(P, k, v)


def reset_params():
    for f in fields(Params):
        setattr(P, f.name, getattr(_DEFAULTS, f.name))


def snapshot() -> dict:
    return {f.name: getattr(P, f.name) for f in fields(Params)}


# ---- evaluator secrets (SPEC §13.8, anti-gaming) --------------------------
# The observable-clock offset is drawn from an EVALUATOR-SECRET salt that is
# not in the repository. If KATIM_EVAL_SALT is unset a fresh per-process random
# salt is used, so the offset of any run is unpredictable from public inputs
# (trace ids, seeds and source code). No metric depends on the offset (metrics
# use true engine time), so baseline results stay bit-reproducible.
_PROCESS_SALT = os.urandom(16).hex()


def eval_salt() -> str:
    return os.environ.get("KATIM_EVAL_SALT") or _PROCESS_SALT


def episode_salt() -> str:
    """Salt for the per-(instance, seed) EPISODE randomisation (window
    position, length, B-leg offset, retry phase). Public by default so train
    results are reproducible; the official held-out evaluation sets
    KATIM_EPISODE_SALT to a secret so held-out episodes cannot be precomputed."""
    return os.environ.get("KATIM_EPISODE_SALT", "")
