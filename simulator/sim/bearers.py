"""Bearers — trace replay (zero-order hold) + GEO model + stitching.

SPEC §2 (time model), §3 (bearers), §13.2 and §13.8 (realism fixes). Frozen.

Key rules enforced here:
  * Replay is zero-order hold, per field, never interpolation (SPEC §2.2).
  * Each field has a max hold horizon; past it the field is ABSENT (SPEC §2.3).
  * A throughput gap > H_tput makes the whole bearer UNUSABLE for those ticks
    (modelled as an underlay outage: no bytes move) (SPEC §2.3).
  * GEO is a measurement-parameterised model, never presented as a replay
    (SPEC §3.6), driven by a seeded two-state (clear/fade) rain process.

SPEC §13.8 realism fixes implemented here:
  * CAPACITY DIRECTION [I30]. The mission traffic is uplink-dominant (team
    gateway -> command, SPEC §5), so a bearer's capacity is its red->black
    (UPLINK) capacity. Where UL was measured with a saturating test it is used
    directly (Starlink iperf ``upload_mbps``; the GEO model's ``ul``). In the
    UCC 4G/5G Download captures and HSR-MPTCP the logged UL is the ACK stream of
    a download (UCC 4G median 0.107 Mbps), i.e. DEMAND not capacity — using it
    would repeat the §12.1 category error — and Lumos5G / Meireles log DL only.
    For those, UL capacity = measured DL x a documented UL/DL ratio (cellular
    default 0.53 = Berlin V2X saturated LTE medians, UL 20.8 / DL 39.0 Mbps on
    the same loop; Wi-Fi 1.0), swept by S-CAPDIR. The handshake serialises at
    min(DL, UL) (SPEC §4.3) via ``cap_dl_mbps``.
  * UCC G-NetTrack PING-SECOND ARTEFACT [I34]. The UCC logger runs a ping every
    ~14 s; the throughput logged in that second (and its duplicated row) is a
    measurement artefact (77-86 % of the "collapses" in UCC 5G Download fall
    on a ping second vs ~21 % by chance). The ping second's throughput is
    removed (left ABSENT, so ordinary §2.2 zero-order hold within H_tput
    applies — nothing is invented) while its RTT/loss are kept, and duplicated
    same-second rows are collapsed (last real value per field).
  * RTT [I31]. Telemetry fields a dataset never measured are never left as a
    silent constant that fingerprints the dataset: UCC 5G RTT is sparse
    (H_RTT hold of its own pings); before its first ping, and for datasets with
    no RTT at all (Lumos5G, UCC 4G, HSR-MPTCP sparse ACK RTT), RTT/loss are a
    LABELLED SEEDED DRAW from the measured UCC 5G Download ping samples,
    conditioned on the row's access mode (5G / LTE / HSPA), one draw per ping
    cadence window (14 s), paired RTT+loss from the same ping sample. Only the
    Wi-Fi leg (Meireles; no RTT in any Wi-Fi capture) keeps a per-class
    default constant, scaled by S-RTT-DEFAULT.
"""
from __future__ import annotations

import glob
import hashlib
import os
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from sim.params import P

# ---- frozen time constants (SPEC §2.1, §2.3) -----------------------------
SIM_TICK_S = 0.1
H_TPUT = 2.0      # throughput / fast telemetry hold horizon (s)   [I2]
H_RTT = 120.0     # sparse telemetry (LEO/UCC ping) hold horizon (s) [I3]
H_POSE = 5.0      # signal / pose hold horizon (s)                 [I3]

PROCESSED_ROOT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "traces", "processed",
)

# datasets whose own RTT samples are sparse pings (H_RTT hold applies)
_SPARSE_RTT_DATASETS = {"starlink_road", "ucc5g"}
# G-NetTrack Pro captures (ping-second artefact + duplicated-second rows)
_GNETTRACK_DATASETS = {"ucc5g", "ucc4g"}
# datasets whose UL column is a MEASURED saturating capacity
_UL_MEASURED_DATASETS = {"starlink_road"}

