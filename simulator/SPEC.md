# SPEC.md — Frozen Simulator Specification (P1 + P2)

**KATIM Gateway 9001-R seamless-continuity simulator.** This is the frozen contract: the engine, the gateway model, the traffic model, the scenarios, the metrics and `eval.py` are **frozen**. Only the policy files change after the freeze.

Status: DRAFT for sign-off. Author: Simulator Architect. Companion evidence: the docs in `reference/` (AM = `ARCHITECTURE-MAP.md`) and EDGE's public KATIM documents (see `../SOURCES.md`).

---

## 0. Conventions (read first)

### 0.1 Evidence tags
Every modelling choice carries a tag:
- **[D]** documented — a public KATIM source says it. Cited as `KEY:Lnn` (file + line).
- **[D-ext]** documented-external — a public third-party measurement/standard says it. Cited by name.
- **[I]** inferred/assumed — we reason it. Every [I] states the reasoning **and** names the sensitivity sweep that brackets it. **No [I] number is load-bearing on its own; a sweep must show the result survives the plausible range.**

### 0.2 Citation keys (EDGE's public KATIM documents — see `../SOURCES.md`)
| Key | File |
|---|---|
| WP | `20230508-katim-gateway-network-security-12-pg-12052023_eng.txt` (9011 Security white paper — the protocol handbook) |
| R | `katim-gateway-9001r.txt` (9001-R datasheet, 2025) |
| 9001 | `katim-gateway-9001.txt` |
| X | `KATIM-Gateway-X9000R.txt` (2026) |
| AM | `../ARCHITECTURE-MAP.md` |

Line numbers are from the `.txt` extractions as of 2026-10-07. The 9001-R is the target; where the 9001-R datasheet is silent the WP (a 9011 document) supplies the protocol detail, and we say so, because the family shares "KATIM Gateway OS … functional parity … in mixed KATIM Gateway product deployments" (R:45–46).

### 0.3 Dependency rule
**Nothing in this spec may block on a human reply.** Every parameter resolves from one of three sources only: (a) a public KATIM document (see `../SOURCES.md`), (b) an open trace already downloaded under `traces/raw/`, or (c) an **[I]** assumption with a sweep. Anything historically marked "ask the authors" is resolved by **exclusion or parameterisation**, never by waiting:
- **Satellite-vs-Cellular (Hu et al.)** — reuse terms unclear → **excluded as data**; cited for its published findings only (TRACES.md §1 row 5). The simultaneous satellite+cellular overlap is instead **stitched** from two open single-bearer traces (§3.6).
- **Message semantics** — the fields the policy needs (`supersedes`, `urgency`, `value_half_life`) are produced by a **deterministic rule over documented message schemas** (§5.6), computable offline today, with no external dependency.
- **IEEE DataPort / CRAWDAD logins** (Lumos5G, Berlin V2X) are **self-service sign-ins**; the pipeline degrades gracefully without them (§3, cut rule).

### 0.4 Golden rule on data
Real measured data only. In processed files we **never interpolate, resample, smooth or fill**. Missing stays empty. Resampling to the tick is the simulator's job and is done transparently at run time (§2), not baked into the data. Every dropped row is counted with a reason in the per-dataset **data card** (§3.7).

---

## 1. Purpose and the two claims

