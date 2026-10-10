# Real Link Traces: Vetted Shortlist

Vetting for the frozen simulator (P1 freshness + P2 make-before-break). Run on 2026-10-07: 5 category researchers, then an adversarial verifier per category, which opened each dataset's own page, then a completeness critic. 34 candidates were found, 33 kept (many with caveats) and 1 rejected for its category.
Rule: **real measured data only.** Where no real trace exists, we parameterise from real measurements and say so.

Status key:
- **open** = downloadable now, no login.
- **login** = free IEEE DataPort account; the user signs in, I can't.
- **ask** = data exists but reuse terms are unclear; ask the authors.
- **cite-only** = paper data is not released.

---

## 1. Core set: download these first

| # | Dataset | Bearer · mobility | What it gives us | Size | Licence · access |
|---|---|---|---|---|---|
| 1 | **UCC/MISL 5G**: Raca et al., *Beyond Throughput, The Next Generation*, ACM MMSys 2020 · github.com/uccmisl/5Gdataset | 5G ↔ LTE · static + **in car** | 1 s DL+UL throughput with a logged **5G↔LTE NetworkMode switch**: a real bearer-mode transition to replay | 2.18 MB | GPL-3.0 · **open** |
| 2 | **Starlink on the Road**: Laniewski et al., TMA 2024 (arXiv:2403.13497) · github.com/sys-uos/Starlink-on-the-Road | LEO · **van driving** (urban 0–50, highway >100 km/h) | 1 s DL+UL throughput with speed; RTT/loss only every ~90–100 s | ~1.9 MB | CC BY-SA 4.0 · **open** |
| 3 | **Lumos5G**: Narayanan et al., IMC 2020 · ieee-dataport.org/node/3598 | 5G mmWave · walking + driving | 1 s DL throughput + RSRP/SINR + tower_id + nrStatus; the sharpest real capacity collapses | 1.53 MB | CC BY 4.0 · **login** |
| 4 | **HSR-MPTCP**: Li et al., ACM SIGCOMM 2018 · github.com/superlitong/hsr-mptcp | Multi-carrier cellular + Wi-Fi subflows, MPTCP · high-speed rail | **The multi-bearer source:** real simultaneous subflows with packet-level throughput/RTT/loss, handover-dense | ~50 MB | MIT · **open** |
| 5 | *(cite only)* **Satellite-vs-Cellular**: Hu et al., CoNEXT Companion 2023 | Starlink + 3 carriers simultaneously in one car | The only real simultaneous satellite + cellular capture, but **no licence**. Decision: **not used as data.** We cite its published findings only, so nothing depends on an author reply | — | none · excluded |
| 6 | **LENS**: Zhao & Pan, MMSys 2024 · github.com/clarkzjw/LENS + monthly Zenodo snapshots | LEO · stationary | 1 s latency + loss over long runs; carries the **~15 s Starlink reconfiguration dips** | **≥18 GB** per monthly snapshot (compressed) | CC BY-SA 4.0 · **open**, but large; pick one month |
| 7 | **Wi-Fi pair**: Meireles et al. V2I (zenodo.org/records/6884095) + CRAWDAD dartmouth/campus movement trace (DOI 10.15783/C71593) | Wi-Fi · driving past roadside AP / campus roaming | Meireles: 1 s CSV of the **coverage-cliff** throughput. Dartmouth: real **AP-to-AP reassociation** timing (no throughput) | 11.3 MB CSV / 49.1 MB | CC BY 4.0 **open** / CRAWDAD terms **login** |
| 8 | **Berlin V2X**: Hernangómez et al., VTC 2023-Spring · DOI 10.21227/8cj7-q373 | LTE, two operators · driving 17 km loop incl. tunnel | **Best handover ground truth**: event-based cell identity, datarate dips at handover, ping latency, packet-error rate. Used to validate our handover-extraction rule | 24.4 MB (cellular file) | Licence not stated · **login** |

**GEO satellite (no open per-second trace exists):** anchor levels on **FCC Measuring Broadband America**, satellite subset (ViaSat/HughesNet). It's public-domain, regulator-grade throughput, latency and loss (≈600–700 ms RTT), but only per test, not per second. Add rain-fade RTT physics from published measurements (IIUM SES-12 study 2025; Thomas, Dubois, Kuhn & Lochin 2019, free copy hal-02401946). **We label the GEO bearer "measurement-parameterised", not "replayed".**

## 2. Coverage against what the simulator needs

