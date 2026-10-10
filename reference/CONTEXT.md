# Project Context: Seamless Connectivity for KATIM Gateway

Working brief for our competition entry. Read this first.
Companion files: `ARCHITECTURE-MAP.md` (the evidence: every KATIM claim tagged [D]ocumented or [I]nferred, with sources) and `docs/` (the public PDFs and their extracted text).

---

## 1. The competition

> One of the greatest challenges in modern communication is maintaining a seamless connection as users move between different network technologies and coverage areas. Roaming between Wi-Fi and mobile networks, or transitioning between different Wi-Fi Access Points, interruptions can degrade the user experience and impact users' critical applications.
>
> The challenge statement is to define a set of network topologies (for example, Wi-Fi, satellite, 5G, wired networks), and design, implement and/or simulate a set of tools and mechanisms for users to maintain a seamless connection. The users run different applications while moving with different speeds and in different directions, traversing different network technologies. The goal is to maintain the quality of service or the quality of experience for the users of the networks.
>
> This challenge is deliberately open. You choose the scenario, the network technologies and the data you work with. How you frame and justify that choice is itself part of what is being assessed.

**What this means for us:** finding the right problem *is* the main difficulty. Generic seamless connectivity is largely solved commercially: Wi-Fi/cellular/satellite bonding, automatic failover, packet duplication and app prioritisation (e.g. Peplink SpeedFusion). We need a problem that is real, unsolved, specific, and defensible in front of judges. The framing will be graded.

## 2. How we got here

1. **Sector:** defence, military and emergency response, where losing connectivity at the wrong moment costs lives, not just user experience. NIST PSCR (June 2026) still lists reliability, performance and interoperability gaps for mission-critical services. A 2024 audit found public-safety broadband unavailable at times during the Maui fires.
2. **Company:** EDGE Group (UAE), likely judges or stakeholders. Its secure-communications entity is **KATIM**.
3. **Product:** the **KATIM Gateway 9001-R**, a rugged network encryptor built for "in-motion deployments". Proposing an improvement to *their own product* is more compelling to EDGE than a generic tool.
4. **Rule:** we work only from **public documentation**. No probing of devices, no firmware. Every claim about their internals is tagged documented or inferred.
5. **Method:** an autoresearch-style optimisation loop (Karpathy, March 2026): an LLM agent edits a policy file, a frozen evaluator scores it, and the change is kept or rolled back. This is the same family as DeepMind's AlphaEvolve, which found a 48-multiplication 4×4 complex matmul.
6. **Framing borrowed from System 1 / System 2** (Kahneman; recently TypeSafe AI's "Jev"):
   - **System 2 (offline, slow):** the autoresearch loop *discovers* a policy.
   - **System 1 (deployed, fast):** a small, auditable policy runs on the gateway.
   - Selling point: a certified defence encryptor can ship and certify **readable code**, not a black-box neural network.

## 3. KATIM in one page

- **Who:** KATIM is part of EDGE Group's Space & Cyber Technologies cluster (formerly Electronic Warfare & Cyber). HQ in Abu Dhabi, office in Finland. Lines: network encryptors, ultra-secure phones, secure apps, satellite communications. In 2025 it signed an MoU with Nokia for Military 5G and "5G in-a-box". In 2026 it partnered with Kerno to manufacture the gateways locally.
- **Gateway family:** 9011 (rack, up to 10 Gbps), 9001 (small office), **9001-R** (rugged, in-motion, ≤1 Gbps), X9000R (540 g, edge, ≤100 Mbps, <5 ms latency).
- **What the 9001-R does:**
  - L2 or L3 encrypted overlay: point-to-point, hub-and-spoke or mesh, with policy routing and OSPFv2.
  - Post-quantum + classical cryptography.
  - Red / crypto / black hardware separation, tamper response.
  - **One plaintext port and one encrypted (cipher) port.**
- **Its custom tunnelling protocol** (documented in the 12-page white paper):
  - A discovery handshake that hides device identities, then a session handshake ("quad Diffie-Hellman + triple KEM").
  - **Handshake retry every 10 s.**
  - **Full re-handshake about every 60 s.**
  - **Key ratchet every 131 072 packets.**
  - Separate keepalives for the control plane and the data plane.
  - [I] Looks like the Noise / WireGuard protocol family.
- **QoS:**
  - **Up to 8 encryption planes** per peer association, each with its own keys and its own DSCP marking on the black (encrypted) side.
  - Traffic is mapped to planes by a "user-configurable or automatic" rule.
  - Internal priority and discard-on-congestion classes.
- **The key structural fact [I]:** with a single cipher port, the gateway cannot see the radio bearers. Switching between Wi-Fi, 5G and satellite happens in an external router. The encryptor only experiences it as loss, delay, address changes and dead-peer timeouts. Its QoS and session logic **do not react to the bearer changing**.
- **No public performance numbers** for handover, freshness or alert delivery.

## 4. Common baseline and evaluation (shared by P1–P3)

- **Baseline:** a faithful emulation of the *documented* behaviour (static plane mapping, 10 s retry, 60 s re-handshake, 131 072-packet ratchet), run through the same scenarios.
  - Claim wording: "beats a faithful emulation of the documented design", never "beats KATIM".
- **Absolute thresholds:**
  - Voice one-way delay ≤150 ms recommended, ≤400 ms limit (ITU-T G.114).
  - Mission-critical push-to-talk mouth-to-ear ≤300 ms, access ≤1000 ms (3GPP TS 22.179).
  - Location freshness: we set the threshold (e.g. <5 s at command) and justify it.
- **Physics constraint:** GEO satellite round trip ≥~480 ms (≈550–600 ms in practice). Voice over GEO starts with almost no slack.
- **Scenario:** about 15 responders and a command vehicle at an industrial incident.
  - Bearers: 5G (or Nokia 5G-in-a-box), vehicle Wi-Fi, GEO / LEO satcom backhaul.
  - Traffic: hazard alerts, position reports, push-to-talk voice, bodycam / drone video, bulk data.
  - Movement at walking and vehicle speeds, including **collective transitions**: many users switching bearers at once.
- **Data rule:** use **real measured link traces**, not invented ones. Candidates to vet: UCC 5G dataset, Lumos5G, published Starlink / GEO measurements. Use real codec and position-reporting rates. Anything synthetic (e.g. mission labels) must be stated as an assumption.
- **Autoresearch hygiene (all three):**
  - One editable policy file.
  - Evaluator and simulator frozen.
  - The policy may read **only what a real gateway could observe**.
  - Final numbers come from **held-out** scenarios the loop never saw.
  - Baselines to beat: the documented emulation, a hand-written heuristic, and (where it fits) an RL policy.

---

## 5. P1: Freshness-aware scheduling across bearer transitions

### Problem statement
**When a moving team's encrypted link drops from 5G to satellite, KATIM's static mapping of traffic to QoS planes keeps sending queued, already-outdated data. Command's picture goes stale and hazard alerts get delayed, even though the connection never "fails". Can a learned, freshness-aware policy for mapping and discarding traffic keep responder locations current and alerts on time through bearer transitions, beyond what well-configured encrypted QoS achieves?**

### Why it matters
"Connected" is not the same as "useful". After a 5G→GEO switch, capacity can fall about 20× and round-trip time can rise about 10×. Queued position reports, now superseded, compete with fresh alerts and voice. Command acts on positions that are tens of seconds old.

### What KATIM does today
- [D] Up to 8 planes with per-plane keys, plus internal discard priority on congestion.
- [D] Static or "automatic" mapping of traffic to planes.
- [I] No reaction to the bearer changing.
- [I] No notion of *superseded* data: a stale position is as deliverable as a fresh one.
- [D] Planes isolate congestion *in the transit network*, not a collapse in capacity of the bearer underneath.

### The gap
Prioritisation answers *what goes first*. It doesn't answer *what is still worth sending* or *how to act before the link shrinks*.

### What is learned
- **Inner model:** a capacity/handover predictor from signals the gateway can see (keepalive round-trip time and loss trends, throughput estimate). It answers "is the link about to collapse, and to roughly what rate?"
- **Policy (found by autoresearch):** `decide(queues, message metadata, link telemetry) → plane mapping, discards of superseded data, per-plane rate caps, pre-emptive flushes`.
- What it implicitly learns: early signs of a transition; how fast each data type loses value as it ages; how to reallocate a shrunken link.

### Autoresearch setup
- **Editable:** `policy.py`.
- **Frozen:** simulator (8-plane encryptor, documented timers, real bearer traces, mobility), scenarios, `eval.py`.
- **Score:**
  - Hard constraints: hazard-alert deadline-hit rate ≥ threshold; voice within G.114 / MCPTT limits.
  - Then minimise p95 location Age of Information.
  - Then maximise video goodput.

### Metrics
Location AoI (mean and p95), alert deadline-hit rate and latency, voice delay and MOS, video goodput, wasted bytes (stale data delivered).

### Deployment story
The policy ships as a configuration feature of KATIM's management software (Network Management Suite / Crypto Network Manager). The certified crypto boundary is untouched.

### Suggestion (optional): Jev as an offline teacher for message semantics
TypeSafe AI's Jev (Sept 2026) is a "System One" model: it takes text or JSON plus typed questions and returns calibrated, typed answers in 70–500 ms. Deployment is a cloud console in early access; on-prem is not stated. Inputs are text and structured state only.
- **Not in the live path:** sending plaintext to a third-party cloud would defeat the encryptor.
- **Offline teacher:** Jev labels a realistic responder-message corpus with typed questions, e.g. `urgency`, `supersedes_previous`, `value_half_life`, `data_type`. Its calibrated probabilities flag low-confidence labels for human review.
- **Distil:** a tiny on-device classifier trained on those labels feeds P1's policy in microseconds, auditable and with no cloud.
- **Why it matters:** static traffic mapping cannot handle unstructured messages ("gas leak near tank B"). This makes the System 1 / System 2 framing literal: a frontier fast-judgment model teaches, a certifiable small model deploys.
- **Caveats:** use real public-safety message formats and incident logs, not invented text; spot-check labels by hand; claim a direct mode only if TypeSafe confirms on-prem deployment.
- Not suited to P2 or P3 (their inputs are numeric telemetry, where a small classical model is better), nor inside the frozen evaluator (its answers can vary, which invites gaming).

### Risks and open questions
- What "automatic mapping" and "application identification" actually classify on (unpublished).
- Results depend on how realistic the traffic model is.
- Superseded-data detection needs message metadata on the plaintext side. Assume a standard position format such as Cursor-on-Target, and state it.

---

## 6. P2: Make-before-break for encrypted tunnels

### Problem statement
**When the underlying bearer changes, KATIM's post-quantum tunnel may have to be re-established: dead-peer detection, a 10 s retry interval, then discovery and session handshakes that cost several round trips over a ~600 ms satellite link. Every plane is dark during that time. Can a learned policy predict transitions and establish the next tunnel *before* the current one breaks (make-before-break), cutting outage time to near zero while keeping handshake overhead on constrained links within budget and leaving the security protocol unchanged?**

### Why it matters
This is "seamless" in the literal sense. A dark gap of several seconds drops push-to-talk calls and loses alerts, whatever the QoS. Post-quantum handshakes make it worse: KB-scale key material over a slow, high-latency link.

### What KATIM does today
- [D] Handshake retry at 10 s intervals.
- [D] Re-handshake about every 60 s.
- [D] Keepalives for fast dead-peer detection, values unpublished.
- [D] Mesh and hub-and-spoke supported.
- [I] Recovery is *reactive*: detect death, then rebuild.
- [I] Worst case ≈ detection time + up to 10 s + 2–3 handshake round trips (≈1.2–1.8 s on GEO).
- **Open question:** does their protocol roam endpoints without a re-handshake, as WireGuard does? If yes, P2 narrows to cases where the session itself dies (long outages, NAT and address changes during a satcom gap). If no, P2 is a major gap. **We must state this assumption explicitly.**

### The gap
Cellular networks have done make-before-break at the radio layer for decades. As far as we can find, **no encrypted overlay does it for the crypto session itself**. Doing it naïvely is expensive: a standby tunnel means extra handshakes and keepalives on links that are metered or slow.

### What is learned
- **Inner model:** a transition predictor giving P(switch within T seconds) and the likely target bearer.
- **Policy (found by autoresearch):**
  - when to warm up a standby association on the target bearer
  - which bearer
  - when to cut over
  - when to tear the standby down
  - how to adapt keepalive and retry timers to the current bearer
- **The tradeoff it learns:** outage avoided vs handshake and keepalive bytes spent.

### Security constraint (non-negotiable)
The policy controls only **timing and triggers** of standard handshakes. It never changes the protocol, key derivation or crypto. Standby associations get fresh, independent keys and never reuse them. Score anything else as invalid.

### Autoresearch setup
- **Editable:** `continuity_policy.py`.
- **Frozen:** simulator with the documented timer model and a parameterised handshake cost (round trips × bearer RTT + key-material bytes ÷ rate), traces and scenarios.
- **Score:** minimise total outage seconds and packets lost during transitions, subject to an overhead budget (bytes per hour on the satellite bearer).
- **Upper bound:** a perfect-foresight oracle.

### Metrics
Outage duration per transition (mean, p95, max), packets lost, handshake and keepalive overhead in bytes, number of dropped push-to-talk calls.

### Risks and open questions
- The roaming question above.
- Needs at least two bearers available during the overlap. True for vehicle Wi-Fi + 5G + satcom; state it.
- The handshake cost model is inferred (the KEM is unnamed). Run a sensitivity sweep across plausible key sizes.

---

## 7. P3: Assigning each encryption plane to a bearer

### Problem statement
**A command vehicle often has several bearers available at once (5G, Wi-Fi mesh, satcom), but KATIM's 8 encryption planes all ride whichever single bearer the external router picks. Can a learned policy assign each plane to the right bearer, duplicating critical planes (alerts, voice) across bearers and keeping bulk data off scarce satellite capacity, without breaking per-plane key isolation and anti-replay?**

### Why it matters
Using every available bearer, per traffic class, means an alert survives the collapse of any single link, and video doesn't starve voice on satcom.

### What KATIM does today
- [D] One active cipher port.
- [D] Per-plane keys and per-plane DSCP marking on the black side.
- [D] Policy-based routing and OSPFv2.
- [I] **Key insight:** because each plane already carries its own DSCP on the encrypted side, an external policy router could steer planes onto bearers **without touching the encryptor**. KATIM's existing design already exposes the hook.

### The gap and novelty
Multi-bearer bonding exists commercially (Peplink etc.). Our twist is that it is **crypto-aware and per encryption plane**:
- Duplicating packets across bearers with different delays causes reordering and duplicates. These interact with **anti-replay windows** and the **packet-count key ratchet** (resynchronisation risk).
- The policy must choose duplication and steering that the crypto layer tolerates.

Be upfront with judges that bonding is not new. The crypto-aware, per-plane aspect is the contribution.

### What is learned
- **Inner model:** per-bearer short-term quality prediction (rate, round-trip time, loss).
- **Policy (found by autoresearch):** for each plane at each moment, primary bearer, whether to duplicate and onto which bearer, and rate caps. Constrained by satcom cost and by anti-replay and ratchet tolerance limits (taken from the simulator's crypto model).

### Autoresearch setup
- **Editable:** `steering_policy.py`.
- **Frozen:** simulator with multiple simultaneous bearers (real traces), per-plane anti-replay window and ratchet model, scenarios.
- **Score:**
  - Hard constraint: alert delivery ≥ threshold through any single-bearer failure.
  - Voice within limits.
  - Then minimise satcom bytes and packets rejected by anti-replay or ratchet.

### Metrics
Per-plane delivery ratio and latency, alert survival under single-bearer failure, voice MOS, satcom bytes per hour, packets dropped by anti-replay or ratchet, duplication overhead.

### Risks and open questions
- Weakest novelty of the three.
- Anti-replay window size and ratchet resynchronisation behaviour are unpublished, so model them as parameters and sweep.
- Implies a deployment with a multi-WAN router behind the 9001-R. State this as the assumed integration.

---

## 8. How the three relate

| | P1 Freshness scheduling | P2 Make-before-break | P3 Per-plane bearers |
|---|---|---|---|
| Layer | what flows *inside* the tunnel | the tunnel *session* itself | *which link* each plane uses |
| Failure addressed | connected but stale | dark gap during transition | single-bearer dependence |
| Fit to challenge | ★★★ QoS/QoE | ★★★ literal "seamless" | ★★ |
| Novelty | ★★ | ★★★ | ★ (crypto-aware angle ★★) |
| Biggest unknown | the "automatic mapping" classifier | whether their protocol roams endpoints | anti-replay / ratchet behaviour |
| Shared infrastructure | same simulator, traces, scenarios, evaluator harness | | |

They share one simulator, so choosing one doesn't waste work on the others. **P1 + P2 combine into a single story: "nothing stalls during a transition."** P2 keeps the session alive; P1 makes sure what flows through it is still useful.

## 9. Next steps

1. Choose P1, P2, P3 or P1+P2.
2. Vet real link traces (5G, Wi-Fi, LEO / GEO satellite).
3. Build the frozen simulator: documented-behaviour baseline first, which produces the bar.
4. Autoresearch loop on the chosen policy file; held-out evaluation; write-up with every KATIM claim tagged [D] or [I].
