"""Frozen metric definitions — SPEC §8. Computed from the engine event log.

Exact definitions per §8.1-§8.6. Nothing here is policy-specific; the same
functions score every mechanism. ``compute_metrics`` returns a flat dict of
raw numbers; the scalar score (§8.7) is assembled in ``evaluate.py`` because
it needs the documented-static baseline to normalise against.
"""
from __future__ import annotations

import numpy as np

from sim.gateway import SIM_TICK_S, PROC_DELAY_S

# frozen gate/threshold defaults (SPEC §8.7, §5, CONTEXT §4)
ALERT_HIT_MIN = 0.99
AOI_STALE_S = 5.0
VOICE_MCPTT_MS = 300.0        # 3GPP TS 22.179 mouth-to-ear
VOICE_G114_LIMIT_MS = 400.0   # ITU-T G.114 hard limit
VOICE_FRAME_HARD_FRAC = 0.99  # >=99% of frames within the hard limit
_STALL_MBPS = 0.0024          # deliverable floor (MELPe), matches engine §8.4


class FeasRef:
    """Mechanism-independent FEASIBILITY reference (SPEC §13.3, fix C).

    Perfect-foresight, standby-always-ready, ideal switching to whichever
    bearer can deliver: a per-tick 'max-availability' run over BOTH legs,
    ignoring crypto recovery (pure physics). A message is FEASIBLE if, under
    this reference, SOME path could have delivered it within its deadline. The
    gates (SPEC §8.2/§8.3) are then computed over FEASIBLE messages only, with
    the absolute rates reported alongside (never hidden). Built once per
    (instance, seed), identical for every mechanism, so it cannot flatter one."""

    def __init__(self, deliverable_mbps, one_way_s):
        self.d = deliverable_mbps          # best deliverable rate per tick
        self.ow = one_way_s                # best one-way delay per tick (s)
        self.n = len(deliverable_mbps)

    def _k(self, t):
        return int(t / SIM_TICK_S)

    def alert_feasible(self, enqueue_t, deadline_t):
        k0 = max(0, self._k(enqueue_t))
        k1 = min(self.n - 1, self._k(deadline_t))
        for k in range(k0, k1 + 1):
            if self.d[k] >= _STALL_MBPS and (k * SIM_TICK_S + self.ow[k]) <= deadline_t:
                return True
        return False

    def voice_feasible(self, enqueue_t):
        k = self._k(enqueue_t)
        for kk in range(max(0, k - 2), min(self.n, k + 3)):
            if self.d[kk] >= _STALL_MBPS and \
                    (self.ow[kk] + 0.020) * 1000.0 <= VOICE_G114_LIMIT_MS:
                return True
        return False


def build_feasref(instance, duration_s, seed) -> FeasRef:
    """Max-availability reference arrays from the instance's two real legs
    (instance already prepared with ``seed``). Pure physics: a leg contributes
    its deliverable rate (capacity x (1-loss)) when usable, and its one-way
    delay (rtt/2 + processing); the reference takes the best leg per tick.
    Cached per (instance, prepared seed): it is identical for every mechanism
    (SPEC §13.3), so it is built once and reused across the policies."""
    cache = getattr(instance, "_feasref_cache", None)
    if cache is None:
        cache = {}
        instance._feasref_cache = cache
    key = getattr(instance, "prepared_seed", seed)
    if key in cache:
        return cache[key]
    envs = instance.env_list() if hasattr(instance, "env_list") else None
    n = int(round(duration_s / SIM_TICK_S))
    d = np.zeros(n)
    ow = np.full(n, 1e9)
    for k in range(n):
        env = envs[k] if (envs is not None and k < len(envs)) \
            else instance.env(k * SIM_TICK_S)
        for role in (env.roleA, env.roleB):
            if role is None or role.sample is None or not role.sample.usable:
                continue
            s = role.sample
            dm = s.cap_mbps * (1.0 - min(0.99, s.loss or 0.0))
            if dm <= 0:
                continue
            rtt = s.rtt_ms if s.rtt_ms is not None else 60.0
            owk = rtt / 2.0 / 1000.0 + PROC_DELAY_S
            if dm > d[k]:
                d[k] = dm
            if owk < ow[k]:
                ow[k] = owk
    ref = FeasRef(d, ow)
    cache[key] = ref
    return ref