| Need | Status | From |
|---|---|---|
| 5G walking | Covered | Lumos5G (+ Sunway multi-operator, UCC 4G fallback) |
| 5G driving | Covered | Lumos5G, UCC 5G (real 5G↔LTE switch), Berlin V2X (LTE), Mahimahi (LTE, 1 ms capacity) |
| Wi-Fi AP-to-AP roaming | **Partial** | No set has organic roaming *and* throughput. Compose: Dartmouth roaming timing + Meireles / 802.11ad per-AP throughput |
| GEO | **Partial** | Levels from FCC MBA; dynamics parameterised; **no open ≤1 s GEO throughput trace** |
| LEO stationary | Covered (latency/loss) | LENS, Starlink one-way-delay set (Zenodo 16275284, 5.9 GB) |
| LEO in motion | Covered, split | Starlink on the Road (road-speed throughput) + Starlink Robot (10 Hz RTT showing the ~15 s step changes, walking speed) |
| Simultaneous multi-bearer | Covered but **licence-fragile** | Satellite-vs-Cellular (ask), HSR-MPTCP (MIT fallback) |
| GEO + anything simultaneous | **Gap** | None public. Build the overlap from two real single-bearer traces, aligned by the policy; state it |
| Labelled handover events | **Gap (derivable)** | No set ships a labelled column. Write one documented extraction rule per bearer (cell-id change, NetworkMode flip, AP change, RTT step) and validate it on Berlin V2X |

## 3. What this means for the proposal (say these in the write-up)

1. **Overlap uses openly licensed data only:** HSR-MPTCP gives real simultaneous multi-carrier subflows. The satellite + cellular overlap is stitched from two real single-bearer traces (e.g. UCC 5G driving + Starlink on the Road), stated as such. We cite Satellite-vs-Cellular's published findings to show the overlap behaviour is realistic.
2. **GEO is a calibrated model**, not a replay. No public per-second GEO trace exists. That gap is itself worth a sentence: it's why KATIM-class users would benefit from GEO measurement.
3. **Handover events are extracted by a published rule**, not hand-labelled, so anyone can reproduce them from the raw traces.
4. **Wi-Fi roaming is composed** from two real traces: timing from one, throughput from the other.

## 4. Actions

- [ ] **Create a free IEEE DataPort account** (you, not me) for Lumos5G, Berlin V2X and CRAWDAD.
- [ ] Open the UCC 5G zip and confirm column names (not yet inspected).
- [ ] Pick one LENS month: the smallest snapshot, about 18 GB.

## 5. Supporting sets (kept with caveats, not core)

| Dataset | Why not core |
|---|---|
| UCC/MISL 4G LTE (Zenodo 1219679, 6.5 MB, CC BY 4.0) | LTE only; good pedestrian/tram/train breadth |
| Mahimahi cellular traces (github ravinet/mahimahi, GPL-3.0) | Capacity only, at 1 ms resolution; no RTT/loss/signal |
| 5Gophers (IEEE DataPort, 254 KB, CC BY 4.0, login) | Handoff microbenchmarks; use for calibration |
| Multi-operator Sunway City (Mendeley 8vvn3k3hmz, CC BY 4.0) | 3 operators concurrently, but cellular only; no RTT/loss |
| Vienna 4G/5G drive test (Zenodo 18322066, 788 MB, CC BY 4.0) | Throughput presence and sampling rate unconfirmed; paper link unverified |
| Starlink Robot (starlinkrobot.github.io, MEGA) | 10 Hz RTT shows handovers clearly, but throughput only every 30 s, walking speed, data licence ambiguous |
| Starlink one-way delay (Zenodo 16275284, 5.9 GB, CC BY 4.0) | Delay only; useful if up/down asymmetry matters for tunnel bring-up |
| Multifaceted Starlink, WWW 2024 | Best description of the ~15 s reconfiguration; data licence unverified, rsync access |
| WetLinks (sys-uos, TMA 2024) | Throughput only every 3 min, stationary, no licence file |
| LEO Access Link Traces (Choi & Paek, IEEE DataPort, 91.7 GB) | Technically the richest per-packet Starlink set, but **paid subscription**; reproducibility risk |
| V2I 802.11ad (Zenodo 7026550, 662 MB) | 60 GHz coverage cliff + beam-switch events; single AP |
| CRAWDAD mobisteer / cambridge-inmotion / WiPerf | Old 802.11b/g, login, or CC BY-NC-ND |
| M-Lab NDT satellite ASNs (CC0) | Satellite ISPs' proxies end TCP before the satellite hop, so RTT usually **excludes** the GEO hop |
| FAU QUIC-over-GEO, WPI SEARCH (Viasat), IIUM SES-12 | Real GEO studies with no released traces: **cite-only** |
| Ghent 4G logs (van der Hooft et al. 2016; users.ugent.be/~jvdrhoof/dataset-4g/) | Found by the critic; 4G walk/drive breadth |

Rejected: WetLinks for the multi-bearer category, since it's stationary and single-bearer; it's still listed above as a supporting set.

Unconfirmed leads: Salzburg Research dual-operator highway set (WoWMoM 2025 paper real, dataset record not found) · FjordLink Starlink vs 5G on vessels (LEO-NET 2025; data not confirmed) · Czech 3-operator moving measurements · ESA Arctic Ka-band characterisation.