# per-class default RTT (ms) used ONLY where no measured or drawn RTT exists
# (Wi-Fi: no Wi-Fi capture measures RTT). [I31] sweep S-RTT-DEFAULT (scale).
_DEFAULT_RTT_MS = {"WiFi": 20.0, "5G": 72.0, "LTE": 84.0,
                   "cellular-subflow": 84.0, "LEO": 50.0, "GEO": 600.0,
                   "unknown": 84.0}

# capacity-field name kept for backward compatibility of the constructor
CAPACITY_FIELD_DEFAULT = "auto"


def default_rtt_ms(bclass: str) -> float:
    return _DEFAULT_RTT_MS.get(bclass, 84.0) * float(P.rtt_default_scale)


def ul_dl_ratio(bclass: str) -> float:
    if bclass == "WiFi":
        return float(P.ul_dl_ratio_wifi)
    return float(P.ul_dl_ratio_cellular)


@dataclass
class BearerSample:
    """What a bearer presents at one tick after zero-order hold + horizons."""
    usable: bool              # throughput present within horizon => link alive
    cap_mbps: float           # UPLINK-direction capacity this tick (0 if unusable)
    dl_mbps: Optional[float]
    ul_mbps: Optional[float]
    rtt_ms: Optional[float]
    loss: Optional[float]
    rtt_age_s: float          # staleness of the freshest MEASURED RTT sample
    net_mode: str
    cell_id: str
    rsrp_dbm: Optional[float]
    sinr_db: Optional[float]
    speed_kmh: Optional[float]
    lat: Optional[float]
    lon: Optional[float]
    cap_dl_mbps: Optional[float] = None   # DL-direction capacity (handshake min)
    rtt_src: str = "measured"             # measured | held | draw | model | default


class _FieldSeries:
    """One field's real (non-empty) samples, for zero-order-hold lookup."""

    __slots__ = ("times", "values", "is_str")

    def __init__(self, times: np.ndarray, values, is_str: bool):
        self.times = times
        self.values = values
        self.is_str = is_str

    def at(self, t: float, horizon: float):
        """Return (value, age_s). value is None if absent/stale.

        Zero-order hold: the most recent real sample at or before ``t``. Never
        reads a future sample; never interpolates. If the newest real sample
        is older than ``horizon`` the field is absent (age still returned).
        """
        n = self.times.shape[0]
        if n == 0:
            return None, float("inf")
        idx = int(np.searchsorted(self.times, t, side="right")) - 1
        if idx < 0:
            return None, float("inf")
        age = t - float(self.times[idx])
        if age > horizon:
            return None, age
        return self.values[idx], age


class Bearer:
    """Common interface: ``sample(t_s) -> BearerSample`` and ``bclass``."""
    bclass = "unknown"

    def sample(self, t_s: float) -> BearerSample:  # pragma: no cover
        raise NotImplementedError

    @property
    def duration_s(self) -> float:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# measured RTT pools (SPEC §13.8 [I31])
# ---------------------------------------------------------------------------
_POOL_CACHE = {}


def _mode_group(mode: str) -> str:
    m = (mode or "").upper()
    if m in ("5G", "CONNECTED", "NR"):
        return "5G"
    if m in ("HSPA+", "HSDPA", "HSUPA", "UMTS", "WCDMA", "EDGE", "GPRS"):
        return "HSPA"
    return "LTE"


def ucc5g_ping_pool():
    """{mode_group: (rtt_ms[], loss[])} — every measured ping sample of the
    UCC 5G Download captures (paired RTT/loss rows). Cached."""
    if "ucc5g" not in _POOL_CACHE:
        pools = {}
        files = sorted(glob.glob(os.path.join(PROCESSED_ROOT, "ucc5g",
                                              "Download__*.csv")))
        parts = []
        for f in files:
            try:
                d = pd.read_csv(f, usecols=["net_mode", "rtt_ms", "loss"],
                                dtype={"net_mode": str})
            except (OSError, ValueError):
                continue
            d = d[pd.to_numeric(d["rtt_ms"], errors="coerce").notna()]
            parts.append(d)
        if parts:
            d = pd.concat(parts, ignore_index=True)
            d["g"] = d["net_mode"].astype(str).map(_mode_group)
            for g, sub in d.groupby("g"):
                rtt = pd.to_numeric(sub["rtt_ms"], errors="coerce").to_numpy()
                loss = pd.to_numeric(sub["loss"], errors="coerce").fillna(
                    0.0).to_numpy()
                pools[g] = (rtt, loss)
        _POOL_CACHE["ucc5g"] = pools
    return _POOL_CACHE["ucc5g"]