def compute_metrics(rr, feas: FeasRef = None) -> dict:
    dt = SIM_TICK_S
    t_grid = rr.t_grid
    n = len(t_grid)

    plis = [m for m in rr.messages if m.data_type == "pli"]
    alerts = [m for m in rr.messages if m.data_type == "alert"]
    voice = [m for m in rr.messages if m.data_type == "voice"]

    # --- §8.1 location AoI (mean + p95 over responders x ticks) -----------
    srcs = sorted({m.source_id for m in plis if m.source_id is not None})
    aoi_samples = []
    for r in srcs:
        dl = sorted(((m.delivered_t_s, m.enqueue_t_s) for m in plis
                     if m.source_id == r and m.delivered_t_s is not None),
                    key=lambda x: x[0])
        aoi = np.empty(n)
        j = 0
        freshest_gen = None
        for k in range(n):
            t = t_grid[k]
            while j < len(dl) and dl[j][0] <= t:
                g = dl[j][1]
                if freshest_gen is None or g > freshest_gen:
                    freshest_gen = g
                j += 1
            aoi[k] = (t - freshest_gen) if freshest_gen is not None else t
        aoi_samples.append(aoi)
    if aoi_samples:
        allaoi = np.concatenate(aoi_samples)
        aoi_mean = float(np.mean(allaoi))
        aoi_p95 = float(np.percentile(allaoi, 95))
        aoi_stale_frac = float(np.mean(allaoi > AOI_STALE_S))
    else:
        aoi_mean = aoi_p95 = aoi_stale_frac = 0.0

    # --- §8.2 hazard-alert deadline-hit rate (GATE) -----------------------
    def _alert_hit(m):
        return (m.delivered_t_s is not None and m.deadline_t_s is not None
                and m.delivered_t_s <= m.deadline_t_s)
    if alerts:
        alert_hit = sum(1 for m in alerts if _alert_hit(m)) / len(alerts)
        delivered_alerts = [m for m in alerts if m.delivered_t_s is not None]
        alert_latency_mean = (float(np.mean([m.delivered_t_s - m.enqueue_t_s
                                             for m in delivered_alerts]))
                              if delivered_alerts else float("inf"))
    else:
        alert_hit = 1.0
        alert_latency_mean = 0.0
    # feasible-only (SPEC §13.3): alerts some path could have delivered in time
    if feas is not None and alerts:
        feas_alerts = [m for m in alerts
                       if feas.alert_feasible(m.enqueue_t_s, m.deadline_t_s)]
        n_feas_alert = len(feas_alerts)
        alert_hit_feasible = (sum(1 for m in feas_alerts if _alert_hit(m))
                              / n_feas_alert) if n_feas_alert else 1.0
    else:
        n_feas_alert = len(alerts)
        alert_hit_feasible = alert_hit

    # --- §8.3 voice one-way delay compliance (GATE) -----------------------
    delivered_voice = [(m.delivered_t_s - m.enqueue_t_s) for m in voice
                       if m.delivered_t_s is not None]
    n_voice = len(voice)
    if n_voice:
        owd = np.array(delivered_voice) * 1000.0 if delivered_voice else np.array([])
        voice_median_ms = float(np.median(owd)) if owd.size else float("inf")
        within_mcptt = (np.sum(owd <= VOICE_MCPTT_MS) if owd.size else 0) / n_voice
        within_hard = (np.sum(owd <= VOICE_G114_LIMIT_MS) if owd.size else 0) / n_voice
        voice_delivered_frac = len(delivered_voice) / n_voice
    else:
        voice_median_ms = 0.0
        within_mcptt = within_hard = voice_delivered_frac = 1.0

    # voice gate: median within MCPTT AND >=99% of frames within hard limit
    voice_gate = (voice_median_ms <= VOICE_MCPTT_MS) and \
        (within_hard >= VOICE_FRAME_HARD_FRAC)

    # feasible-only voice (SPEC §13.3): frames a path could deliver within the
    # delay limit at generation time.
    if feas is not None and n_voice:
        feas_voice = [m for m in voice if feas.voice_feasible(m.enqueue_t_s)]
        n_feas_voice = len(feas_voice)
        owd_f = np.array([(m.delivered_t_s - m.enqueue_t_s) * 1000.0
                          for m in feas_voice if m.delivered_t_s is not None])
        voice_median_feasible_ms = float(np.median(owd_f)) if owd_f.size else 0.0
        within_hard_feasible = ((np.sum(owd_f <= VOICE_G114_LIMIT_MS)
                                 / n_feas_voice) if n_feas_voice else 1.0)
        within_mcptt_feasible = ((np.sum(owd_f <= VOICE_MCPTT_MS)
                                  / n_feas_voice) if n_feas_voice else 1.0)
    else:
        n_feas_voice = n_voice
        voice_median_feasible_ms = voice_median_ms
        within_hard_feasible = float(within_hard)
        within_mcptt_feasible = float(within_mcptt)
    voice_gate_feasible = (voice_median_feasible_ms <= VOICE_MCPTT_MS) and \
        (within_hard_feasible >= VOICE_FRAME_HARD_FRAC)

    # --- §8.4 outage seconds per transition -------------------------------
    # rr.outage marks ticks where NO plane can deliver: tunnel dark OR the
    # tunnel is up over ~zero deliverable capacity (engine STALL_GOODPUT_MBPS).
    outage_s_total = float(rr.outage.sum()) * dt
    ntr = max(1, len(rr.transitions))
    # attribute contiguous outage runs to the nearest transition for p95/max
    runs = _contiguous_runs(rr.outage)
    per_trans = {i: 0.0 for i in range(len(rr.transitions))} if rr.transitions else {0: 0.0}
    for (a, b) in runs:
        mid = (t_grid[a] + t_grid[min(b, len(t_grid) - 1)]) / 2.0
        if rr.transitions:
            ti = int(np.argmin([abs(mid - tx) for tx in rr.transitions]))
        else:
            ti = 0
        per_trans[ti] += (b - a + 1) * dt
    pt_vals = np.array(list(per_trans.values())) if per_trans else np.array([0.0])
    outage_per_transition = outage_s_total / ntr
    outage_p95 = float(np.percentile(pt_vals, 95))
    outage_max = float(np.max(pt_vals))

    # decomposition of §8.4 outage (review fix): tunnel-dark vs stall (tunnel
    # up but ~0 deliverable capacity). outage = dark OR stall, disjoint.
    dark = rr.tunnel_dark if rr.tunnel_dark is not None else rr.outage
    stall = rr.stall if rr.stall is not None else np.zeros_like(rr.outage)
    tunnel_dark_s_total = float(np.sum(dark)) * dt
    stall_s_total = float(np.sum(stall)) * dt

    # dropped PTT: talk-spurt frames entirely lost (never delivered)
    dropped_voice_frames = sum(1 for m in voice if m.delivered_t_s is None)

    # --- §8.5 emissions: standby-tx + secondary/satcom active airtime -----
    standby_tx_s = float(rr.standby_tx_s)
    # SPEC §13.8 emissions proxy: airtime on the non-primary / satcom leg,
    # whatever its role (standby warming OR the active tunnel riding B). This
    # is what makes "flee to the standby at t=0 and stay" cost its real
    # emissions; it is the stage-2 lexicographic criterion and the frontier
    # axis. standby_tx_s and secondary_active_s are also reported separately.
    emission_tx_s = float(getattr(rr, "emission_tx_s", standby_tx_s))
    secondary_active_s = float(getattr(rr, "secondary_active_s", 0.0))
    overhead_satcom = int(rr.overhead_bytes_satcom)
    overhead_total = int(rr.overhead_bytes_total)

    # --- §8.6 video goodput (secondary) -----------------------------------
    vid_off = sum(fs.offered_bytes for fs in rr.fluids if fs.data_type == "video")
    vid_del = sum(fs.delivered_bytes for fs in rr.fluids if fs.data_type == "video")
    video_goodput = (vid_del / vid_off) if vid_off > 0 else 1.0

    dropped_pli_superseded = sum(1 for m in plis if m.dropped)
    stale_pli_delivered = sum(1 for m in plis if m.superseded
                              and m.delivered_t_s is not None)

    return dict(
        inst_id=rr.inst_id, scenario=rr.scenario, seed=rr.seed, policy=rr.policy,
        aoi_mean_s=aoi_mean, aoi_p95_s=aoi_p95, aoi_stale_frac=aoi_stale_frac,
        alert_hit_rate=alert_hit, alert_latency_mean_s=alert_latency_mean,
        voice_median_ms=voice_median_ms, voice_within_mcptt=float(within_mcptt),
        voice_within_hard=float(within_hard),
        voice_delivered_frac=float(voice_delivered_frac), voice_gate=bool(voice_gate),
        alert_hit_rate_feasible=float(alert_hit_feasible),
        n_feasible_alerts=int(n_feas_alert),
        voice_median_feasible_ms=float(voice_median_feasible_ms),
        voice_within_hard_feasible=float(within_hard_feasible),
        voice_within_mcptt_feasible=float(within_mcptt_feasible),
        voice_gate_feasible=bool(voice_gate_feasible),
        n_feasible_voice=int(n_feas_voice),
        outage_s_total=outage_s_total, outage_per_transition_s=outage_per_transition,
        outage_p95_s=outage_p95, outage_max_s=outage_max,
        tunnel_dark_s_total=tunnel_dark_s_total, stall_s_total=stall_s_total,
        tunnel_dark_per_transition_s=tunnel_dark_s_total / ntr,
        stall_per_transition_s=stall_s_total / ntr,
        dropped_voice_frames=int(dropped_voice_frames),
        standby_tx_s=standby_tx_s, emission_tx_s=emission_tx_s,
        secondary_active_s=secondary_active_s,
        overhead_bytes_satcom=overhead_satcom,
        overhead_bytes_total=overhead_total,
        video_goodput=float(video_goodput),
        dropped_pli_superseded=int(dropped_pli_superseded),
        stale_pli_delivered=int(stale_pli_delivered),
        invalid=bool(rr.invalid_reason), invalid_reason=rr.invalid_reason,
        n_transitions=len(rr.transitions),
    )


def gates_pass(m: dict, feasible: bool = True) -> bool:
    """§8.2 + §8.3 gates. By default computed over FEASIBLE messages (SPEC
    §13.3) — a mechanism is not failed for a message no path could have
    delivered. ``feasible=False`` returns the absolute-rate gate (reported
    alongside, never hidden)."""
    if feasible:
        return (m["alert_hit_rate_feasible"] >= ALERT_HIT_MIN) \
            and m["voice_gate_feasible"] and not m["invalid"]
    return (m["alert_hit_rate"] >= ALERT_HIT_MIN) and m["voice_gate"] \
        and not m["invalid"]


def gate_shortfall(m: dict) -> float:
    """Non-negative distance from passing the FEASIBLE gates (SPEC §13.3), for
    the gradient score. 0 exactly when the feasible gates pass."""
    s = max(0.0, ALERT_HIT_MIN - m["alert_hit_rate_feasible"])
    s += max(0.0, VOICE_FRAME_HARD_FRAC - m["voice_within_hard_feasible"])
    s += max(0.0, (m["voice_median_feasible_ms"] - VOICE_MCPTT_MS) / 1000.0)
    if m["invalid"]:
        s += 1.0
    return float(s)


def _contiguous_runs(mask):
    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs
