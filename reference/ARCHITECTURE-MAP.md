# KATIM Gateway — Architecture Map (from public sources)

Target: **KATIM Gateway 9001-R** (rugged, "in-motion deployments"), with the family (9001, 9011, X9000R) as context.
Every claim is tagged **[D]** documented (with source) or **[I]** inferred (with reasoning). Judges from EDGE will know where we guessed — keep the tags honest.

## Sources (local copies in `docs/`)

| Key | Document | URL |
|---|---|---|
| WP | Gateway 9011 Security Features white paper, 12 pp, 2023 — the de-facto protocol handbook | https://products-webfiles.edgegroup.ae/s3fs-public/2023-06/20230508-katim-gateway-network-security-12-pg-12052023_eng.pdf |
| R | 9001-R datasheet, 2025 | https://products-webfiles.edgegroup.ae/s3fs-public/2025-02/katim-gateway-9001r.pdf |
| 9001 | 9001 datasheet, 2025 | https://products-webfiles.edgegroup.ae/s3fs-public/2025-02/katim-gateway-9001.pdf |
| 9011 | 9011 datasheets (2023, 2025) | https://products-webfiles.edgegroup.ae/s3fs-public/2025-02/katim-gateway-9011.pdf |
| FAM | Gateway family datasheet, 2024 | https://products-webfiles.edgegroup.ae/s3fs-public/2024-08/1-gateway-family-datasheet.pdf |
| X | X9000R datasheet, 2026 (newest, edge/SWaP) | https://edgesolutions4export.edgegroup.ae/wp-content/uploads/2026/04/KATIM-Gateway-X9000R.pdf |
| PORT | KATIM portfolio 2024 | https://edgesolutions4export.edgegroup.ae/wp-content/uploads/2024/10/KATIM-Portfolio-2024-Print.pdf |
| NOK | KATIM–Nokia MoU (IDEX 2025): Military 5G, "5G in-a-box" | https://edgegroup.ae/news/edge-group-and-nokia-collaborate-strengthen-secure-communications-defence-sector |

No public operator manual was found; the WP is the deepest source. EDGE web pages are bot-blocked (403) — read them in a browser if needed.

---

## 1. Physical / system architecture

- **[D]** Red / crypto / black separation; on the 9001-R it is "on shared, programmable H/W" (FAM), on the 9011 on dedicated H/W. Three-level isolation (WP p3).
- **[D]** 9001-R: **1 active plain port + 1 active cipher port**, RJ-45 or SFP; up to 1 Gbps bidirectional; DC 12/24/48 V; passive cooling; −40…+60 °C (R, FAM).
- **[D]** X9000R: 540 g, USB-C, ≤100 Mbps IMIX, **latency < 5 ms**, OSPF, managed by "KATIM Crypto Network Manager (CNM)" (X).
- **[I]** With a single cipher port, the gateway has **no native multi-bearer (Wi-Fi / 5G / satcom) awareness**. Bearer selection and handover happen in an external router/modem on the black side, so they are *invisible* to the encryptor except as loss, delay and endpoint changes. This is the central structural fact for our challenge.

## 2. Tunnelling protocol (KATIM Gateway Tunnelling Protocol, "KGTP")

- **[D]** Custom protocol "based on a proven public cryptographic protocol design", reduced in features, with PQ additions; formally reviewed by a third party (WP p8).
- **[D]** Claimed differentiators vs IPsec/TLS: identity hiding, PQ support, enforced re-authentication, **stealthiness**, frequent key ratcheting, **simplified handshake** (WP Table 3).
- **[I]** That feature set (identity hiding, stealth = silent to unauthenticated packets, simplified handshake, periodic re-handshake, "quad DH") matches the **Noise Protocol Framework / WireGuard** family very closely. A WireGuard + PQ handshake (e.g. the open-source Rosenpass approach) is therefore a defensible **emulation stand-in**. Say "Noise-family stand-in", never "it is WireGuard".
- **[D]** Two handshakes: (1) **discovery handshake** with deployment-wide static discovery keys for identity hiding; (2) session handshake with device-unique classical + PQ keys — "quad Diffie-Hellman and triple KEM" (WP p5, p8).
- **[D]** Classical (NQ) and PQ key material are mixed; **independent keys per direction per QoS plane** are derived (WP p5, Diagram 1).
- **[D]** Crypto suite is locked per software release, so there is no negotiation and no downgrade (WP p4).
- **[D]** Underlay: L2 or L3 overlay; P2P, (M)P2MP hub-and-spoke or mesh; policy-based routing, static routing, OSPFv2 (R). On the 9011, dynamic routing is listed as "future" (9011).

