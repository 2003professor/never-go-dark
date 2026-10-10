# Baseline bar — TRAIN split (frozen evaluator, SPEC §13)

All numbers are mean over the frozen train seed set [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19] x the train scenario instances. Transitions are **event-anchored and randomised** per (instance, seed): a real degradation event from the A-leg trace (SPEC §6.3) falls at a random position (25-75%) of a randomised-length episode; there is no forced cut at a scenario time (SPEC §13.1). **Outage** (SPEC §8.4) = seconds no plane can deliver: tunnel dark (DPD/retry/per-tick handshake, SPEC §13.5) **or** tunnel up over deliverable goodput below 2.4 kbps (*stall*). Gates (SPEC §8.2/§8.3) are computed over **FEASIBLE** messages (a path could have delivered them; SPEC §13.3) with the absolute rate shown alongside. Terrestrial 5G capacity = saturating UCC **Download** captures only (SPEC §12.1). GEO is a measurement-parameterised **model** (availability 94%; SPEC §12.4). Score 0 = documented-baseline parity (SPEC §13.3).

## Oracle lexicographic property (SPEC §13.4/§13.8)

PASS: on all **640** train runs the oracle's outage is <= the outage of every same-mechanism policy (documented, ATSSS, always-warm AND the diagnostic fixed-timer gamer), as the lexicographic upper-bound construction requires. The oracle enumerates and engine-evaluates a superset of their schedules (a cut at every outage-relevant boundary + a whole-episode warm), so it cannot be beaten on outage.

*MOBIKE-style roaming (RFC 4555) uses a different, structurally faster RECOVERY mechanism (~1 RTT migration vs a full re-handshake), not the make-before-break family the oracle bounds. On single-leg scenarios (S1), where make-before-break offers nothing, MOBIKE's faster reactive recovery can beat the reactive-physics oracle — reported here as a finding, not a ceiling violation: a predictive policy should COMBINE prediction with migration.* MOBIKE beats the oracle on 48/640 runs (all single-leg/reactive cases).

## Overall (all train scenarios)

| Mechanism | AoI p95 (s) | Outage/tr (s) | dark (s) | stall (s) | Emission-tx (s) | Standby-tx (s) | Satcom ovh (B) | Alert-hit (feas/abs) | Voice<=400ms (feas/abs) | Video gp | Gates feas | Gates abs | Score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 5.9 ± 11.1 | 5.15 | 5.13 | 0.02 | 8.7 | 0.0 | 892 | 0.916/0.912 | 0.899/0.892 | 0.745 | FAIL | FAIL | -2.043 |
| MOBIKE-style (RFC 4555) | 4.0 ± 10.2 | 3.75 | 3.74 | 0.01 | 35.1 | 0.0 | 5367 | 0.960/0.956 | 0.945/0.937 | 0.768 | FAIL | FAIL | -0.504 |
| ATSSS-style (TS 23.501) | 1.7 ± 3.1 | 0.68 | 0.65 | 0.04 | 102.0 | 97.6 | 12091 | 0.990/0.986 | 0.977/0.970 | 0.709 | FAIL | FAIL | 0.652 |
| Always-warm | 1.7 ± 3.1 | 0.69 | 0.65 | 0.04 | 102.0 | 97.6 | 12014 | 0.990/0.986 | 0.978/0.970 | 0.739 | FAIL | FAIL | 0.671 |
| Oracle (perfect foresight) | 1.6 ± 3.0 | 0.38 | 0.37 | 0.01 | 34.3 | 6.2 | 11732 | 0.993/0.990 | 0.989/0.981 | 0.788 | FAIL | FAIL | 1.149 |
| Diag: fixed-timer gamer | 2.6 ± 4.1 | 1.68 | 1.66 | 0.02 | 62.0 | 8.0 | 9785 | 0.972/0.969 | 0.952/0.945 | 0.703 | FAIL | FAIL | 0.117 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

*The `diag_fixed_timer` row is a DIAGNOSTIC gaming policy (SPEC §13.1/§13.8), not a mechanism: it bets on a single constant transition time. It is scored here against ALL mechanisms, not just the oracle. With the emission loophole closed (active satcom/secondary airtime is charged) and 20 seeds, its score (0.117) does NOT beat ATSSS (0.652) or always-warm (0.671), and it stays far above the foresight ceiling on outage — a constant timing cannot line up with the randomised, event-anchored, secret-offset degradation. (Earlier wording that compared the gamer only to the oracle and called the benchmark simply 'ungameable' was removed as misleading.)*

## Oracle emissions frontier (SPEC §13.4/§13.8)