def _stable_seed(*parts) -> int:
    h = hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()
    return int(h, 16) & ((1 << 63) - 1)


class TraceReplayBearer(Bearer):
    """Replays one processed CSV as a bearer, zero-order hold per field."""

    def __init__(self, csv_path: str, dataset: str, trace_id: str,
                 capacity_field: str = CAPACITY_FIELD_DEFAULT,
                 df: "pd.DataFrame" = None, cap_ceiling_mbps: float = None,
                 rtt_seed: int = 0, rtt_pool: dict = None):
        self.csv_path = csv_path
        self.dataset = dataset
        self.trace_id = trace_id
        # gateway throughput ceiling (SPEC §13.6): X9000R <=100 Mbps IMIX
        # (X:41); 9001-R <=1 Gbps (R:53). Applied to delivered capacity.
        self.cap_ceiling_mbps = cap_ceiling_mbps
        if df is None:
            df = pd.read_csv(csv_path, dtype={"net_mode": str, "cell_id": str,
                                              "bearer": str, "ts_utc": str},
                             low_memory=False)
        df = df.sort_values("t_s", kind="stable").reset_index(drop=True)
        self.bclass = str(df["bearer"].iloc[0]) if len(df) else "unknown"
        self.n_ping_seconds_removed = 0
        if dataset in _GNETTRACK_DATASETS:
            df = self._clean_gnettrack(df)
        self._t = df["t_s"].to_numpy(dtype=float)
        self._dur = float(self._t[-1]) if len(self._t) else 0.0
        self.sparse_rtt = dataset in _SPARSE_RTT_DATASETS
        if capacity_field == "auto":
            capacity_field = ("ul_measured" if dataset in _UL_MEASURED_DATASETS
                              else "dl_ratio")
        self.capacity_field = capacity_field

        def numeric_series(col):
            if col not in df.columns:
                return _FieldSeries(np.empty(0), np.empty(0), False)
            vals = pd.to_numeric(df[col], errors="coerce").to_numpy(dtype=float)
            mask = ~np.isnan(vals)
            return _FieldSeries(self._t[mask], vals[mask], False)

        def str_series(col):
            if col not in df.columns:
                return _FieldSeries(np.empty(0), np.empty(0, dtype=object), True)
            raw = df[col].astype(str)
            mask = (raw != "") & (raw != "nan")
            return _FieldSeries(self._t[mask.to_numpy()],
                                raw[mask].to_numpy(dtype=object), True)

        self._f = {
            "dl_mbps": numeric_series("dl_mbps"),
            "ul_mbps": numeric_series("ul_mbps"),
            "rtt_ms": numeric_series("rtt_ms"),
            "loss": numeric_series("loss"),
            "rsrp_dbm": numeric_series("rsrp_dbm"),
            "sinr_db": numeric_series("sinr_db"),
            "speed_kmh": numeric_series("speed_kmh"),
            "lat": numeric_series("lat"),
            "lon": numeric_series("lon"),
            "net_mode": str_series("net_mode"),
            "cell_id": str_series("cell_id"),
        }
        # RTT draw source (SPEC §13.8 [I31]): own pings if the trace has a
        # usable set of them, else the mode-conditioned UCC 5G ping pool;
        # Wi-Fi keeps its per-class default (no measured Wi-Fi RTT exists).
        own = self._f["rtt_ms"]
        self.rtt_seed = int(rtt_seed)
        self._draw_pool = None
        if rtt_pool is not None:
            self._draw_pool = rtt_pool
            self.rtt_draw_source = "given pool"
        elif self.bclass == "WiFi":
            self.rtt_draw_source = "per-class default (no Wi-Fi RTT measured)"
        elif own.times.shape[0] >= 20 and dataset != "starlink_road":
            loss_own = self._f["loss"]
            lv = np.array([loss_own.at(float(tt), 0.0)[0] or 0.0
                           for tt in own.times]) if loss_own.times.size else \
                np.zeros(own.times.shape[0])
            self._draw_pool = {"*": (own.values.astype(float), lv)}
            self.rtt_draw_source = "own-trace ping samples (seeded draw)"
        elif own.times.shape[0] == 0 or own.times.shape[0] < 20:
            if dataset != "starlink_road":
                self._draw_pool = ucc5g_ping_pool()
                self.rtt_draw_source = ("UCC 5G Download ping samples by "
                                        "access mode (seeded draw)")
            else:
                self.rtt_draw_source = "attached ping windows"
        self._draw_cache = {}

    # ------------------------------------------------------------------
    def _clean_gnettrack(self, df):
        """SPEC §13.8 [I34]: drop the throughput of every ping-logging second
        (keep its RTT/loss) and collapse duplicated same-second rows to the
        last real value per field. Never fills: removed seconds are ABSENT."""
        if "rtt_ms" in df.columns and P.ucc_drop_ping_second:
            rtt = pd.to_numeric(df["rtt_ms"], errors="coerce")
            ping_secs = set(df.loc[rtt.notna(), "t_s"].astype(float).tolist())
            if ping_secs:
                m = df["t_s"].astype(float).isin(ping_secs)
                for c in ("dl_mbps", "ul_mbps"):
                    if c in df.columns:
                        df.loc[m, c] = np.nan
                self.n_ping_seconds_removed = len(ping_secs)
        if df["t_s"].duplicated().any():
            df = df.groupby("t_s", sort=True, as_index=False).last()
        return df

    @classmethod
    def from_frame(cls, df: "pd.DataFrame", dataset: str, trace_id: str,
                   capacity_field: str = CAPACITY_FIELD_DEFAULT, **kw):
        """Build a replay bearer from an in-memory unified-schema frame (used
        by the Starlink measured-seconds concatenation, SPEC §13.2). The frame
        is real data re-indexed only — never interpolated or filled."""
        return cls("<in-memory>", dataset, trace_id, capacity_field, df=df, **kw)

    @property
    def duration_s(self) -> float:
        return self._dur

    def net_mode_at(self, t_s: float) -> str:
        v, _ = self._f["net_mode"].at(t_s, H_POSE)
        return v if v is not None else ""

    # ------------------------------------------------------------------
    def _drawn_rtt(self, t_s: float, mode: str):
        """Labelled seeded draw (SPEC §13.8 [I31]): one ping sample per
        cadence window, from the pool of this row's access-mode group."""
        pool = self._draw_pool
        if not pool:
            return None, None
        g = "*" if "*" in pool else _mode_group(mode)
        if g not in pool:
            g = "LTE" if "LTE" in pool else next(iter(pool))
        rtt, loss = pool[g]
        if rtt.size == 0:
            return None, None
        w = max(1.0, float(P.rtt_draw_window_s))
        k = int(np.floor(t_s / w))
        key = (g, k, w)
        j = self._draw_cache.get(key)
        if j is None:
            j = _stable_seed("rttdraw", self.dataset, self.trace_id,
                             self.rtt_seed, g, k, w) % rtt.size
            self._draw_cache[key] = j
        return float(rtt[j]), float(loss[j])

    def capacity_at(self, dl, ul):
        """UL-direction capacity from the raw fields (SPEC §13.8 [I30])."""
        if self.capacity_field == "ul_measured":
            cap = ul
        elif self.capacity_field == "dl_mbps":      # legacy / tests
            cap = dl
        elif self.capacity_field == "ul_mbps":
            cap = ul
        else:                                      # dl_ratio
            cap = None if dl is None else float(dl) * ul_dl_ratio(self.bclass)
        if cap is None:
            return None
        cap = float(cap)
        if self.cap_ceiling_mbps is not None:
            cap = min(cap, float(self.cap_ceiling_mbps))
        return cap

    def sample(self, t_s: float) -> BearerSample:
        rtt_h = H_RTT if self.sparse_rtt else H_TPUT
        dl, _ = self._f["dl_mbps"].at(t_s, H_TPUT)
        ul, _ = self._f["ul_mbps"].at(t_s, H_TPUT)
        rtt, rtt_age = self._f["rtt_ms"].at(t_s, rtt_h)
        loss, _ = self._f["loss"].at(t_s, rtt_h)
        rsrp, _ = self._f["rsrp_dbm"].at(t_s, H_POSE)
        sinr, _ = self._f["sinr_db"].at(t_s, H_POSE)
        spd, _ = self._f["speed_kmh"].at(t_s, H_POSE)
        lat, _ = self._f["lat"].at(t_s, H_POSE)
        lon, _ = self._f["lon"].at(t_s, H_POSE)
        nm, _ = self._f["net_mode"].at(t_s, H_POSE)
        cid, _ = self._f["cell_id"].at(t_s, H_POSE)

        cap = self.capacity_at(dl, ul)
        # Bearer-level gap rule (SPEC §2.3): capacity absent => UNUSABLE.
        usable = cap is not None
        if not usable:
            cap = 0.0
        cap_dl = dl
        if cap_dl is not None and self.cap_ceiling_mbps is not None:
            cap_dl = min(float(cap_dl), float(self.cap_ceiling_mbps))
        src = "measured"
        if rtt is not None:
            src = "measured" if rtt_age <= 1.0 else "held"
        else:
            r2, l2 = self._drawn_rtt(t_s, nm or "")
            if r2 is not None:
                rtt, src = r2, "draw"
                if loss is None:
                    loss = l2
            else:
                rtt, src = default_rtt_ms(self.bclass), "default"
        # SPEC §13.8: a drawn/default RTT is a CURRENT synthetic estimate, not a
        # stale measurement, and a missing measurement must not leak a 1e9 age
        # that fingerprints the dataset. Report a bounded age: the real staleness
        # for a measured/held value, 0 for a fresh draw/default/model value.
        if src in ("draw", "default"):
            out_age = 0.0
        elif rtt_age == float("inf"):
            out_age = H_RTT
        else:
            out_age = min(rtt_age, H_RTT)
        return BearerSample(
            usable=usable, cap_mbps=float(cap),
            dl_mbps=dl, ul_mbps=ul, rtt_ms=rtt, loss=loss,
            rtt_age_s=out_age,
            net_mode=nm or "", cell_id=cid or "",
            rsrp_dbm=rsrp, sinr_db=sinr, speed_kmh=spd, lat=lat, lon=lon,
            cap_dl_mbps=cap_dl, rtt_src=src,
        )

    def capacity_series(self):
        """(t[], cap[]) at every real sample time: the UL-direction capacity
        the gateway actually sees (cleaned, ratio, ceiling). NaN where the
        capacity field is absent at that sample. Used by the anchor-event rule
        (SPEC §13.8) so events are extracted on deliverable capacity."""
        src = (self._f["ul_mbps"] if self.capacity_field in
               ("ul_measured", "ul_mbps") else self._f["dl_mbps"])
        t = self._t
        cap = np.full(t.shape[0], np.nan)
        if src.times.size:
            idx = np.searchsorted(src.times, t, side="left")
            ok = (idx < src.times.size)
            ok[ok] = src.times[idx[ok]] == t[ok]
            vals = np.full(t.shape[0], np.nan)
            vals[ok] = src.values[idx[ok]]
            if self.capacity_field == "dl_ratio":
                vals = vals * ul_dl_ratio(self.bclass)
            if self.cap_ceiling_mbps is not None:
                vals = np.minimum(vals, float(self.cap_ceiling_mbps))
            cap = vals
        return t, cap