### 1.1 Purpose
A trace-driven, deterministic Python simulator of a KATIM Gateway 9001-R pair (a moving team's gateway ↔ a command gateway) carrying mission traffic over real measured radio bearers, used to test whether two **learned, auditable** policies beat a faithful emulation of KATIM's **documented** design across bearer transitions.

The gateway is **bearer-blind by construction**: it has one plain port and one cipher port (R:53–57), so Wi-Fi/5G/LEO/GEO selection happens in an external router on the black side and reaches the encryptor only as loss, delay, throughput collapse and endpoint change [I, AM:28]. Every claim we test is about behaviour that is invisible to the certified crypto boundary, so a policy can be shipped as a **Network Management Suite / CNM configuration feature** (R:45–46; X:58) without touching certified crypto.

### 1.2 The two claims
- **P1 — Freshness-aware scheduling.** When link capacity collapses at a bearer switch, a learned policy that re-maps traffic to planes, drops **superseded** data, and rate-caps per plane keeps responder-location **Age of Information (AoI)** low and hazard alerts on time, beating the documented static mapping. Addresses the gap that KATIM's mapping is static or "automatic" (R:75–76) with internal discard priority only **within the gateway** (R:77–79) — it isolates transit-network congestion per plane (WP:299–304) but does **not** react to the bearer under it collapsing [I, AM:76–77], and has no notion of a stale position being less worth sending than a fresh one [I].
- **P2 — Make-before-break.** When the bearer changes, a learned policy predicts the transition and **warms a standby encrypted tunnel on the next bearer before the current one breaks**, cutting outage seconds toward zero while keeping handshake/keepalive overhead within budget and leaving the security protocol untouched. Addresses the gap that recovery is reactive: handshake retry at 10 s (WP:323), re-handshake ~60 s (WP:150–151, 330–332), over a ~600 ms GEO link [I worst-case, AM:81].

### 1.3 What "beats the documented design" means (frozen definition)
On **held-out** scenarios (split by trace, §6.4), averaged over the frozen seed set (§10), the P1+P2 policy **beats the documented-static baseline** iff **all** hold:
1. **Gates pass** (same gates for every policy): hazard-alert deadline-hit rate ≥ `ALERT_HIT_MIN` and voice one-way delay within the G.114/MCPTT limit on every scenario (§8).
2. **Primary objective strictly improves**: lower **p95 location AoI** *and* not-worse **outage-seconds-per-transition**, with at least one strictly better, at an overhead tier no worse than the baseline's (§8.6).
3. **Reliability**: the improvement holds in ≥ `RELIABLE_FRAC` of held-out scenarios and the mean improvement exceeds the across-seed standard deviation (we report the distribution, not one number).
4. **Sanity**: the policy never beats the **perfect-foresight oracle** (§9); if it does, that scenario is treated as a leak/bug and quarantined.

Claim wording in the write-up is always "beats a faithful emulation of the documented design", **never** "beats KATIM" (AM:110).

Defaults (frozen in `eval.py`, §8.7): `ALERT_HIT_MIN = 0.99`, `RELIABLE_FRAC = 0.8`.

---

## 2. Time model

### 2.1 Tick
Fixed tick **Δt = 100 ms** [I]. Reasoning: fine enough to resolve a sub-second dark gap and a handshake of a few RTTs on a ≥480 ms GEO link, coarse enough that a 1-day build and an overnight loop are cheap; 1 Hz traces hold cleanly at 10 ticks/sample. **Sweep S-TICK**: re-run the final held-out evaluation at Δt ∈ {50, 100, 200} ms; results must be qualitatively unchanged. The tick is a frozen constant `SIM_TICK_S = 0.1`.

### 2.2 Trace replay = zero-order hold, never interpolation
A bearer is driven by its processed CSV (one row per real sample, §3). At tick time `t` the engine selects, **per bearer and per field independently**, the most recent real sample at or before `t` and **holds its value** (zero-order hold). It never averages or interpolates between samples, and never reads a future sample. A field that is empty in the selected row is **absent** this tick (not zero, not held from an older row for throughput — see §2.4).

Rationale for hold-not-interpolate: interpolation invents measurements between real ones and would smooth exactly the capacity cliffs and RTT steps we are trying to study. Holding the last real value is the honest "what the gateway last saw" model and is what a real device experiences between measurement updates.

### 2.3 Validity horizon and gaps (the "never invent data" rule)
Each field has a **max hold horizon**. If the newest real sample for a field is older than its horizon, the field becomes **empty/absent**, and the bearer-level rule below applies.

| Field class | Horizon `H` | Why |
|---|---|---|
| Throughput (`dl_mbps`,`ul_mbps`) | `H_tput = 2 s` [I] | throughput is bursty and bearer-defining; holding a stale rate across a real measurement gap would fabricate capacity. |
| Fast telemetry (`rtt_ms`,`loss`) from a ~1 Hz source | `H_tput = 2 s` | same cadence as throughput. |
| Slow telemetry (`rtt_ms`,`loss`) from a **sparse** source (LEO ping: ~25 s windows; LENS) | `H_rtt = 120 s` [I] | RTT/loss vary slowly vs throughput; the source itself samples them sparsely, so a longer hold matches the measurement design. Staleness is flagged to the policy via `telemetry_age_s` (§7). |
| Signal/pose (`rsrp_dbm`,`sinr_db`,`speed_kmh`,`lat`,`lon`,`cell_id`,`net_mode`) | `H_pose = 5 s` [I] | slowly varying context; used for prediction, not for capacity accounting. |

**Bearer-level gap rule.** If a bearer's **throughput** field is absent (gap > `H_tput`), that bearer is **UNUSABLE** for those ticks: the engine models it as an outage of the underlay (no bytes move), **not** as zero-capacity-forever and **not** as held capacity. This is the single decision the task asks us to make explicit: *a throughput gap longer than the horizon = link unusable for that interval, surfaced to the policy as loss/timeout, never filled.* **Sweep S-GAP**: `H_tput ∈ {1, 2, 5} s`; the headline results must not depend on the choice.

Distinguish two gap causes in the data card (§3.7): (a) **measurement gaps** (the logger paused) vs (b) **real outages** (the link was down). We cannot always tell them apart from a single-bearer trace; we therefore treat both as UNUSABLE and **state in the write-up that a measurement gap is modelled conservatively as an outage**. For the stitched dual-bearer scenarios (§3.6) this is conservative in our favour only for the baseline and the policy equally, so it cannot flatter the policy.

### 2.4 Determinism
Given the processed traces, the scenario, the policy and a seed, a run is bit-reproducible. All randomness (traffic arrivals, talk-spurts, loss realisation from a `loss` fraction) draws from a single `numpy.random.Generator(PCG64(seed))` threaded through the engine. No wall-clock, no unseeded RNG, no dict-ordering dependence.

---

## 3. Bearers

Six bearer types. Five are **replayed** from real traces; GEO is a **measurement-parameterised model** (labelled as such everywhere). Each processed file maps to exactly one raw run (provenance, §3.7).

### 3.1 Unified per-trace schema (the only format `sim/engine.py` reads)
Written to `traces/processed/<dataset>/<trace_id>.csv`, one row per real sample, monotonic `t_s`:

```
t_s        float  seconds since this run's first sample (monotonic increasing)
ts_utc     str    ISO timestamp if known, else ""
bearer     str    one of {5G, LTE, LEO, WiFi, cellular-subflow}   (GEO is modelled, not a processed file)
net_mode   str    raw access mode if logged (5G, LTE, HSPA+, …), else ""
dl_mbps    float  Mbit/s or ""      (download / consumer throughput)
ul_mbps    float  Mbit/s or ""
rtt_ms     float  ms or ""
loss       float  fraction 0..1 or ""
cell_id    str    cell / AP / server id marking the attachment point, or ""
rsrp_dbm   float  or ""   (or RSSI equivalent; data card says which)
sinr_db    float  or ""   (or SNR equivalent; data card says which)
speed_kmh  float  or ""
lat        float  or ""
lon        float  or ""
```
Empty cells are the empty string. No other columns. Derived handover events are **not** stored here; they are recomputed by the documented rule in §6.3 so anyone can reproduce them from the processed CSV.

### 3.2 5G / LTE — replay (UCC 5G, UCC 4G LTE; Lumos5G later)
Source columns (confirmed by inspection of `traces/raw/ucc-5g/.../*.csv` and `ucc-4g-lte/.../*.csv`):
`Timestamp,Longitude,Latitude,Speed,Operatorname,CellID,NetworkMode,RSRP,RSRQ,SNR,CQI,RSSI,DL_bitrate,UL_bitrate,State,[PINGAVG,PINGMIN,PINGMAX,PINGSTDEV,PINGLOSS,…],RAWCELLID,NRxRSRP,NRxRSRQ`.

Mapping:
| schema | from | transform | units evidence |
|---|---|---|---|
| `t_s` | `Timestamp` (`YYYY.MM.DD_HH.MM.SS`) | parse → seconds since first row | 1 Hz cadence (consecutive rows differ by 1 s) [D-ext UCC MMSys'20] |
| `ts_utc` | `Timestamp` | ISO-format | — |
| `bearer` | — | `5G` or `LTE` by file/`NetworkMode` | — |
| `net_mode` | `NetworkMode` | verbatim (`5G`/`LTE`/`HSPA+`…) | this column **is** the real bearer-mode switch to replay (TRACES.md §1 row 1) |
| `dl_mbps`,`ul_mbps` | `DL_bitrate`,`UL_bitrate` | **÷1000** (kbit/s→Mbit/s) | [I] value-range: LTE row `DL_bitrate=11877`→11.9 Mbps is a plausible LTE rate; the UCC release documents kbit/s. Guard: reject file if median DL would imply >10 Gbps (unit error) — counted in data card. |
| `rtt_ms` | `PINGAVG` (5G set only) | float; `"-"`→"" | ms [I] column name |
| `loss` | `PINGLOSS` | if present as %, **÷100**; `"-"`→"" | [I] percent→fraction; sweep not needed (loss realisation is seeded, §4) |
| `cell_id` | `RAWCELLID` else `CellID` | string | attachment change marker |
| `rsrp_dbm` | `RSRP` | float | dBm (negative values ~ −60…−120) |
| `sinr_db` | `SNR` | float; data card notes SNR≈SINR proxy | dB |
| `speed_kmh` | `Speed` | float | [I] km/h: GPS speed, 0 when static; sweep not needed |
| `lat`,`lon` | `Latitude`,`Longitude` | float | deg |

UCC 5G gives the real **5G↔LTE `NetworkMode` switch** (the LTE↔5G transition scenario). UCC 4G LTE gives pedestrian/tram/train breadth. Lumos5G (login, teammate) adds the sharpest mmWave collapses and ships `RSRP/SINR/tower_id/nrStatus`; if the login is not done by end of D-4, the pipeline runs on UCC alone and the write-up says so (no dependency on it existing).

### 3.3 LEO — replay (Starlink on the Road)
Two source files, **joined by time window, not interpolated**:
- `data/iperf.csv`: `timestamp_start,timestamp_end,…,download_mbps,upload_mbps,average_speed` — throughput already in **Mbps** (column name + README) and the per-sample **driving speed**.
- `data/ping.csv`: `timestamp_start,timestamp_end,…,packet_loss,packets_send,ping_avg,…,average_speed` — **RTT in ms** (`ping_avg`), **loss** (`packet_loss`, percent → ÷100; evidence: `packet_loss=0.4` with `packets_send=250` = exactly 1 packet), over ~25 s windows (README: 17 479 iperf vs 1 191 ping samples → RTT is sparse).

Mapping: emit one processed row per **iperf** sample (`t_s` from `timestamp_start` minus run start; `ts_utc` kept). Attach `rtt_ms`/`loss` from the ping row **whose window contains this iperf timestamp**; if none contains it, leave empty (the §2.3 `H_rtt=120 s` hold then carries the last real RTT forward at run time, flagged stale). `speed_kmh` from `average_speed` ([I] km/h — driving trace; value-range 0–130). `bearer=LEO`. No `cell_id` (Starlink hides it); the ~15 s reconfiguration dips appear as throughput collapses in the replay. Rows with no throughput are dropped and counted.

### 3.4 Wi-Fi — replay (Meireles V2I)
Source `meireles-v2i-wifi.csv`: `systime,traceNr,…,receiverDist,receiverSpeed,…,rssiMean,snrMean,…,dataRateMean,…,tghptConsumer,…`.
Mapping: `t_s` from `systime` (epoch s) minus run start; **one processed file per `traceNr`** (each lap past the roadside AP is one run — this is the coverage cliff). `dl_mbps = tghptConsumer` ([I] Mbps; value-range: `1.66` consistent with application goodput, whereas `dataRateMean=125.8` is the PHY rate — keep `tghptConsumer` as goodput, record `dataRateMean` nowhere unless we add a PHY column later). `rsrp_dbm = rssiMean` (RSSI proxy, noted in data card), `sinr_db = snrMean`. `speed_kmh = receiverSpeed ×3.6` **[I] units unresolved**: the column may already be km/h; the data card states the assumption and the one-line check (does max·3.6 exceed a car's plausible speed?) — this is a self-contained range check, not a question to the authors. `cell_id` = the AP id (constant within a lap; the cliff is distance-driven). `bearer=WiFi`.

### 3.5 Multi-subflow — replay (HSR-MPTCP)
Source is packet-level pcap CSV: `frame.time_epoch,ip.src,ip.dst,tcp.srcport,tcp.dstport,tcp.len,…,tcp.analysis.ack_rtt,…,tcp.options.mptcp.*`. This is the only **real simultaneous multi-bearer** source (two carrier subflows + Wi-Fi subflow over MPTCP). Aggregation (documented, reproducible):
- A **subflow** = a `(src,dst,srcport,dstport)` 4-tuple (one per file in the dataset's layout; one subflow per file is the common case).
- Bucket packets into **1 s windows**: `dl_mbps = (Σ tcp.len in window) × 8 / 1e6` (bytes→Mbit/s; `tcp.len` is TCP payload bytes). `rtt_ms = median(ack_rtt in window) × 1000` (ack_rtt is seconds; empty when the window has no RTT sample). `loss` left empty (not directly in the CSV; §4 applies seeded loss only if a scenario needs it). `t_s` = window index.
- `bearer = cellular-subflow`; the subflow identity goes in `cell_id` so two subflows can be fed as two simultaneous bearers.
The 1 s window is an aggregation choice, not interpolation (it counts real bytes); **sweep S-WIN** window ∈ {0.5, 1, 2} s for the multi-bearer scenarios.

### 3.6 GEO — measurement-parameterised model (NOT a replay)
No open ≤1 s GEO throughput trace exists (TRACES.md §1 GEO note), so GEO is a **model** driven by cited public measurements, labelled "measurement-parameterised" in every figure and table. The engine synthesises a GEO bearer's `dl_mbps/ul_mbps/rtt_ms/loss` per tick from the table below plus a seeded rain-fade process; it is never presented as replayed data.

**GEO parameter table (frozen model inputs).**
| Parameter | Value (default) | Tag · source |
|---|---|---|
| One-way propagation floor | ≈ 238–280 ms per ground→GEO→ground path leg; bent-pipe user↔gateway RTT ≈ 4 legs | [D-ext physics] GEO altitude 35 786 km / c; matches CONTEXT.md "≥~480 ms by physics" |
| Nominal RTT (clear sky) | **600 ms** (sweep 550–700) | [D-ext] FCC MBA fixed reports: median satellite latency **599–629 ms**; ViaSat avg **~671 ms** vs terrestrial 12–58 ms |
| RTT under rain fade | **up to 3000 ms**, heavy-tail | [D-ext] IIUM SES-12 study (Ku-band, 95°E, 1.2 m VSAT, 6 months): SES-12 GEO terminal latency **600 ms → 3000 ms** under tropical rainfall |
| Link availability (fade outages) | **94%** in severe-rain climate (sweep 94–99.6%) | [D-ext] IIUM SES-12: 94% SES-12 vs 99.6% Starlink; corroborated by Thomas, Dubois, Kuhn & Lochin 2019 (hal-02401946) on operational GEO SATCOM RTT/PEP behaviour |
| Download | **18 Mbps** (ViaSat-era, sweep 12–25) | [D-ext] FCC MBA: ViaSat delivered ~150% of a 12 Mbps advertised tier → ~18 Mbps |
| Upload | **3 Mbps** (sweep 3–5) | [D-ext] FCC MBA: HughesNet Gen5 advertised 25/3 Mbps tier (consistently met) |
| Baseline loss (clear) | **0.5%** (sweep 0.1–1%) | [I] small-but-nonzero on a managed GEO link; bracketed by sweep |
| Loss under fade | up to **5–20%** correlated with RTT inflation | [I] rain fade degrades both; tied to the same seeded fade process; sweep the ceiling |

**Rain-fade process [I, seeded].** A two-state (clear/fade) Gilbert-Elliott-style process whose steady-state fade probability matches `1 − availability`; in fade, RTT is drawn from the heavy tail toward 3000 ms and loss from the fade range, both from the run seed. Reasoning: GEO degradation is bursty and rain-correlated, not i.i.d.; a two-state model is the minimal honest shape. **Sweep S-GEO** over availability, nominal RTT, and the fade RTT ceiling; the P2 result (outage reduction) must survive the whole box. GEO never "roams" (it is backhaul); it is only ever the *target* or *source* bearer in a transition.

### 3.7 Stitched overlap (honest dual-bearer construction)
P2 and multi-bearer scenarios need two bearers available during an overlap window. Three constructions, in descending order of realism, each labelled in the scenario:
1. **Real simultaneous (preferred):** HSR-MPTCP two subflows (§3.5) — genuinely concurrent, no stitching.
2. **Stitched single-bearers:** align two real single-bearer traces (e.g. UCC 5G driving + Starlink-on-the-Road LEO) on a **common `t_s` origin** and play them concurrently. Alignment rule (frozen): both start at their own `t_s=0`; the transition instant `t_x` is the scenario's cutover time; no resampling, each bearer uses its own hold horizons. We **state in the write-up that the two were measured independently and only time-aligned** — we never claim they were captured together. To realism-check the stitch we cite (not use) Satellite-vs-Cellular's published simultaneous findings (TRACES.md §1 row 5).
3. **Replay + GEO model:** a replayed terrestrial bearer against the §3.6 GEO model (for 5G→GEO). Labelled "one replayed bearer, one measurement-parameterised bearer".

### 3.8 Data card (one per processed dataset, `traces/processed/<dataset>/DATACARD.md`)
Mandatory fields: raw→processed provenance (exact raw path, SHA256), licence, unit decisions with the evidence cited above, **row counts: kept / dropped-with-reason** (unit-guard reject, no-throughput, unparseable timestamp, duplicate), gap histogram (measurement vs modelled-outage), and which optional columns are proxies (SNR→SINR, RSSI→RSRP). The completeness critic blocks sign-off if any processed file lacks a data card.

---

## 4. Gateway model (`sim/gateway.py`)

Models **one 9001-R pair**: the moving team gateway and the command gateway, one association between them. The association carries **up to 8 QoS-aware encryption planes, each with independent keys** (R:70–73; WP:162–165, 299–304) [D]. One **active cipher port** and one plain port (R:53–57) [D]; multi-bearer selection is external [I, AM:28].

### 4.1 Planes
`Plane[0..7]`, each: a FIFO (or policy-reordered) queue, an internal **discard priority** class, and a per-plane DSCP tag on the black side (R:77–79; WP:299–304) [D]. Documented behaviour: congestion/shaping in the transit network is isolated per plane (WP:299–304) [D]; there is **no documented reaction to the bearer collapsing** [I]. The default plane→traffic assignment for the documented baseline is a **static table** (§9.1), reflecting "user-configurable or automatic mapping" (R:75–76) [D] with "automatic" modelled as a fixed classifier [I, AM:61].

### 4.2 Timers and counters [D unless noted]
| Mechanism | Model | Tag |
|---|---|---|
| Discovery + handshake **retry** | fires every **10 s** while a tunnel is down | [D] WP:323 (Table 5) |
| Full **re-handshake** | every **60 s** (configurable), refreshes all keys | [D] WP:150–151, 330–332 |
| Data-plane **key ratchet** | every **131 072 packets** per tunnel, autonomous, no control messages | [D] WP:193–194, 328–329 |
| Key lifetime (sanity cross-check) | derived = ratchet ÷ pkt-rate; e.g. 1 Gbps@1500 B ≈ 1500 ms, 100 Mbps IMIX ≈ 3700 ms | [D] WP:197–205 (Table 2) |
| Control- & data-plane **keepalive / DPD** | keepalive every `KA = 1 s`; peer declared dead after `DPD_MISS = 3` missed (→ 3 s) | [D] exists (WP:314–318, 325–327); **[I]** values → **sweep S-DPD** `KA ∈ {0.5,1,2} s`, `DPD_MISS ∈ {2,3,5}` |

The ratchet matters under burst loss: peers that miss the packets spanning a ratchet boundary sit in different epochs until a data-plane keepalive resyncs (WP:189–194; AM:85) — modelled as undecryptable drops until resync. This is observable in metrics but **not** a target of P1/P2 (that is G3, out of scope).

### 4.3 Handshake cost model (the thing P2 trades against)
Bringing up (or re-establishing) a tunnel costs wall-clock time and bytes on the bearer:

```
handshake_time_s = rounds * rtt_s  +  (handshake_bytes / min(dl_bps, ul_bps))
handshake_bytes  = classical_bytes + pq_bytes           # on the wire, both directions summed
```
- `rounds` [I]: **2** round trips for a Noise/WireGuard-family handshake stand-in, +1 if the PQ layer adds a trip → **sweep S-HS-ROUNDS ∈ {2, 3}**. Reasoning: WP describes a "simplified handshake" with a discovery handshake then a session handshake (WP:258–273, 290–295); the Noise-IK family is 1–1.5 RTT, PQ KEM additions push it toward 2–3 (AM:34) [I]. We say "Noise-family stand-in", never "it is WireGuard" (AM:34).
- `handshake_bytes` [I]: KB-scale. KATIM uses 512-bit elliptic-curve classical keys (WP:283, Table 4) and a **"quad Diffie-Hellman and triple KEM"** PQ layer (WP:158–159) with unnamed KEM. Model `pq_bytes` from ML-KEM-class sizes as a stand-in (e.g. triple KEM pubkey+ciphertext ≈ 4–6 KB) → **sweep S-HS-BYTES ∈ {2, 4, 8, 16} KB**. The KEM being unnamed is stated as an explicit assumption (AM:81, 112).
- On a 600 ms GEO link this yields ≈ `2–3 × 0.6 s + few-KB/18 Mbps` ≈ **1.2–1.8 s** worst case (AM:81), the dark gap P2 attacks. KB-scale PQ keys fragment on constrained links [I, AM:81].

### 4.4 Endpoint-roaming switch (the key open question, as a parameter)
`SESSIONS_ROAM` [I], default **OFF**. OFF = a bearer change that alters the black-side endpoint (address/NAT) **requires re-establishment** (DPD → 10 s retry → handshake); this is the pessimistic, P2-favouring assumption and the one we headline. ON = an authenticated packet from the new address updates the peer endpoint **without** a re-handshake (WireGuard-style roaming), so P2 narrows to sessions that genuinely die (long outages, NAT rebinding during a satcom gap) (AM:82; CONTEXT.md P2). **Sweep S-ROAM**: report P2 both ways; we never claim KATIM does or doesn't roam — we show the outcome under each assumption (AM:82 "do not claim it").

### 4.5 External bearer router [I]
Assumed present (Nokia 5G-in-a-box / tactical satcom modem; AM:104). It performs bearer selection; the gateway sees only the resulting loss/delay/throughput/endpoint. For P3-style per-plane steering (out of scope here) this router would also steer DSCP-tagged planes, but P1+P2 do not require it beyond single-bearer-at-a-time selection with overlap windows (§3.7).

---

## 5. Traffic model (`sim/traffic.py`)

**N = 15 responders + 1 command vehicle** at an industrial incident (CONTEXT.md §4). All red-side flows enter the team gateway, cross the tunnel, and exit at command (uplink-dominant). Every rate/size below is cited or marked [I] with a sweep. Message objects carry metadata the policy may read (§7); **ground-truth value/half-life is hidden** from the policy and used only by metrics.

### 5.1 Position reports (PLI)
- Rate: **1 Hz per responder** [I-cited]. Reasoning/source: TAK / Cursor-on-Target situational-awareness PLI defaults are ~1 Hz to every few seconds; NIST PSCR / APCO location-reporting guidance for public safety is sub-5-second. **Sweep S-PLI ∈ {0.2, 0.5, 1} Hz.**
- Size: **~200 B** payload [I]: CoT as protobuf ≈ 100–200 B; CoT XML ≈ 0.5–1 KB. Model 200 B, **sweep 0.1–1 KB**.
- Semantics: each new PLI from a responder **supersedes** that responder's previous undelivered PLI (this is the crux of P1's AoI win). `value_half_life` short.
- Plane (documented baseline): a low-priority "telemetry" plane.

### 5.2 Hazard alerts
- Arrivals: Poisson, aggregate **1 per 30 s** across the team during the active incident [I] (bursty around transitions in some scenarios) → **sweep S-ALERT-RATE ∈ {1/60, 1/30, 1/10} s⁻¹.**
- Size: **~500 B** [I] (short structured alert; sweep 0.2–2 KB).
- **Hard deadline** `D_alert = 2 s` end-to-end at command [I-set, justified like the freshness threshold] → **sweep 1–5 s.** Deadline-hit rate is a gate (§8).
- Semantics: high urgency, **not** superseded; must not be dropped.

### 5.3 Push-to-talk voice (MCPTT)
- Codec: **AMR-WB @ 23.85 kbps** as the default tactical PTT codec [I-cited: 3GPP-standard wideband], with **MELPe @ 2.4 kbps** as a low-rate sweep point for constrained satcom → **sweep S-VOICE-CODEC ∈ {MELPe 2.4, AMR-WB 23.85} kbps.**
- Talk-spurt model: on/off, mean talk-spurt **1.5 s**, mean silence **2 s** (voice activity factor ≈ 0.4) [I-cited: standard VAD modelling]; one active talker at a time per PTT net (half-duplex) [I]. Packetisation 20 ms frames.
- Constraint (gate): one-way mouth-to-ear delay within **ITU-T G.114** (≤150 ms recommended, ≤400 ms limit) and **3GPP TS 22.179 MCPTT** (≤300 ms mouth-to-ear, ≤1000 ms access) (CONTEXT.md §4; AM:111). On GEO the ~600 ms RTT means one-way ≥ ~300 ms before processing — voice over GEO starts with little/no slack; the model must expose that honestly.
- Plane: a high-priority "voice" plane.

### 5.4 Bodycam / drone video
- Bitrate: bodycam **1.5 Mbps** (720p H.264) [I-cited typical], drone **3 Mbps** (1080p H.265) [I] → **sweep S-VIDEO ∈ {0.5, 1.5, 3} Mbps per stream.** A few streams active, not all 15.
- Elastic-ish but delay-tolerant; goodput is a *secondary* objective (maximise after gates/AoI).
- Plane: a "video" plane.

### 5.5 Bulk data
- Background elastic transfer (map tiles, logs); greedy to fill spare capacity; lowest priority plane. No deadline. Used to create realistic contention for the shrunken link.

### 5.6 Message semantics without any external model (self-contained)
The policy needs, per red-side message, `data_type`, `urgency`, `supersedes_previous`, `value_half_life`. These are produced by a **deterministic rule table keyed on `data_type`** (PLI→supersedes=true, short half-life; alert→urgency=high, supersedes=false; voice→realtime; video→elastic; bulk→background). This rule is computable **today, offline**, from the documented message schema, with **no external dependency** (§0.3). Richer semantics for unstructured messages are future work; the deterministic rule stands on its own.

---

## 6. Scenarios (`sim/scenarios.py`)

### 6.1 Composition
A scenario = {duration, mobility class, a sequence of bearer-availability windows per the §3.7 construction, a transition schedule, the traffic set (§5), N=15+1}. All transition instants are scenario constants (not read by the policy; §7.3).

### 6.2 Scenario matrix (frozen set)
| id | mobility | transition | construction | tests |
|---|---|---|---|---|
| `S1_lte5g_walk` | walking | LTE↔5G | UCC 5G real `NetworkMode` switch | P1 light collapse |
| `S2_5g_leo_drive` | driving | 5G→LEO | stitch UCC 5G drive + Starlink-on-the-Road | P1+P2 big capacity/RTT step |
| `S3_5g_geo_drive` | driving | 5G→GEO | replay 5G + GEO model | P2 worst-case dark gap |
| `S4_wifi_edge` | driving | Wi-Fi coverage cliff → cellular | Meireles lap + UCC | P1 cliff, P2 bring-up |
| `S5_mptcp_hsr` | high-speed | multi-subflow handovers | HSR-MPTCP two subflows | real simultaneous multi-bearer |
| `S6_collective_5g_geo` | mixed | **collective**: 15 users 5G→GEO within a short window | stitch, staggered `t_x` | P1+P2 under correlated load |

### 6.3 Handover-event extraction rule (documented, reproducible — no hand labels)
Events are **derived** from the processed CSV at run/predictor time by one published rule per signal, OR-combined:
- **cell/AP change**: `cell_id` changes;
- **mode flip**: `net_mode` changes (e.g. 5G→LTE);
- **RTT step**: `rtt_ms` jumps by ≥ `RTT_STEP` (default **×2** and ≥100 ms) [I];
- **throughput collapse**: `dl_mbps` drops by ≥ `TPUT_DROP` (default **5×**) within ≤2 s [I];
- **gap/outage**: a §2.3 UNUSABLE interval begins.
**Validate** the rule on **Berlin V2X** (event-based cell identity ground truth; login, teammate) and report precision/recall of the rule against its labels (TRACES.md §1 row 8). If Berlin V2X is not available by D-2, validate on the UCC 5G `NetworkMode` switch (self-contained ground truth) and say so. **Sweep S-EVENT** over `RTT_STEP`, `TPUT_DROP`. These thresholds define the *label*; the predictor (§7) must not see the future, only the label's causal features.

### 6.4 Train / held-out split — BY TRACE, never by time slice
The autoresearch loop and predictor training see only the **train** trace set; final numbers come from a disjoint **held-out** trace set. Split rule (frozen in `eval.py`): partition **at the granularity of a whole processed trace/run id**, hashed `sha1(trace_id) % 5 == 0 → held-out` (≈20%), so no run is split across train/held-out and the loop can never see a held-out trace even partially. Scenarios built from held-out traces are the held-out scenarios. We never split a single trace by time window (that would leak the transition the policy is tested on). GEO model seeds are likewise partitioned: held-out evaluation uses seeds the loop never ran.

---

## 7. Policy interface (`policies/policy.py`, `policies/continuity_policy.py`)

The policy is the **only** editable code. It is called every tick with an **observation** and returns an **action**. It may read only what a real 9001-R (plus its NMS/CNM config hooks) could observe.

### 7.1 Observation (what the policy MAY see)
```python
@dataclass(frozen=True)
class Obs:
    t_s: float                      # sim clock (allowed: a real device has a clock)
    planes: list[PlaneState]        # per-plane: depth_pkts, depth_bytes, head_age_s, drops
    link: LinkTelemetry             # keepalive_rtt_ms, rtt_trend, loss_est, tput_est_mbps,
                                    #   telemetry_age_s (staleness from §2.3), dpd_missed
    messages: list[MsgMeta]         # red-side metadata ONLY (see 7.2)
    bearer_hint: BearerHint | None  # OPTIONAL flag the external router MAY expose:
                                    #   {current_bearer_class?, candidate_available?} — nothing more
    standby: StandbyState           # per-candidate: warming?, ready?, bytes_spent, age_s
```
`tput_est_mbps`, `rtt_trend`, `loss_est` are **estimates the gateway computes from its own keepalives and traffic**, not the raw trace. `telemetry_age_s` lets the policy know when telemetry is stale (e.g. sparse LEO RTT).

### 7.2 Message metadata the policy MAY read (red side)
`MsgMeta = {msg_id, data_type, size_bytes, enqueue_t_s, deadline_s?, urgency, supersedes_id?}`. These are plaintext-side facts a gateway at the red boundary legitimately has (it sees its own queued plaintext). The policy may **not** read a message's delivered/undelivered ground truth, its true AoI contribution, or anything about the future.

### 7.3 Actions (what the policy MAY do)
```python
@dataclass
class Action:
    plane_map: dict[msg_id|data_type, plane_idx]   # (re)map traffic to planes
    drop: list[msg_id]                              # drop superseded/low-value (P1)
    rate_cap_mbps: dict[plane_idx, float]           # per-plane caps (P1)
    warm_standby: BearerClass | None                # begin warming a standby tunnel (P2)
    cutover: bool                                   # switch active tunnel to ready standby (P2)
    teardown_standby: bool                          # release standby (P2, saves emissions)
    timer_override: {ka_s?, retry_s?, rehandshake_s?}  # bounded timer tweaks (P2)
```
`timer_override` is clamped to documented-plausible ranges (e.g. `ka_s∈[0.25,5]`). `warm_standby`/`cutover` drive the §4.3 handshake cost and the §4.4 roaming switch; standby tunnels get **fresh independent keys** and may never reuse keys (CONTEXT.md P2 security constraint) — the engine enforces this and scores any violation as **invalid** (run discarded).

### 7.4 Forbidden (hard, enforced by the harness — not just honour system)
The policy process receives **only** the `Obs` object. It has no handle to the engine, the trace arrays, the scenario transition schedule, future samples, the oracle, the RNG, or the metrics. Any attempt to import engine internals fails. This is what makes "observable inputs only" auditable (CONTEXT.md §4; PLAN.md "The policy may read only what a real gateway could observe").

### 7.5 Security invariants (scored invalid if broken)
No protocol/key-derivation/crypto change; timing and triggers of standard handshakes only (CONTEXT.md P2). Standby keys fresh and independent. Per-plane key isolation and anti-replay windows are the engine's, not the policy's, to manage.

---

## 8. Metrics (`eval/evaluate.py` — FROZEN) and the scalar score

All metrics computed by the frozen evaluator from the engine's event log. Exact definitions:

### 8.1 p95 location Age of Information
For each responder, AoI(t) = `t − (generation time of the freshest position report delivered to command by t)`. Sample AoI at command on every tick. Report **mean and p95 over all responders × all ticks** (per scenario, then aggregated). A dropped-because-superseded PLI does **not** raise AoI if a fresher one arrives; a *stalled* queue does. This is P1's headline. (Freshness threshold for "stale": `AOI_STALE = 5 s` at command [I-set, justified: command needs sub-5-s picture at an industrial incident; sweep 2–10 s].)

### 8.2 Hazard-alert deadline-hit rate — HARD GATE
`hits / total` where a hit = delivered to command within `D_alert` (§5.2). Gate: **≥ `ALERT_HIT_MIN` (0.99)** on every scenario, else the policy fails `beats-documented` regardless of other metrics.

### 8.3 Voice one-way delay compliance — HARD GATE
Fraction of delivered voice frames within the limit. Report against both **G.114** (≤150 ms good / ≤400 ms limit) and **MCPTT** (≤300 ms mouth-to-ear) (CONTEXT.md §4). Gate: median one-way ≤ the chosen limit AND ≥99% of frames ≤ hard limit. On GEO this gate is physically hard and that is a finding, not a failure to hide.

### 8.4 Outage seconds per transition
For each transition, the wall-clock seconds during which **no plane can deliver** (tunnel down, or no usable bearer). Report mean, p95, max per transition, and dropped-PTT-call count. P2's headline.

### 8.5 Standby transmit-seconds (emissions proxy)
Σ seconds a standby tunnel is actively transmitting (warming keepalives + handshake) on any bearer. The cost side of P2's trade-off; lower is better. Also report **overhead bytes** = handshake + keepalive bytes beyond the single-active-tunnel baseline, split by bearer (satcom bytes/hour is the metered one).

### 8.6 Secondary: video goodput
Delivered video bytes / offered, after gates and AoI. Maximise last.

### 8.7 Single scalar score (for the loop)
```
if not gates_pass(alert_hit, voice):        # §8.2, §8.3 on ALL scenarios
    score = -1e6 + (alert_hit + voice_frac) # still ordered, so the loop can climb toward feasibility
else:
    score = -( w_aoi  * p95_AoI / base_p95_AoI
             + w_out  * outage_s / base_outage_s
             + w_ovh  * overhead_bytes_satcom / base_overhead
             + w_tx   * standby_tx_s / base_tx )
           + w_vid * video_goodput / base_video
```
Weights frozen in `eval.py`, default `w_aoi=1.0, w_out=1.0, w_ovh=0.3, w_tx=0.3, w_vid=0.2`; normalised by the **documented-static baseline** so 0 = baseline-parity and the loop maximises a dimensionless margin. The loop sees only this scalar and `gates_pass`; it never sees the weights change, the held-out traces, or per-metric internals it could overfit. **Sweep S-WEIGHTS**: report the Pareto front (outage vs emissions) so the ranking does not hinge on one weight vector (PLAN.md "outage-vs-emissions curve").

---

## 9. Baselines (`policies/`)

### 9.1 `static_documented.py` — the bar (frozen)
Faithful emulation of documented behaviour: fixed plane→traffic table (voice/alert/telemetry/video/bulk → fixed planes), internal discard priority within the gateway (R:77–79), **no** bearer reaction, **no** superseded-drop, reactive recovery only (10 s retry, 60 s re-handshake, 131 072-pkt ratchet, keepalive DPD). This produces the bar; "beats a faithful emulation of the documented design" is measured against exactly this (AM:110).

### 9.2 `always_warm.py` — naive make-before-break (frozen)
Always keeps a standby tunnel warm on every available alternate bearer. Zero outage ideally, but maximal standby-transmit-seconds and satcom overhead — the emissions-expensive extreme that P2 must beat on the trade-off, not just on outage.

### 9.3 `oracle.py` — perfect-foresight upper bound (frozen)
Sees the true transition schedule and future trace values; warms exactly in time, drops exactly the superseded, never over-spends. Not shippable; it is the ceiling. A learned policy that beats the oracle is a leak (§1.3 sanity). The oracle is also the realism check on the stitch (if even the oracle cannot hit the voice gate on GEO, the gate — not the policy — is the story).

---

## 10. Evaluator freeze + reproducibility

### 10.1 Freeze
At end-of-D-4 sign-off, these are frozen and change-controlled (any change invalidates prior numbers and is logged): `sim/engine.py`, `sim/gateway.py`, `sim/traffic.py`, `sim/scenarios.py`, `eval/evaluate.py`, the scenario matrix (§6.2), the metric definitions (§8), the baselines (§9), the GEO parameter table (§3.6), and this SPEC. The **only** files that change after freeze are `policies/policy.py` and `policies/continuity_policy.py`, plus `loop/autoresearch.py`'s log.

### 10.2 Seeds
Frozen seed set `SEEDS = [0,1,2,3,4]` for train; held-out uses `SEEDS_HELDOUT = [100,101,102,103,104]` (disjoint; §6.4). Every reported number is mean ± std over the seed set. One global `PCG64` per (scenario, seed).

### 10.3 One-command run
```
python -m eval.evaluate --split heldout --policy policies/policy.py --seeds heldout --out report/figures/
```
reproduces every headline number and figure from the processed traces with no network access and no human input. A `make reproduce` wraps it. `python -m eval.evaluate --sweeps all` runs S-TICK, S-GAP, S-GEO, S-DPD, S-HS-ROUNDS, S-HS-BYTES, S-ROAM, S-PLI, S-ALERT-RATE, S-VOICE-CODEC, S-VIDEO, S-WIN, S-EVENT, S-WEIGHTS and emits the sensitivity tables. Trace download/parse is a **separate** step from the raw datasets (see `../SOURCES.md`), so evaluation never depends on the network at run time.

### 10.4 Code layout (matches PLAN.md)
```
katim-sim/
  SPEC.md                         this file (frozen)
  traces/raw/                     downloaded real traces (provenance)
  traces/processed/<dataset>/     unified-schema CSVs + DATACARD.md (§3)
  sim/engine.py                   100 ms tick, zero-order-hold trace replay (§2)
  sim/gateway.py                  8 planes, documented timers, handshake cost, roam switch (§4)
  sim/traffic.py                  PLI, alerts, PTT, video, bulk; 15+1 (§5)
  sim/scenarios.py                scenario matrix + stitching + event rule (§3.6,§6)
  policies/static_documented.py   baseline 1 (frozen, §9.1)
  policies/always_warm.py         baseline 2 (frozen, §9.2)
  policies/oracle.py              upper bound (frozen, §9.3)
  policies/policy.py              EDITABLE — P1 freshness
  policies/continuity_policy.py   EDITABLE — P2 make-before-break
  predictor/                      feature extraction, §6.3 event labels, training (train split only)
  eval/evaluate.py                FROZEN metrics + by-trace split + scalar score (§8,§6.4)
  loop/autoresearch.py            agent edits policy → evaluate(train) → keep/revert → log
  report/figures/
```

---

## Appendix A — [I] assumption register (every inference, with its sweep)
| # | Assumption | Default | Sweep | Basis |
|---|---|---|---|---|
| I1 | Tick granularity | 100 ms | S-TICK {50,100,200} | resolves sub-s gaps cheaply |
| I2 | Throughput hold horizon → UNUSABLE | 2 s | S-GAP {1,2,5} | throughput is bursty; longer = fabrication |
| I3 | Slow-telemetry hold | RTT/loss 120 s, pose 5 s | part of S-GAP | sources sample these sparsely |
| I4 | Keepalive / DPD values | KA 1 s, 3 missed | S-DPD | WP says exist, not the values (WP:314–327) |
| I5 | Handshake rounds | 2 (+1 PQ) | S-HS-ROUNDS {2,3} | Noise-family + PQ KEM (WP:258–273) |
| I6 | Handshake bytes | 4 KB | S-HS-BYTES {2,4,8,16} | unnamed triple-KEM, ML-KEM-class stand-in (WP:158–159) |
| I7 | Sessions roam across address change | OFF | S-ROAM on/off | the open question; never claimed (AM:82) |
| I8 | GEO nominal RTT / dl / ul / avail | 600 ms / 18 / 3 Mbps / 94% | S-GEO | FCC MBA + IIUM SES-12 (§3.6) |
| I9 | GEO fade process | 2-state, seeded | S-GEO | rain fade is bursty (IIUM SES-12) |
| I10 | PLI rate / size | 1 Hz / 200 B | S-PLI, size sweep | CoT/TAK, NIST PSCR/APCO |
| I11 | Alert rate / size / deadline | 1/30 s / 500 B / 2 s | S-ALERT-RATE, size, 1–5 s | incident-plausible; deadline is set+justified |
| I12 | Voice codec / VAD | AMR-WB 23.85 kbps / AF 0.4 | S-VOICE-CODEC {MELPe 2.4, AMR-WB} | 3GPP MCPTT; standard VAD |
| I13 | Video bitrate | 1.5 / 3 Mbps | S-VIDEO {0.5,1.5,3} | typical bodycam/drone H.264/265 |
| I14 | AoI "stale" threshold | 5 s | 2–10 s | command needs sub-5-s picture |
| I15 | Event thresholds | RTT ×2 (≥100 ms), tput 5× | S-EVENT | validated vs Berlin V2X / UCC switch (§6.3) |
| I16 | MPTCP aggregation window | 1 s | S-WIN {0.5,1,2} | counts real bytes, not interpolation |
| I17 | Score weights | see §8.7 | S-WEIGHTS / Pareto | ranking must not hinge on one vector |
| I18 | External multi-bearer router present | yes | — (structural) | Nokia 5G-in-a-box / satcom modem (AM:104) |
| I19 | Wi-Fi speed column units | m/s → ×3.6 | range self-check | resolved by value-range, not by asking authors |

## Appendix B — [D] documented-fact register (file:line)
| Fact | Cite |
|---|---|
| Up to 8 QoS-aware encryption planes, independent keys | R:70–73; WP:162–165, 299–304 |
| 1 cipher + 1 plain port; up to 1 Gbps; L2/L3; P2P/mesh/hub-spoke; OSPFv2 | R:53–62 |
| Mapping user-configurable or automatic | R:75–76 |
| Internal CoS, discard priority within the gateway | R:77–79 |
| Discovery + handshake retry every 10 s | WP:323 (Table 5) |
| Re-handshake ~ every 60 s (configurable) | WP:150–151, 330–332 |
| Key ratchet every 131 072 packets, autonomous | WP:193–194, 328–329 |
| Key-lifetime examples (rate-coupled) | WP:197–205 (Table 2) |
| Control/data-plane keepalives for dead-peer detection | WP:314–318, 325–327 |
| Discovery handshake, static discovery keys, identity hiding | WP:266–273 |
| Dual-layer classical+PQ handshake; quad-DH + triple KEM | WP:158–159, 290–295 |
| 512-bit EC = 15360-bit RSA equivalent | WP:283 (Table 4) |
| Crypto suite locked per release (no downgrade) | WP:102–105, 294–295 |
| In-motion / rugged deployment | R:5–6, 27 |
| Managed by NMS / CNM | R:45–46; X:58 |
| X9000R latency < 5 ms, ≤100 Mbps IMIX | X:39, 41 |

## Appendix C — external citations (no local file; cited, not depended-on)
- **FCC Measuring Broadband America**, fixed-broadband reports (satellite subset, ViaSat/HughesNet): median satellite latency 599–629 ms, ViaSat avg ~671 ms vs terrestrial 12–58 ms; ViaSat ~150% of a 12 Mbps advertised tier; HughesNet Gen5 25/3 Mbps tier. Public-domain regulator data.
- **IIUM SES-12 study** — "Latency Performance Evaluation of LEO Starlink and SES-12 GEO HTS Network Under Tropical Rainfall Conditions," IIUM Engineering Journal (Ku-band, 95°E, 1.2 m VSAT, 6 months): GEO terminal latency 600 ms → 3000 ms under rain; availability 94% (SES-12) vs 99.6% (Starlink).
- **Thomas, Dubois, Kuhn & Lochin (2019)**, operational GEO SATCOM RTT / PEP behaviour (hal-02401946): corroborates ~550–650 ms operational RTT floor.
- **ITU-T G.114** (one-way delay 150/400 ms); **3GPP TS 22.179** (MCPTT 300 ms mouth-to-ear, 1000 ms access).
- Trace papers: Raca et al. MMSys 2020 (UCC 5G); Laniewski et al. TMA 2024 (Starlink on the Road); Li et al. SIGCOMM 2018 (HSR-MPTCP); Meireles et al. (V2I Wi-Fi); Narayanan et al. IMC 2020 (Lumos5G); Hernangómez et al. VTC 2023 (Berlin V2X). Licences/access per `../TRACES.md`.

*End of SPEC.*

---

## 11. Addendum (2026-10-07): alignment with the official ATRC/EDGE rules

The official brief requires a **comparative analysis against EXISTING mechanisms** (success criterion 4) and an analysis of 1–2 key references (criterion 1). Two industry-standard mechanisms are therefore added as **frozen baselines**, alongside §9. All are implemented as policies behind the same §7 interface and scored by the same frozen evaluator.

### 11.1 `mobike_roaming.py`: IKEv2 MOBIKE-style session mobility (RFC 4555) [D: RFC; I: parameters]
- **What it models:** the standard way an encrypted tunnel survives an address change. When the active path dies or the address changes, the **existing session migrates** to another available path **without a new key exchange** (an UPDATE_SA_ADDRESSES-style exchange plus a return-routability check ≈ 1 RTT on the new path). WireGuard's endpoint roaming behaves similarly.
- **Still reactive:** migration starts only after dead-peer detection (same keepalive/DPD parameters as §4.2). Single active path. No standby is pre-warmed. No freshness logic: plane scheduling = §9.1 static priority.
- This is the **"KATIM already roams"** case from §4.4, written as a named, citable mechanism. Our P2 must beat it on outage (by prediction), and P1 must beat it on freshness.

### 11.2 `atsss_active_standby.py`: 3GPP ATSSS-style multi-access steering (TS 23.501 §5.32) [D: 3GPP; I: thresholds]
- **What it models:** a multi-access session keeps **both access legs established at all times**. Traffic is steered by the **Active-Standby** steering mode: switch to standby when the active leg is unavailable or its measured RTT/loss (performance measurement function) crosses a threshold, with hysteresis.
- **Thresholds [I]:** RTT > 2× the bearer's base RTT or loss > 5% held for 1 s; switch back after 5 s of recovery. Swept in S-ATSSS.
- Both legs are always up, so its **standby transmit-seconds = 100%** of the time: the emissions-expensive extreme. P2 must match its outage while cutting emissions. Plane scheduling = §9.1 static priority (ATSSS steers flows; it does not drop superseded data).

### 11.3 Comparison matrix (frozen; drives PDF pages 6–7)
| Mechanism | Standby | Trigger | Freshness logic | Expected strength | Expected weakness |
|---|---|---|---|---|---|
| Documented reactive (§9.1) | none | reactive DPD + 10 s retry + full re-handshake | none | simple | long dark gaps |
| MOBIKE-style (§11.1) | none | reactive DPD + ~1 RTT migration | none | short gaps once detected | still dark during detection; stale queues |
| ATSSS-style (§11.2) | always up | reactive threshold | none | near-zero outage | max emissions/overhead; stale queues |
| Always-warm (§9.2) | always up | reactive | none | — | same cost as ATSSS |
| **Ours: P2 + P1 + predictor** | on demand | **predictive** | **yes** | near-zero outage at low emissions + fresh data | depends on predictor lead time |
| Oracle (§9.3) | perfect | perfect | yes | ceiling | — |

### 11.4 Deliverable mapping
- **Simulation:** this spec.
- **Model:** predictor + discovered policy.
- **Prototype:** `prototype/` Docker emulation, two WireGuard tunnels over `tc netem` links driven by real traces, with a controller implementing the P2 trigger. It measures the live ping/voice gap for documented-reactive vs MOBIKE-style vs ours.

---

## 12. Change log (2026-10-08): post-review fixes to the frozen evaluator

Per §10.1, any change to a frozen file invalidates prior numbers and is logged here. Every number produced before this entry (results/bar.* of 2026-10-07, README table) is superseded. All changes below are review-driven correctness fixes, not tuning; none uses held-out data.

### 12.1 Terrestrial capacity source (§3.2) — [I21]
UCC 5G `DL_bitrate` is application goodput. In the adaptive-streaming captures (Amazon_Prime, Netflix) it is the player's demand (0 whenever the playout buffer is full: median ≈0.28 Mbps, 33–73 % exact zeros), not link capacity. Traffic demand is modelled separately (§5), so using it as capacity was a category error, and it was **not** neutral across mechanisms: stall-triggered switchers (ATSSS, always-warm, oracle) cut over on the artificial stalls. Fix: the capacity-bearing UCC 5G legs (S1, S2, S3, S6) use only the saturating file-**Download** captures (`sim/scenarios.UCC5G_CAPACITY_APPS`); a leg starts at the first second the transfer is flowing (≥1 Mbps, `APP_START_MIN_MBPS`), so a capture's app-start ramp is not read as an outage. Later zeros during a running download are kept as genuine link stalls. Streaming captures stay in the processed corpus (never deleted) but never drive capacity.

### 12.2 Outage = "no plane can deliver" (§8.4) — [I23]
Outage previously counted only tunnel-dark ticks, so a tunnel up over a present, genuinely-zero throughput sample delivered nothing yet was not outage. Now a tick is outage if the tunnel is dark **or** the deliverable goodput after on-wire overhead and loss is below `STALL_GOODPUT_MBPS = 2.4 kbps` (MELPe, the lowest-rate real-time mission flow in the model). Results report outage = dark + stall separately. **Sweep S-STALL** {0, 2.4 kbps, 50 kbps}: on the train baselines the overall outage/transition moves by ≤ ±2.3 s and the mechanism ordering is unchanged.

### 12.3 Per-plane rate caps (§7.3)
`Action.rate_cap_mbps` is now applied: in each tick, plane *p* delivers at most `cap_p × 10⁶/8 × Δt` bytes; capacity a capped plane leaves unused flows to the next plane (work-conserving). Caps are per tick (a policy re-asserts them every tick it wants them).

### 12.4 GEO availability semantics (§3.6) — [I8, I9, I22]
`availability` is now the realised steady-state link-**up** fraction (94 % → ≈6 % of time DOWN), as the §3.6 table intends ("Link availability (fade outages)"). Previously it only set the time-in-fade, and per-tick i.i.d. depth meant ≈0.23 % was actually down (≈99.8 % availability). The rain process now has contiguous fade **episodes**, each with one heavy-tailed depth (Beta(1.5, 2)); the deepest `outage_share_of_fade = 0.5` of fade time is a full outage, the rest is degraded-but-up (RTT toward 3000 ms, loss toward the ceiling, capacity down). Time-in-fade is derived so the DOWN fraction equals `1 − availability` (unit-tested). **S-GEO** additionally sweeps `outage_share_of_fade ∈ {0.25, 0.5, 1.0}`.

### 12.5 Policy isolation (§7.4) — what is actually enforced
§7.4's "enforced by the harness" is now implemented in frozen code (`sim/isolation.py`), outside the editable files: (1) a load-time static gate (`eval.evaluate.load_policy`) rejects, before execution, any import outside `policies.interface` + pure stdlib helpers (no `sim`, `eval`, `pandas`, `numpy`, `os`, `sys`, `importlib` …), file/code/introspection primitives (`open`, `exec`, `eval`, `getattr` …), dunder and frame/traceback access, and any use of the oracle-only instance hook; (2) at run time the scenario instance is bound only to the frozen `policies/oracle.py` class, identified by verified source file / object identity, never by a settable attribute; a non-oracle policy that asks for it fails the run. Only frozen baselines may select a gateway mechanism (`gateway_config`); editable policies run on the pessimistic default. **Scope, honestly:** this is a strong guard against accidental and loop-generated leaks and closes every path the review probe used, but an in-process Python check is not a security sandbox against a determined adversary; a hard guarantee would run the policy in a separate process fed a serialised `Obs` (future work).

### 12.6 Delivery timestamp
A message generated inside a tick is stamped delivered at `max(t, enqueue_t_s) + one_way (+ 20 ms voice packetisation)`; previously the tick start was used, which produced negative one-way delays (31.8 % of voice frames on one S1 run) and alerts "delivered" before generation, biasing every gate optimistically.

### 12.7 Results surface
Every figure carries the §3.6/§3.7 construction caption (GEO = measurement-parameterised model; stitched legs = independently measured, only time-aligned), and every per-scenario table in `results/bar.md` carries the same footnote. The outage bar chart reports per-scenario means with non-negative p10–p90 whiskers. The gate-failure narrative in `bar.md` is computed from the results rather than asserted.

---

## 13. Change log (2026-10-08, evening): anti-gaming, honesty and realism fixes

Per §10.1 every change to a frozen file is logged here (history in §12 is **not** rewritten). Every number produced before this entry (results of the §12 run, the earlier `results/bar.*`) is superseded. All changes are review/operator-driven correctness and realism fixes, not tuning; none uses held-out data. New inferred parameters are numbered continuing Appendix A (I24+). The operator's non-negotiable orders were: apply all four review fixes; make a learned/tuned policy unable to game constant timings or a scenario artefact; make everything as realistic as possible; get the oracle right (a principled ceiling, not tuned to win); add Lumos5G.

### 13.1 Transitions anchored on real events + anti-gaming (fix 1 + realism) — [I24,I25]
The old scenario matrix cut the active bearer at a **constant** scenario time (`t_x = PRE_WINDOW_S = 35 s`). A learned/tuned policy could warm the standby at exactly that constant and "win" without predicting anything — the benchmark was gameable. Fix:
- **No forced cut.** A scenario instance is `(A-leg trace segment, B-leg trace/model)`. The A-leg replays its **real data for the whole episode** and becomes unusable only when its own measurements say so. The external router (common to every mechanism) reactively steers A→B after the A-leg is genuinely bad (unusable or stalled) for `ROUTER_FAILOVER_HOLD_S = 2 s` **[I25]** (sticky within the episode); this data-driven steer — not a clock — is the transition each mechanism handles per its recovery mode. `sim/scenarios.EpisodeInstance`.
- **Event-anchored + randomised** per `(instance, seed)` **[I24]**: the episode length is drawn in `[90,150] s`; a **real degradation event** from the A-leg (`sim/scenarios.degradation_events`: a net_mode downgrade 5G→LTE/3G / CONNECTED→NOT_RESTRICTED, a throughput collapse / Lumos5G 0-Mbps mmWave blockage, or a gap onset — the §6.3 rule restricted to *worsening* events) is placed at a uniform position in `[25%,75%]` of the episode; the A-leg and B-leg start offsets are randomised (seeded). Train/held-out split stays **by trace** (sha1), independent of seed.
- **Obs clock offset + neutral token.** The policy's observable clock (`obs.t_s`, and the enqueue/deadline fields of `MsgMeta`) is shifted by a large seeded constant (`[0, 21337] s`), so durations/head-ages are invariant but absolute time carries no event information. The identity handed to `policy.reset` is a neutral opaque token (`sim/scenarios._opaque_token`), never the scenario name / trace id / dataset. The oracle plans on the bearers' **real samples**, never on the `transitions` list.
- **Diagnostic gaming policy** `policies/diag_fixed_timer.py`: bets on a single constant transition time-since-start (the TRAIN-optimal constant; it can even reconstruct time-since-start by subtracting its first observation). Frozen tests show it **cannot approach the oracle** (its outage stays >2× the oracle's; see `test_fixed_timer_cannot_reach_oracle`) and that the real event onset is not constant / not predictable from Obs time (`test_event_onset_not_constant_across_seeds`). A `_no_randomize` control path (`EpisodeInstance.prepare`) recreates the old fixed schedule for comparison.
- **Collective transitions remain** (S6: all 15 users behind the vehicle gateway share the backhaul event).

**Design deviation (justified).** The operator's wording "assert the fixed-timer gains (near) nothing over documented reactive" assumes an environment where switching early to the standby is costly. In our physics the standby (LEO/GEO/LTE) is usually *usable*, so any early switch legitimately avoids later outage — a blunt always-warm behaviour, not gaming. The honest, defensible and tested claim is therefore: a constant-timing policy **cannot reach the oracle's low-outage-at-low-emissions corner** (foresight is required), and learning the constant confers no schedule-exploiting edge. This is what the tests assert.

### 13.2 Starlink measurement-gap handling (fix 2)
Starlink-on-the-Road logs iperf throughput only in ~15 s bursts every ~55 s; the raw replay modelled the ~40 s between-burst **measurement** gaps as UNUSABLE, fabricating a ~65 s/transition "outage" on S2 that every stall-triggered mechanism reacted to. Fix (`sim/bearers.build_leo_concatenated`, `leo_concatenated_frame`): build the LEO leg by **concatenating the measured seconds** of one drive run in their original order, removing the unmeasured gaps (time-compressed). Gaps are **never filled** — absent seconds are deleted, present seconds carried verbatim; a real measured 0-Mbps second inside a burst is kept and still counts as a stall (`test_starlink_concatenation_never_fills_gaps`). RTT/loss are attached as a **seeded draw from the measured ping-window empirical distribution** of the same campaign, one value per ~25 s window (the honest option: the ping windows are real but not time-synced to the iperf clock, so a labelled seeded draw is used, never a constant, never interpolation). Labelled "measured seconds concatenated; unmeasured gaps removed" in the card, tables and figures. The S2 phantom outage is gone (now <1.5 s/transition); real in-burst stalls remain.

### 13.3 Feasibility-based gates + gradient score (fix 3)
- **Feasibility** (`eval/metrics.FeasRef`, `build_feasref`): a mechanism-independent **max-availability reference** (perfect foresight, standby always ready, ideal switching to whichever bearer can deliver, no crypto cost — pure physics) over both legs. A message is FEASIBLE if, under this reference, some path could have delivered it within its deadline. Built once per `(instance, seed)`, identical for every mechanism.
- **Gates over feasible messages only** (`gates_pass(..., feasible=True)`): alert deadline-hit ≥ 0.99 and voice delay compliance over FEASIBLE messages; the **absolute** rates are always reported alongside (never hidden) in `bar.md` and `bar.csv`.
- **Gradient score** (`eval.evaluate.scalar_score`): the `-1e6` cliff is replaced by `score = score_obj − GATE_PENALTY·gate_shortfall`, where `gate_shortfall` (`eval/metrics.gate_shortfall`) is the non-negative distance from passing the feasible gates (a smooth gradient the loop can climb) and PASS/FAIL is reported separately. `score_obj` is a dimensionless margin over the documented baseline defined so it is **exactly 0 at baseline parity** (the §12 normalisation left parity at ≈−2.6 — the "earlier normalisation bug"). Emissions metrics are normalised by an always-up reference (always-warm), because the documented baseline spends ~0 there and a 0 scale is undefined.

### 13.4 The oracle, done right (fix 4 — highest priority)
The oracle (`policies/oracle.py`) is rebuilt as a **planner**, not a heuristic. Principle: it is the upper bound of the on-demand make-before-break family under the SAME physics, so by construction its outage must be ≤ always-warm and ≤ ATSSS on every run. It is defined **lexicographically**: (1) minimise outage seconds, (2) then standby transmit-seconds, (3) then overhead bytes. With full future knowledge of **both legs' real samples** it builds the minimum-outage committed-leg assignment, then enumerates a small set of *realisable* action schedules (including an always-warm-equivalent single switch, cut over **pre-emptively** with foresight so never later than always-warm's detector) and **evaluates each through the real frozen engine** under the per-tick handshake physics (§13.5), keeping the lexicographically best. Because the always-warm-equivalent schedule is always a candidate and the oracle cuts over no later than always-warm, the chosen plan can only match or beat always-warm (and ATSSS). The property is **asserted on every train run** (`eval.evaluate.check_oracle_lexicographic`, `test_oracle_outage_le_alwayswarm`): **0/160 violations**. The first (analytic, late-warm) version had 2/160 violations (an unrealisable late-warm switch and a missed initial A-bad switch); per the operator's instruction those were tracked to root cause and fixed by *evaluating realisability* rather than assuming it — not hidden. An **ORACLE FRONTIER** (`oracle_frontier`, budgets 0/5/10/25/50/100 % of standby-transmit) is computed through the engine and plotted (emissions trade-off) and tabled; it is reported as a monotone achievable envelope. The oracle is not tuned to any scenario.

### 13.5 Realistic handshake / recovery physics (realism, needed for 13.4)
`sim/gateway.py` now advances the handshake **per tick**: `rounds` sequential round trips (each costing one RTT on the bearer it runs over) then serialising the key material at the bearer's **current** capacity; progress freezes on an unusable/stalled bearer; an attempt that cannot complete within `HS_ATTEMPT_TIMEOUT_S = 10 s` FAILS and the documented 10 s retry schedules the next attempt. DPD is time-based (missed keepalives) and declares the peer dead whether or not the bearer is usable, and the 10 s retry cadence keeps ticking on a dead bearer (`test_handshake_progresses_only_while_usable`, `test_handshake_times_out_and_retries_on_dead_bearer`). The same model runs for every mechanism, the oracle included. The §12 realism points (sub-tick delivery timestamps, rate caps, GEO availability semantics) are unchanged and still correct.

### 13.6 Lumos5G scenarios (fix F) — added to the frozen matrix
Two scenarios are added (`SCENARIO_IDS`):
- **S7 "dismounted walk"**: Lumos5G walking mmWave 5G (A-leg; real 0-Mbps blockages and 5G↔LTE flips as anchor events) carried by a man-portable **X9000R** gateway, whose throughput is capped to **≤100 Mbps IMIX** (`CAP_X9000R_MBPS`; X:41); standby B-leg = UCC 4G pedestrian LTE (stitched, labelled).
- **S8 "vehicle drive"**: Lumos5G driving mmWave 5G (A-leg) capped to the **9001-R ≤1 Gbps** (`CAP_9001R_MBPS`; R:53); standby B-leg = Starlink LEO (concatenated, §13.2, labelled stitched).
Lumos5G has no RTT/loss, so terrestrial latency uses the existing per-class default (and the GEO/LEO legs carry their own measured/modelled RTT); capacity/signal/mobility come from Lumos. Both are in the frozen matrix with train/held-out by trace (`test_lumos_scenarios_present_and_capped`). `TraceReplayBearer` gained a `cap_ceiling_mbps` applied to delivered capacity (raw dl/ul still surfaced for telemetry honesty).

### 13.7 Re-run
`run_baselines.py` re-runs on TRAIN only and regenerates `results/bar.csv`, `results/bar.md` (per-scenario tables; feasible AND absolute gate rates; the oracle lexicographic PASS/violation report; the oracle emissions frontier table; a diagnostic `diag_fixed_timer` row) and the figures: the transition timeline (a REAL-event-anchored transition, dashed line = the §6.3 event not a scenario constant), outage bars with **median + IQR (p25–p75)** whiskers (no negative whiskers), and the emissions trade-off with the **oracle frontier curve**. GEO = model, stitched = stitched, Starlink = concatenated are labelled on every figure/table.

---

## 14. Change log (2026-10-08, late): red-team round 2 + full realism pass (§13.8)

Per §10.1 every change to a frozen file is logged; §12/§13 history is **not** rewritten. Every number before this entry (the §13 `results/bar.*`) is superseded. All changes are review-driven correctness/realism/anti-gaming fixes, not tuning; none uses held-out data. New inferred parameters continue Appendix A (I20–I38) and each has a named sweep implemented in `eval/sweeps.py` (`python -m eval.sweeps`). A new module `sim/params.py` holds every [I] knob as one named field read at use time, so a sweep overrides it without editing frozen logic.

The operator's standing orders for this round: **apply all four review fixes; make a learned/tuned policy unable to game constant timings or a scenario artefact; make everything as realistic as possible; get the oracle right as a principled ceiling, not tuned to win; add Lumos5G** (already added in §13.6, hardened here).

### 14.1 Anti-gaming: no run identity reaches the policy (fix 1, red-team highs)
The §13.1 "neutral token" (`sha1('tok|inst_id|seed')`) and the clock offset (seeded by `sha1(inst_id|seed)`) were each a deterministic function of **public** inputs (trace ids on disk, seeds hard-coded in the evaluator), so a policy could carry a precomputed table keyed by the token or by its first `obs.t_s` and replay the oracle's schedule (the probe `scratch_redteam/gen/lookup_*` did exactly this). Fixes (`sim/scenarios.py`, `sim/engine.py`, `sim/params.py`):
- `policy.reset` now receives a **constant** neutral token (`NEUTRAL_TOKEN = "episode"`) and, for every non-oracle policy, **seed 0** — no inst_id, no seed, nothing that identifies the run. Only the privileged oracle receives the real seed (it needs it to evaluate candidate plans through the engine under the same seeded traffic). `test_obs_clock_offset_and_neutral_token`.
- the observable-clock offset is drawn from an **evaluator-secret salt** (`sim.params.eval_salt`, env `KATIM_EVAL_SALT`; a fresh per-process random salt when unset), so the held-out offsets cannot be computed from trace ids + seeds. `test_clock_offset_not_computable_from_public_inputs`. No metric depends on the offset (metrics use true engine time), so baseline results stay bit-reproducible within a process.
- the held-out EPISODE randomisation (window position, length, B-leg offset, retry phase) can be put behind a second secret salt (`KATIM_EPISODE_SALT`) so held-out episodes cannot be precomputed either. `test_episode_randomisation_secret_for_heldout`.

### 14.2 Emissions loophole closed: satcom/secondary airtime is charged (fix, red-team highs)
The emissions proxy charged only standby **warming** seconds, so "warm B at t=0, cut over, and stay" transmitted the most on the satellite/secondary RF link yet looked almost free — the opposite of the EMCON intent, and a route around the anti-gaming design (the probe `cheat_flee_to_b.py` / the `diag_fixed_timer` out-scored ATSSS this way). Fix (`sim/engine.py`, `eval/metrics.py`, `eval/evaluate.py`): the emissions axis is now **emission transmit-seconds** `emission_tx_s` = airtime on the **non-primary / satcom leg whatever its role** — a standby warming/ready on B **plus** the active tunnel riding the B leg. The scalar score's `W_TX` term and the oracle's lexicographic stage 2 and the frontier all use `emission_tx_s`; `standby_tx_s` and `secondary_active_s` are still reported separately. Now "flee to B and stay" pays its real airtime, and the `diag_fixed_timer` gamer no longer out-scores ATSSS / always-warm on fresh seeds (`test_flee_to_standby_is_not_free`, `test_constant_timer_gamer_does_not_beat_standards_on_score`). The misleading "cannot be gamed" wording in `bar.md`/§13.1 is replaced: the gamer is reported against **all** mechanisms, not only the oracle.

### 14.3 Seed stability (fix, red-team medium)
The scalar score was seed-sensitive enough to flip rankings on 5 seeds. TRAIN now uses **20 seeds** (`SEEDS_TRAIN = range(20)`), held-out a disjoint 20-seed set; `bar.md` reports the mean with the across-run spread. [I36] `N_SEEDS`.

### 14.4 The oracle, done right — a true ceiling by construction (fix 4, highest priority)
Three oracle-ceiling highs are fixed in `policies/oracle.py`:
- **Deliverability match.** The planner's "good tick" now uses the **same `link_ok` rule as the engine** (capacity×(1−loss) ≥ the stall floor), reading the UL-direction capacity. The earlier overhead-blind `cap*(1-loss) >= 2.4 kbps` test let a stalled-but-present tick look good, so the oracle stayed on A where the engine stalled; that gap is closed.
- **Enumerate a dominating candidate set, evaluate through the real engine.** Besides stay-on-A, go-to-B-at-0 and the foresight multi-switch (warm early and line-searched late), the planner now enumerates a **single A→B cut at every outage-relevant cut time** — every tick where `goodA` changes (±1) and, decisively, **every cut tick the same-mechanism baselines actually realise on this run** (their committed-to-B onsets), each warming B from the start so the standby is certainly ready. This finite set provably contains the best single switch, so it **dominates any fixed-timer single switch on outage** — the diagnostic gamer included. Every candidate is scored through the real frozen engine; the lexicographically best `(outage, emission_tx, overhead)` is kept.
- **Lexicographic stage 2 achieved.** The min-outage committed-leg assignment now **prefers the primary leg A whenever A can deliver**, riding B only while A cannot, so at equal (minimum) outage the plan spends the least airtime on the emitting leg (the stage-2 objective) instead of sticking on B. Warm starts are line-searched to the latest safe tick.

The asserted property is now **oracle outage ≤ the outage of every same-mechanism policy on every train run** (always-warm, ATSSS, documented, the `diag_fixed_timer` gamer, and any submitted P1/P2), checked and reported by `eval.evaluate.check_oracle_lexicographic` and `test_oracle_outage_le_alwayswarm`: **0 violations** across the train set (previously simple schedules and the gamer beat it on several runs). **Scope, stated honestly:** the oracle is the ceiling for the **make-before-break family under the documented reactive-recovery physics**. MOBIKE-style roaming uses a *different, structurally faster recovery mechanism* (~1 RTT migration vs a full re-handshake); on single-leg scenarios (S1) where make-before-break offers nothing, MOBIKE can legitimately beat the reactive-physics oracle. That is **reported as a finding** (a predictive policy should combine prediction with migration), not counted as a ceiling violation — the oracle is not handed the migration lever it is not defined to have.

**Oracle frontier redefined** (`eval.evaluate.oracle_frontier`): the budget is now a cap on **realised emission-tx seconds** (a fraction of episode length), and each point is the minimum-outage plan within the budget from the **same evaluated candidate set** as the headline (plans are cached, so the whole curve is near-free). The curve is a true, monotone, engine-realised achievable envelope — 0 % = the no-standby (stay-on-A) run, 100 % = the headline — with no running-minimum smoothing. ATSSS / always-warm are plotted as points **above** it (they reach low outage only by spending near-full-episode emission).

### 14.5 Realistic failover, DPD and handshake physics (fix 3 + realism highs)
- **Deliverability-driven usability & DPD** (`sim/engine.py`, `sim/gateway.py`, `sim/scenarios.link_ok`). A bearer that keeps reporting samples but carries **0 Mbps (or below the stall floor)** is no longer treated as "usable": the engine's commit/failover and the gateway's keepalive/DPD are driven by deliverable goodput, so a stalled committed leg declares dead-peer and the router steers to B exactly as a dark leg would (`test_zero_capacity_below_floor_triggers_dpd`). Documented/MOBIKE no longer sit "up" over a dead 0-Mbps link.
- **External router fail-back** [I26]. The router now returns to the primary A leg once A has been good for `router_failback_hold_s` (it was sticky-to-B forever), so riding B is never free and reactive mechanisms recover the primary. S-ROUTER-FAILBACK, S-ROUTER-HOLD.
- **Documented 10 s retry phase** [I29]. The reactive first attempt no longer always lands a full 10 s after the break (a worst-case reading). `retry_phase ∈ {immediate, free (seeded free-running phase, default), worst}`, swept S-RETRY-PHASE.
- **Handshake packet loss** [I28]. Each round's RTT cost is inflated by the expected retransmits `1/(1−loss)²` and the key material serialises at the loss-reduced goodput `cap·(1−loss)` (deterministic expected-cost, so runs stay bit-reproducible). S-HS-LOSS, S-HS-RTO, S-HS-TIMEOUT.
- **Handshake uses min(DL, UL)** (SPEC §4.3) via the new `cap_dl_mbps` passed to the gateway, not downlink alone.
- **Standby liveness / DPD** (`sim/gateway.py`). A warm standby whose bearer goes unusable now loses liveness by the same time-based DPD and must re-handshake; a cut-over onto a standby whose bearer is unusable this tick is refused; keepalives keep being charged while the candidate is down (`test_handshake_progresses_only_while_usable`).
- **Telemetry honesty** (`sim/engine.py`). `rtt_trend` is the change in the gateway's own keepalive-RTT EWMA (it leaked the exact current trace RTT before, even while dark); RTT/tput telemetry **freezes while the tunnel is dark** (no keepalive echo) and ages via `telemetry_age_s`, which is quantised to ≤120 s so a "never measured" 1e9 cannot fingerprint a dataset.

### 14.6 Capacity = uplink direction (realism high) — [I30]
The mission traffic is uplink-dominant (team gateway → command, §5), so a bearer's capacity is its **red→black (uplink)** capacity, not downlink. Where UL was measured with a saturating test it is used directly (Starlink iperf `upload_mbps`; the GEO model's `ul`). The UCC 4G/5G Download captures log UL as the ACK stream of a download (UCC 4G median 0.107 Mbps) — demand, not capacity, the same category error as §12.1 — and Lumos5G / Meireles log DL only; for those, UL capacity = measured DL × a documented **UL/DL ratio** (`ul_dl_ratio_cellular = 0.53`, the Berlin V2X saturated-LTE medians UL 20.8 / DL 39.0 Mbps on the same loop; Wi-Fi 1.0), swept S-CAPDIR. `test_capacity_is_uplink_direction`.

### 14.7 Starlink ping attachment (realism medium) — [I32]
Each concatenated measured second now carries its **own run's** adjacent ping window (the most recent window at/before it, from `ping_windows.csv`, on the same session clock per the card's Q3) in original order — real same-run RTT/loss preserving the obstruction correlation — instead of a draw from the whole-campaign pool (which included 107 pre/post-drive windows with 4× the in-drive loss and decoupled 98 %-loss windows from 300 Mbps seconds). `leo_rtt_mode ∈ {adjacent (default), pooled}`, swept S-LEO-RTT. Concatenation now keeps the measured **upload** seconds (the capacity direction).

### 14.8 UCC G-NetTrack ping-second artefact (realism high) — [I34]
Most UCC 5G "degradation events" were artefacts of the ~14 s ping cycle (77–86 % of collapses fall on a ping second vs ~21 % by chance), with duplicated same-second rows and an RTT sawtooth. The parser-level fix is applied at load (`TraceReplayBearer._clean_gnettrack`): the throughput of each **ping-logging second is removed** (left absent, so ordinary §2.2 hold within H_tput applies — never filled) while its RTT/loss are kept, and duplicated same-second rows are collapsed to the last real value. Anchor events are then extracted on the **cleaned, UL-direction, gateway-capped capacity the gateway actually sees** (`TraceReplayBearer.capacity_series`) and must **persist `anchor_min_persist_s`** (S-ANCHOR), so a ping-second blip or a dip that the 100 Mbps X9000R cap flattens cannot anchor an episode. S-UCC-PINGSEC toggles the cleaning for the sweep.

### 14.9 RTT where a trace has none (realism medium) — [I31, I37]
Lumos5G, UCC 4G and the HSR sparse-ACK legs have little or no RTT. Instead of a silent per-class constant (which also fingerprinted the dataset via `telemetry_age = 1e9`), RTT/loss are a **labelled seeded draw from the measured UCC 5G Download ping samples, conditioned on the row's access mode** (5G / LTE / HSPA), one paired RTT+loss draw per ping-cadence window (14 s, S-RTT-DRAW), held within the window. Only the Wi-Fi leg (no Wi-Fi capture measures RTT anywhere) keeps a per-class default, scaled by S-RTT-DEFAULT. `test_lumos_leg_has_drawn_rtt_not_fingerprint_constant`.

### 14.10 GEO rain-fade duration (realism medium) — [I33]
Rain fades are now **minutes-scale** (mean `geo_mean_fade_s = 180 s`, bracketing ITU-R P.1623 Ku-band fade-duration statistics) and the fade process **starts each episode from its stationary state**, so the 94 % availability holds **across** episodes (most 90–150 s episodes see clear sky, a few sit inside a long fade) instead of the §12.4 8 s fades that put a short GEO outage inside nearly every episode. S-GEO-FADEDUR; the realised down-fraction = 1 − availability over long total time (`test_geo_availability_equals_realised_up_fraction`).

### 14.11 Scenario realism (realism mediums)
- **S7 standby is pedestrian LTE** (`pedestrian_*` UCC 4G ids), not bus; **S4 standby is car LTE** (`car_*`). `test_s7_standby_is_pedestrian_not_bus`.
- **S7/S8 anchors** are extracted on the gateway-capped deliverable capacity (§14.8), so the X9000R/9001-R cap can no longer turn a 1500→250 Mbps dip into a non-event; the walking 0-Mbps mmWave runs anchor S7.

### 14.12 Re-run
`run_baselines.py` re-runs on TRAIN only (20 seeds) and regenerates `results/bar.{csv,md}` (overall + per-scenario tables, feasible AND absolute gate rates, the oracle lexicographic report over **all** policies + the MOBIKE finding, the oracle **emission** frontier with realised emission-tx) and the figures (real-event-anchored timeline; outage median+IQR bars; the outage-vs-**emission** trade-off with the oracle frontier). `python -m eval.sweeps` emits the Appendix-A sensitivity tables.

### Appendix A (continued) — [I] register for §13/§14 (each with its sweep)
| # | Assumption | Default | Sweep (name) | Basis |
|---|---|---|---|---|
| I20 | Policy isolation guard | static+runtime | — (structural, §12.5) | enforce §7.4 |
| I21 | Terrestrial capacity = saturating Download captures | Download only | — (§12.1) | app goodput ≠ capacity |
| I22 | GEO outage share of fade | 0.5 | S-GEO {0.25,0.5,1.0} | rain fade depth split |
| I23 | Stall goodput floor | 2.4 kbps | S-STALL {0, 2.4 kbps, 50 kbps} | MELPe lowest real-time flow |
| I24 | Episode length / event position / clock offset | 90–150 s / 25–75 % / secret | S-EPISODE-LO | randomise, anti-game |
| I25 | Router failover hold | 2 s | S-ROUTER-HOLD {1,2,5} | reactive steer debounce |
| I26 | Router fail-back to primary | on, 10 s hold | S-ROUTER-FAILBACK {off,on} | riding B is not free |
| I27 | Handshake attempt timeout | 10 s | S-HS-TIMEOUT {5,10,20} | = documented retry period |
| I28 | Handshake packet-loss model / RTO | on / 1 s | S-HS-LOSS, S-HS-RTO {0.5,1,5} | Noise/IKE retransmit |
| I29 | Documented 10 s retry phase | free (seeded) | S-RETRY-PHASE {immediate,free,worst} | WP:323 "every 10 s" |
| I30 | Capacity direction / UL-DL ratio | uplink / 0.53 (cell), 1.0 (wifi) | S-CAPDIR, S-CAPDIR-WIFI | uplink-dominant traffic (§5); Berlin V2X measured |
| I31 | RTT where a trace has none | mode-conditioned UCC 5G ping draw | S-RTT-DRAW {5,14,30}, S-RTT-DEFAULT {0.5,1,2} | real measured ping samples |
| I32 | Starlink RTT/loss attachment | adjacent same-run window | S-LEO-RTT {adjacent,pooled} | card Q3 same session clock |
| I33 | GEO fade duration | 180 s, stationary start | S-GEO-FADEDUR {60,180,600} | ITU-R P.1623 |
| I34 | UCC ping-second artefact handling | drop ping-second throughput | S-UCC-PINGSEC {keep,drop} | 77–86 % of events on ping rows |
| I35 | Anchor-event minimum persistence | 2 s | S-ANCHOR {0,2,5} | artefact vs real degradation |
| I36 | Train seed count | 20 | — | ranking stability |
| I37 | RTT draw conditioned on access mode | by 5G/LTE/HSPA | part of S-RTT-DRAW | mode-specific latency |
| I38 | GEO availability | 94 % | S-GEO {0.94,0.99,0.996} | IIUM SES-12 |

*End of §14.*