Minimum achievable outage per EMISSION-transmit budget (fraction of episode length; emission = airtime on the non-primary/satcom leg). Each point is the min-outage oracle plan whose REALISED emission-tx is within the budget, evaluated through the real engine with the headline planner (monotone by construction; no smoothing). ATSSS / always-warm sit at the ~100 %% (always-on) end; the bottom-left is the target a predictive policy must reach:

| Emission budget | Realised emission-tx (s) | Outage/tr (s) |
|---|---|---|
| 0% | 3.6 | 4.88 |
| 5% | 4.1 | 4.72 |
| 10% | 5.1 | 4.51 |
| 25% | 9.0 | 3.71 |
| 50% | 11.8 | 2.19 |
| 100% | 34.3 | 0.38 |

## Outage per transition (s) by scenario

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 2.47 | 3.51 | 4.12 | 2.85 | 23.17 | 4.07 | 0.64 | 0.36 |
| MOBIKE-style (RFC 4555) | 2.01 | 0.87 | 0.88 | 1.28 | 23.17 | 0.86 | 0.60 | 0.36 |
| ATSSS-style (TS 23.501) | 2.47 | 0.32 | 0.12 | 0.95 | 0.18 | 0.29 | 0.92 | 0.22 |
| Always-warm | 2.47 | 0.33 | 0.12 | 0.95 | 0.18 | 0.29 | 0.93 | 0.22 |
| Oracle (perfect foresight) | 2.47 | 0.03 | 0.05 | 0.26 | 0.04 | 0.20 | 0.00 | 0.02 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

##   of which stall (tunnel up, ~0 capacity) per transition (s)

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 0.01 | 0.01 | 0.01 | 0.07 | 0.01 | 0.01 | 0.00 | 0.00 |
| MOBIKE-style (RFC 4555) | 0.01 | 0.01 | 0.01 | 0.07 | 0.01 | 0.01 | 0.00 | 0.00 |
| ATSSS-style (TS 23.501) | 0.01 | 0.02 | 0.03 | 0.09 | 0.08 | 0.02 | 0.03 | 0.02 |
| Always-warm | 0.01 | 0.02 | 0.03 | 0.09 | 0.08 | 0.02 | 0.04 | 0.02 |
| Oracle (perfect foresight) | 0.01 | 0.00 | 0.00 | 0.05 | 0.04 | 0.00 | 0.00 | 0.00 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

## Location AoI p95 (s) by scenario

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 4.7 | 5.5 | 5.8 | 2.7 | 21.4 | 5.4 | 1.0 | 1.0 |
| MOBIKE-style (RFC 4555) | 3.4 | 1.2 | 1.3 | 1.2 | 21.4 | 1.4 | 1.0 | 1.0 |
| ATSSS-style (TS 23.501) | 4.7 | 1.1 | 1.2 | 1.4 | 1.0 | 1.6 | 1.5 | 1.0 |
| Always-warm | 4.7 | 1.1 | 1.2 | 1.4 | 1.0 | 1.6 | 1.5 | 1.0 |
| Oracle (perfect foresight) | 4.7 | 1.2 | 1.1 | 1.1 | 1.0 | 1.5 | 1.0 | 1.0 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

## Alert deadline-hit rate (FEASIBLE) by scenario

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 0.951 | 0.890 | 0.917 | 0.971 | 0.720 | 0.877 | 1.000 | 1.000 |
| MOBIKE-style (RFC 4555) | 0.978 | 0.996 | 0.998 | 0.994 | 0.720 | 0.995 | 1.000 | 1.000 |
| ATSSS-style (TS 23.501) | 0.951 | 0.992 | 1.000 | 0.992 | 1.000 | 0.998 | 0.992 | 0.992 |
| Always-warm | 0.951 | 0.992 | 1.000 | 0.992 | 1.000 | 0.998 | 0.996 | 0.992 |
| Oracle (perfect foresight) | 0.951 | 0.997 | 1.000 | 1.000 | 1.000 | 0.998 | 1.000 | 1.000 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

## Alert deadline-hit rate (ABSOLUTE) by scenario

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 0.921 | 0.890 | 0.917 | 0.971 | 0.720 | 0.877 | 1.000 | 1.000 |
| MOBIKE-style (RFC 4555) | 0.946 | 0.996 | 0.998 | 0.994 | 0.720 | 0.995 | 1.000 | 1.000 |
| ATSSS-style (TS 23.501) | 0.921 | 0.992 | 1.000 | 0.992 | 1.000 | 0.998 | 0.992 | 0.992 |
| Always-warm | 0.921 | 0.992 | 1.000 | 0.992 | 1.000 | 0.998 | 0.996 | 0.992 |
| Oracle (perfect foresight) | 0.921 | 0.997 | 1.000 | 1.000 | 1.000 | 0.998 | 1.000 | 1.000 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