@dataclass
class GeoParams:
    """Frozen GEO model inputs (SPEC §3.6 table, §12.4, §13.8). Swept by S-GEO.

    ``availability`` is the steady-state probability that the link is UP
    (IIUM SES-12: 94 % in a severe-rain climate). §13.8 [I33]: rain fades are
    minutes-scale (mean ``mean_fade_s`` = 180 s; ITU-R P.1623 fade-duration
    statistics put Ku-band fades of interest at tens of seconds to many
    minutes) and the process starts each episode from its STATIONARY state, so
    availability holds ACROSS episodes: most 90-150 s episodes see clear sky,
    a few sit inside a long fade — instead of the §12.4 8 s fades that put a
    short GEO outage inside nearly every episode.
    """
    nominal_rtt_ms: float = 600.0
    fade_rtt_ceiling_ms: float = 3000.0
    availability: float = None        # None -> P.geo_availability     [I8]
    dl_mbps: float = 18.0
    ul_mbps: float = 3.0
    base_loss: float = 0.005
    fade_loss_ceiling: float = 0.20
    mean_fade_s: float = None         # None -> P.geo_mean_fade_s      [I33]
    outage_share_of_fade: float = 0.5  # [I22] sweep S-GEO {0.25, 0.5, 1.0}

    def __post_init__(self):
        if self.availability is None:
            self.availability = float(P.geo_availability)
        if self.mean_fade_s is None:
            self.mean_fade_s = float(P.geo_mean_fade_s)