## 3. Timers and counters (the mobility-relevant core)

| Mechanism | Value | Tag |
|---|---|---|
| Discovery + handshake **retry** | every **10 s** | [D] WP Table 5 |
| Full **re-handshake** (re-auth, all keys refreshed) | typically **every 1 min**, configurable | [D] WP p5, Table 5 |
| Data-plane **key ratchet** | every **131 072 packets**, autonomous, per tunnel, no control messages | [D] WP p6 |
| Control-plane keepalive | "tuned for fast dead peer detection"; value not published | [D] exists / [I] value unknown |
| Data-plane keepalive | keeps ratchet state **synchronised** between peers | [D] WP Table 5 |

Derived key lifetimes (WP Table 2): 1 Gbps at 1500 B gives 1.5 s; 100 Mbps IMIX gives 3.7 s.
**[I]** On a tactical satellite bearer (around 2 Mbps, mostly small packets), one ratchet epoch is minutes long. On fast links it is sub-second. Key lifetime is *rate-coupled*.

## 4. QoS architecture

- **[D]** Up to **8 QoS-aware encryption planes** per gateway-to-gateway association, each with independent keys (R, WP p8).
- **[D]** "Each plane uses separate keys, and impact from traffic shaping or policing in the transit network is isolated to that plane" (WP p8).
- **[D]** Plain-to-plane mapping is "user-configurable or automatic" (R). The 9011 docs add "QoS remarking on plain/cipher" (9011).
- **[D]** QoS marking of the *tunnels* that make up a plane is user-configurable (WP p8). So the black network sees per-plane DSCP.
- **[D]** Internal classes of service with configurable mapping give "optimised latency and discard priority on congestion within the gateway" (R).
- **[D]** Marketing: "content-aware Quality of Service" (PORT); "application identification" (EDGE infrastructure-security page, via search snippet).
- **[I]** "Automatic mapping" is almost certainly classifier-driven: DSCP, ports, or application signatures mapped to a plane through a static table. No public evidence of adaptation to link state.
- **[I]** Why per-plane keys matter for QoS: a single anti-replay window per tunnel would drop low-priority packets reordered behind high-priority ones in the transit network. Separate planes avoid that. (This is a known IPsec problem; RFC 4301 recommends per-class SAs.)

## 5. Management

- **[D]** RBAC: Read-Only, Network Admin, Security Admin, Customisation; m-of-n for bypass and un-tamper; USB-token two-factor; SSH, SNMPv3 authPriv, TLS 1.3, rsyslog, authenticated NTP; NMS (online and offline) / CNM (WP p10, R, X).
- **[I]** A learned component would most plausibly be delivered as a **Network Management Suite / CNM policy feature**, pushing plane-mapping and timer configuration. That avoids touching the certified crypto boundary, which matters for how realistic the proposal looks to KATIM.

---

## 6. Gaps under mobility (where a contribution fits)

Ranked by (relevance to the seamless-connectivity challenge) × (how much an ML/security student can credibly add) × (testable in emulation).

### G1. Bearer-blind QoS mapping  ★ primary candidate
- **[D]** Mapping is static or "automatic"; **[I]** nothing reacts to the black-side bearer changing from 5G (≈50 ms, tens of Mbps) to Wi-Fi to GEO satcom (≈600 ms RTT, ≈1–2 Mbps).
- **[I]** At a transition, all 8 planes keep their configured shares and drop priorities. Stale location updates and bulk video keep competing with hazard alerts for a link that just shrank 20×. Planes isolate *congestion in the transit network*, not *capacity collapse of the bearer under them*.
- **Contribution:** a learned, **bearer-aware and freshness-aware plane-mapping and discard policy**. It predicts the transition from mobility and link telemetry, then pre-emptively re-maps, rate-limits and discards superseded data (minimising Age of Information). It is delivered as an NMS/CNM policy, so the crypto boundary is unchanged.