## Voice frames within 400 ms (FEASIBLE) by scenario

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 0.947 | 0.871 | 0.851 | 0.944 | 0.745 | 0.851 | 0.990 | 0.993 |
| MOBIKE-style (RFC 4555) | 0.966 | 0.973 | 0.962 | 0.974 | 0.745 | 0.952 | 0.991 | 0.993 |
| ATSSS-style (TS 23.501) | 0.947 | 0.988 | 0.976 | 0.979 | 0.998 | 0.967 | 0.968 | 0.994 |
| Always-warm | 0.947 | 0.988 | 0.976 | 0.979 | 0.998 | 0.967 | 0.972 | 0.994 |
| Oracle (perfect foresight) | 0.947 | 0.993 | 0.994 | 0.997 | 0.998 | 0.982 | 1.000 | 1.000 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

## Gate-pass fraction (FEASIBLE: alert-hit>=0.99 AND voice MCPTT/G.114) by scenario

| Mechanism | S1_lte5g_walk | S2_5g_leo_drive | S3_5g_geo_drive | S4_wifi_edge | S5_mptcp_hsr | S6_collective_5g_geo | S7_lumos_walk | S8_lumos_drive |
|---|---|---|---|---|---|---|---|---|
| Documented KATIM (reactive) | 0.30 | 0.04 | 0.07 | 0.23 | 0.24 | 0.01 | 0.51 | 0.72 |
| MOBIKE-style (RFC 4555) | 0.41 | 0.31 | 0.09 | 0.36 | 0.24 | 0.01 | 0.51 | 0.72 |
| ATSSS-style (TS 23.501) | 0.30 | 0.79 | 0.06 | 0.62 | 0.88 | 0.03 | 0.66 | 0.88 |
| Always-warm | 0.30 | 0.79 | 0.06 | 0.62 | 0.88 | 0.03 | 0.72 | 0.88 |
| Oracle (perfect foresight) | 0.30 | 0.88 | 0.59 | 0.88 | 0.88 | 0.49 | 0.99 | 1.00 |

*Scenario construction (SPEC §3.6/§3.7/§13.2/§13.6):* **S1** single real replay (UCC 5G Download capture), anchored on a real persistent degradation event; **S2** STITCHED: UCC 5G replay + Starlink LEO (measured seconds CONCATENATED, unmeasured gaps removed), measured independently and only time-aligned; **S3** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay); **S4** STITCHED: Meireles Wi-Fi replay + UCC 4G LTE car replay, measured independently and only time-aligned; **S5** real SIMULTANEOUS capture (HSR-MPTCP, two carrier subflows), no stitching; **S6** UCC 5G replay + GEO measurement-parameterised MODEL (not a replay), 15-user collective load; **S7** STITCHED: Lumos5G walking mmWave 5G replay (gateway capped to X9000R <=100 Mbps) + UCC 4G LTE PEDESTRIAN standby, independently measured, only time-aligned; **S8** STITCHED: Lumos5G driving mmWave 5G replay (gateway capped to 9001-R <=1 Gbps) + Starlink LEO standby (measured seconds CONCATENATED). GEO = model; stitched legs were never captured together; the Starlink LEO leg is measured seconds CONCATENATED (unmeasured gaps removed), never filled.

## Which gate fails where (computed from the oracle, not asserted)

- **S1_lte5g_walk** — oracle feasible gate-pass 0.30; failing: feasible alert-hit 0.951 < 0.99, feasible voice<=400ms 0.947 < 0.99.
- **S2_5g_leo_drive** — oracle feasible gate-pass 0.88; gates met on average.
- **S3_5g_geo_drive** — oracle feasible gate-pass 0.59; gates met on average.
- **S4_wifi_edge** — oracle feasible gate-pass 0.88; gates met on average.
- **S5_mptcp_hsr** — oracle feasible gate-pass 0.88; gates met on average.
- **S6_collective_5g_geo** — oracle feasible gate-pass 0.49; failing: feasible voice<=400ms 0.982 < 0.99.
- **S7_lumos_walk** — oracle feasible gate-pass 0.99; gates met on average.
- **S8_lumos_drive** — oracle feasible gate-pass 1.00; gates met on average.

The oracle is the perfect-foresight ceiling (SPEC §9.3): a FEASIBLE gate it cannot meet is a property of the physics, not of any policy. Over GEO the ~300 ms one-way propagation sits at the MCPTT limit, so the voice gate is physically hard there.

*Figures: results/figures/. The Starlink LEO leg is measured seconds concatenated (unmeasured gaps removed, SPEC §13.2); the S2 phantom ~65 s outages are gone, real in-burst stalls remain.*