_GEO_DEPTH_A, _GEO_DEPTH_B = 1.5, 2.0   # heavy-tailed fade depth ~ Beta(1.5, 2)


class GeoModelBearer(Bearer):
    """Measurement-parameterised GEO bearer (NOT a replay) — SPEC §3.6.

    A seeded rain-fade episode process is precomputed at tick resolution, so
    ``sample(t)`` is deterministic and idempotent. Fade episodes are
    contiguous; each has one depth; episodes deeper than the outage threshold
    are UNUSABLE for their whole duration, shallower ones are degraded-but-up.
    The initial state is drawn from the stationary distribution so the
    realised DOWN fraction equals ``1 - availability`` in expectation across
    episodes (unit-tested). Capacity presented = UPLINK (3 Mbps, SPEC §13.8).
    """
    bclass = "GEO"

    def __init__(self, duration_s: float, seed: int, params: GeoParams = None):
        self.params = params or GeoParams()
        self._dur = float(duration_s)
        p = self.params
        n = int(np.ceil(duration_s / SIM_TICK_S)) + 2
        rng = np.random.Generator(np.random.PCG64(seed))
        down_ss = min(0.5, max(1e-4, 1.0 - p.availability))
        share = min(1.0, max(1e-3, p.outage_share_of_fade))
        fade_ss = min(0.95, down_ss / share)          # steady-state P(fade)
        depth_thr = _beta_ppf(1.0 - share, _GEO_DEPTH_A, _GEO_DEPTH_B)
        mean_fade_ticks = max(1.0, p.mean_fade_s / SIM_TICK_S)
        p_fade_to_clear = 1.0 / mean_fade_ticks
        p_clear_to_fade = p_fade_to_clear * fade_ss / (1.0 - fade_ss)
        u0 = rng.random()
        u_state = rng.random(n)
        u_depth = rng.random(n)
        jitter = rng.random(n)
        state_fade = np.zeros(n, dtype=bool)
        depth = np.zeros(n)
        # stationary start (SPEC §13.8): in a fade already with P = fade_ss
        fade = bool(u0 < fade_ss)
        ep_depth = _beta_ppf(rng.random(), _GEO_DEPTH_A, _GEO_DEPTH_B) if fade else 0.0
        for i in range(n):
            if fade:
                if u_state[i] < p_fade_to_clear:
                    fade = False
            else:
                if u_state[i] < p_clear_to_fade:
                    fade = True
                    ep_depth = _beta_ppf(u_depth[i], _GEO_DEPTH_A, _GEO_DEPTH_B)
            state_fade[i] = fade
            depth[i] = ep_depth if fade else 0.0
        self._rtt = np.where(
            state_fade,
            p.nominal_rtt_ms + depth * (p.fade_rtt_ceiling_ms - p.nominal_rtt_ms),
            p.nominal_rtt_ms * (0.95 + 0.1 * jitter),
        )
        self._loss = np.where(
            state_fade,
            p.base_loss + depth * (p.fade_loss_ceiling - p.base_loss),
            p.base_loss,
        )
        self._down = state_fade & (depth > depth_thr)
        self._dl = np.where(state_fade, p.dl_mbps * (1.0 - 0.6 * depth), p.dl_mbps)
        self._ul = np.where(state_fade, p.ul_mbps * (1.0 - 0.6 * depth), p.ul_mbps)
        self._n = n

    @property
    def duration_s(self) -> float:
        return self._dur

    @property
    def down_fraction(self) -> float:
        return float(np.mean(self._down))

    def sample(self, t_s: float) -> BearerSample:
        i = int(t_s / SIM_TICK_S)
        if i < 0:
            i = 0
        if i >= self._n:
            i = self._n - 1
        down = bool(self._down[i])
        cap = 0.0 if down else float(self._ul[i])
        return BearerSample(
            usable=not down, cap_mbps=cap,
            dl_mbps=None if down else float(self._dl[i]),
            ul_mbps=None if down else float(self._ul[i]),
            rtt_ms=float(self._rtt[i]), loss=float(self._loss[i]),
            rtt_age_s=0.0, net_mode="GEO", cell_id="geo-sat",
            rsrp_dbm=None, sinr_db=None, speed_kmh=None, lat=None, lon=None,
            cap_dl_mbps=None if down else float(self._dl[i]), rtt_src="model",
        )