### G2. Session recovery after a bearer switch
- **[D]** Handshake retry is 10 s. **[I]** If a transition kills the session (underlay address change, NAT rebinding, or a keepalive timeout during a satcom outage), worst-case recovery is around 10 s plus discovery plus a PQ handshake over high RTT. PQ public keys are KB-scale, so they fragment on constrained links.
- **[I]** Unknown: whether KGTP supports WireGuard-style endpoint roaming (an authenticated packet from a new address updates the peer endpoint without a re-handshake). If it does, G2 shrinks. If not, it is a major gap. **Flag this as an open question; do not claim it.**
- **Contribution:** adaptive keepalive and retry timers driven by a handover predictor (pre-emptive re-handshake *before* the predicted outage, back-off tuned to the bearer), versus fixed timers.

### G3. Ratchet resynchronisation under burst loss
- **[D]** Packet-count ratchet; data-plane keepalives keep keys in sync. **[I]** Burst loss at handover could push peers into different ratchet epochs until a keepalive resyncs, so packets are dropped as undecryptable. Severity depends on the unpublished design (sequence number in header vs implicit counter).
- **Contribution:** quantify it in emulation; a smaller-scope option.

### G4. Per-plane traffic-analysis leakage  ★ security + ML angle
- **[D]** Each plane is a separate tunnel with its own DSCP marking visible on the black network. **[I]** A passive observer on the bearer (exactly the contested-environment adversary) sees *per-class* volumes and timing: when the voice plane wakes up, when the alert plane bursts, when video starts. This is an inherent QoS-vs-confidentiality trade-off that KATIM's identity hiding does not address.
- **Contribution:** (a) an ML traffic-analysis classifier that infers mission activity from per-plane metadata in emulation (attack); (b) a learned shaping/padding policy that keeps QoS while bounding leakage (defence). It's a strong fit for an ML + security student. It is also sensitive in tone, so frame it as "hardening".

### G5. Rate-coupled key lifetime (minor)
- **[I]** On slow bearers, the 131 072-packet ratchet stretches epochs to minutes. The 1-minute re-handshake caps exposure, so this is likely by design. Mention it only as an observation.

---

## 7. Open questions to verify (before any claim goes in the submission)

1. Does KGTP roam endpoints without a re-handshake?
2. Default keepalive intervals and the dead-peer threshold.
3. What "automatic mapping" and "application identification" actually classify on.
4. Is the per-plane DSCP on the black side configurable to a single uniform value (which would mitigate G4)?
5. Does the 9001-R sit behind a separate multi-bearer router in typical vehicle integrations? (Nokia 5G-in-a-box, KATIM tactical satellite modem.)

## 8. Baselines: what we must beat

**No public KATIM performance numbers exist** for handover, freshness, alert delivery or leakage. The bar is built from three sources:

1. **Documented-behaviour emulation (main baseline).** 8 planes, static mapping, 10 s retry, 60 s re-handshake, 131 072-packet ratchet. Its simulated metrics are the bar. Claim wording: "beats a faithful emulation of the documented design", never "beats KATIM".
2. **Standards thresholds (pass/fail):** voice one-way ≤150 ms recommended / ≤400 ms limit (ITU-T G.114); MCPTT mouth-to-ear ≤300 ms, end-to-end access ≤1000 ms (3GPP TS 22.179); GEO RTT ≥~480 ms by physics. Location freshness has no standard: we set it (e.g. <5 s at command) and justify it.
3. **Timer-derived bounds [I]:** worst-case session recovery ≈ 10 s retry + 2–3 RTT handshakes (≈1.2–1.8 s on GEO); PQ "triple KEM" key material is likely KB-scale (KEM unnamed, so this is an assumption); ratchet epoch is minutes on slow satcom; backlog staleness ≈ backlog ÷ new link rate after a capacity drop.

G4 bar: our attacker's accuracy against the documented 8-plane design vs a single-plane design. Literature on encrypted-tunnel classification commonly reports >90%. The defender target is near-chance accuracy at an acceptable QoS and overhead point (a curve, not a number).

## 9. Emulation baseline (documented behaviour only)

- Gateway stand-in: Linux node; one Noise-family tunnel per plane (WireGuard, optionally with a PQ PSK à la Rosenpass), 8 planes; `tc` priority classes + DSCP marking; re-handshake 60 s; retry 10 s.
- Bearers: Mininet-WiFi or ns-3 with 5G / Wi-Fi / GEO-satcom links (netem delay, loss and rate profiles) and an external bearer-selector router.
- Baseline = static plane mapping (the documented behaviour). Candidate = learned policy (G1, optionally G2).
- Metrics: Age of Information of responder locations, hazard-alert deadline-hit rate, voice MOS, session-recovery time, and (for G4) classifier accuracy against the leakage bound.