def _beta_ppf(q: float, a: float, b: float) -> float:
    """Inverse CDF of Beta(a, b) by table lookup (deterministic, no SciPy)."""
    q = min(1.0, max(0.0, float(q)))
    xs, cs = _beta_cdf_table(a, b)
    return float(np.interp(q, cs, xs))


_BETA_CACHE = {}


def _beta_cdf_table(a: float, b: float):
    key = (a, b)
    if key not in _BETA_CACHE:
        xs = np.linspace(0.0, 1.0, 20001)
        pdf = np.power(xs, a - 1.0, where=xs > 0, out=np.zeros_like(xs)) * \
            np.power(1.0 - xs, b - 1.0)
        c = np.concatenate([[0.0], np.cumsum((pdf[1:] + pdf[:-1]) / 2.0 *
                                             np.diff(xs))])
        c /= c[-1]
        _BETA_CACHE[key] = (xs, c)
    return _BETA_CACHE[key]


# ---------------------------------------------------------------------------
# Starlink measured-seconds concatenation (SPEC §13.2 fix B, §13.8)
# ---------------------------------------------------------------------------
# iperf throughput is logged only in ~15 s bursts every ~55 s; the unmeasured
# between-burst seconds are a MEASUREMENT gap, not a link outage. The LEO
# capacity leg CONCATENATES the measured seconds of one drive run in their
# original order, removing the unmeasured gaps (time-compressed). Never filled.
# §13.8: capacity = measured UPLINK (iperf upload, the uplink-dominant
# direction); a "measured second" is a second whose upload was measured.
# RTT/loss [I32]: each measured second carries its OWN run's ping window that
# is adjacent in time (the most recent window that started at or before it;
# before the first window, the run's first window) — real values from the same
# run, in original order, preserving the obstruction correlation between
# throughput and RTT. Runs with no in-session windows fall back to a seeded
# draw from the in-session windows of the campaign (labelled).
STARLINK_PING_WINDOWS = os.path.join(PROCESSED_ROOT, "starlink_road",
                                     "ping_windows.csv")
LEO_PING_DRAW_WINDOW_S = 15.0   # fallback draw: one value per burst (~15 s)
_PING_CACHE = {}


def _ping_windows():
    if "pw" not in _PING_CACHE:
        try:
            pw = pd.read_csv(STARLINK_PING_WINDOWS)
            pw["rtt_ms"] = pd.to_numeric(pw.get("rtt_ms"), errors="coerce")
            pw["loss"] = pd.to_numeric(pw.get("loss"), errors="coerce")
            pw["t_s"] = pd.to_numeric(pw.get("t_s"), errors="coerce")
        except OSError:
            pw = pd.DataFrame(columns=["session_trace_id", "t_s", "rtt_ms",
                                       "loss"])
        _PING_CACHE["pw"] = pw
    return _PING_CACHE["pw"]


def leo_concatenated_frame(csv_path: str, seed: int, trace_id: str = None
                           ) -> "pd.DataFrame":
    """Unified-schema frame = the measured (upload) seconds of this Starlink
    run concatenated in original order with unmeasured gaps removed; RTT/loss
    from the run's own adjacent ping windows (SPEC §13.8 [I32]). Pure
    re-indexing of real data: no interpolation, no fill, no invented samples."""
    df = pd.read_csv(csv_path, dtype={"net_mode": str, "cell_id": str,
                                      "bearer": str, "ts_utc": str},
                     low_memory=False)
    df = df.sort_values("t_s", kind="stable").reset_index(drop=True)
    ul = pd.to_numeric(df.get("ul_mbps"), errors="coerce").to_numpy()
    keep = ~np.isnan(ul)                       # a measured capacity second
    df = df.loc[keep].reset_index(drop=True)
    n = len(df)
    t_orig = df["t_s"].to_numpy(dtype=float)
    df["t_s"] = np.arange(n, dtype=float)      # concatenated clock
    tid = trace_id or os.path.basename(csv_path)[:-4]
    pw = _ping_windows()
    own = pw[(pw["session_trace_id"] == tid) & pw["t_s"].notna()
             & pw["rtt_ms"].notna()].sort_values("t_s")
    rtt_col = np.full(n, np.nan)
    loss_col = np.full(n, np.nan)
    mode = P.leo_rtt_mode
    if mode == "adjacent" and len(own):
        ws = own["t_s"].to_numpy(dtype=float)
        wr = own["rtt_ms"].to_numpy(dtype=float)
        wl = own["loss"].fillna(0.0).to_numpy(dtype=float)
        idx = np.searchsorted(ws, t_orig, side="right") - 1
        idx = np.clip(idx, 0, len(ws) - 1)
        rtt_col[:] = wr[idx]
        loss_col[:] = wl[idx]
        src = "adjacent same-run ping window"
    else:
        insess = pw[pw["session_trace_id"].notna() & pw["rtt_ms"].notna()]
        rr = insess["rtt_ms"].to_numpy(dtype=float)
        ll = insess["loss"].fillna(0.0).to_numpy(dtype=float)
        rng = np.random.Generator(np.random.PCG64(seed))
        w = int(round(LEO_PING_DRAW_WINDOW_S))
        if rr.size:
            for start in range(0, n, w):
                j = int(rng.integers(0, rr.size))
                rtt_col[start:start + w] = rr[j]
                loss_col[start:start + w] = ll[j]
        src = "seeded draw from in-session ping windows"
    df["rtt_ms"] = rtt_col
    df["loss"] = loss_col
    df["bearer"] = "LEO"
    df.attrs["rtt_source"] = src
    return df


def build_leo_concatenated(csv_path: str, trace_id: str, seed: int):
    """A TraceReplayBearer over the concatenated measured seconds (fix B)."""
    frame = leo_concatenated_frame(csv_path, seed, trace_id)
    b = TraceReplayBearer.from_frame(frame, "starlink_road", trace_id)
    b.concatenated = True       # labelling flag surfaced in cards/figures
    b.rtt_draw_source = frame.attrs.get("rtt_source", "")
    return b


# ---------------------------------------------------------------------------
# Trace discovery (SPEC §10.3): auto-discover ALL processed datasets.
# ---------------------------------------------------------------------------
def discover_traces():
    """Return {dataset: {trace_id: csv_path}} for every processed dataset."""
    out = {}
    if not os.path.isdir(PROCESSED_ROOT):
        return out
    for ds in sorted(os.listdir(PROCESSED_ROOT)):
        dsdir = os.path.join(PROCESSED_ROOT, ds)
        if not os.path.isdir(dsdir):
            continue
        traces = {}
        for csv in sorted(glob.glob(os.path.join(dsdir, "*.csv"))):
            base = os.path.basename(csv)
            if base.startswith("_") or base == "ping_windows.csv":
                continue
            try:
                with open(csv, "r") as fh:
                    header = fh.readline().strip().split(",")
            except OSError:
                continue
            if not {"t_s", "bearer", "dl_mbps"}.issubset(set(header)):
                continue
            traces[base[:-4]] = csv
        if traces:
            out[ds] = traces
    return out


def trace_is_heldout(trace_id: str) -> bool:
    """Frozen split rule (SPEC §6.4): sha1(trace_id) % 5 == 0 -> held-out."""
    h = hashlib.sha1(trace_id.encode("utf-8")).hexdigest()
    return (int(h, 16) % 5) == 0
