# CLAIMS.md — every number in `never-go-dark.pdf`, mapped to its source

Audit map for the 10-page PDF (revision 2, after the coordinator's fix list).
Each row: the figure as printed → the canonical file and field it was read
from. The build pipeline is `make-data.mjs` → `data.js` → `charts.js` +
`pages.html`; no chart value is typed by hand.

**Canonical sources (all under `/Users/professor/Desktop/katim-research/`):**
- `H`  = `katim-sim/results/final_heldout_s300-319.json` — one-shot held-out, seeds 300–319
- `NF` = `studio/data/heldout/noise_floor.json` — paired bootstrap 95% CIs (B = 10,000, seed 12345)
- `P`  = `katim-sim/predictor/report.json` → **`no_modem`** block (the shipped predictor)
- `PL` = `katim-sim/results/planes_experiment.json`; `PLv1` = `planes_experiment_v1_thrashing.json`
- `proto` = `prototype/logs/result_{reactive,mobike,atsss,ngd}_final.json` — one measured WireGuard run
- `HO` = `studio/data/heldout/heldout.json` → `meta.*`, and `timelines.instances["S4:t304_ac"].mech.never_go_dark` (seed 300)
- `s04` / `s01` = `studio/video/film/data.js` → `NGD_DATA.scene04` / `scene01` (extracted by `build-data.mjs` from `HO` and the UCC trace)
- `POL` = `katim-sim/policies/policy.py`; `EVAL` = `katim-sim/eval/evaluate.py`; `GW` = `katim-sim/sim/gateway.py`
- `SPEC` = `katim-sim/SPEC.md`; `FIND` = `FINDINGS.md`

Rounding: outage 2 dp, airtime 1 dp, scores and rates 3 dp. "Standby airtime" everywhere = `emission_tx_s`
(the figure the locked facts call standby airtime: 56.7 / 43.3 / 98.6 / 98.6 / 6.7 / 30.0 s).

---

## Page 1 — Problem (cover)
| Printed | Value | Source |
|---|---|---|
| Bearer A capacity line, 0–90 s, √ scale | 91 points, max 84.4 Mbps | `s01.link.uplink_mbit` (UCC `Download__Driving__B_2020.02.27`) |
| COLLAPSE · t+44 s | 44 (first dark window starts 43.46 s) | `s01.collapse.t`; `proto reactive .dark_windows_s[0][0]` |
| recover · t+62 s | 62 | `s01.recover.t` |
| Documented reactive 3.90 s · 3 gaps | 3.9, 3 | `proto reactive .dark_s / .dark_gaps` |
| Reactive hatched windows | [43.46,45.92] [47.98,48.28] [49.94,51.08] | `proto reactive .dark_windows_s` |
| MOBIKE 0.76 s · 1 gap | 0.76 | `proto mobike .dark_s` |
| ATSSS 1.04 s · 1 gap | 1.04 | `proto atsss .dark_s` |
| Never Go Dark 0.44 s · 1 gap | 0.44, window [44.12,44.56] | `proto ngd .dark_s / .dark_windows_s` |
| "a 3.9 s gap" | 3.9 | `proto reactive .dark_s` |
| Cork, one real 5G drive | Cork, 27 Feb 2020 | `s01.place` |
| Team names | — | `studio/script/CREDITS.md` |

## Page 2 — Existing mechanisms analysed
| Printed | Value | Source |
|---|---|---|
| Documented reactive 2.52 s · 6.7 s | outage 2.5203, airtime 6.7234 | `H.overall.documented.outage_per_transition_s / .emission_tx_s` |
| MOBIKE-style 1.23 s · 43.3 s | 1.2257, 43.2834 | `H.overall.mobike.*` |
| ATSSS 0.87 s · 98.6 s | 0.8745, 98.5886 | `H.overall.atsss.*` |
| "lowest outage of the three standard designs" | 0.87 < 1.23 < 2.52 | same three rows (always-warm, 0.86, is a naive variant, not a standard) |
| 10 s handshake retry [D] | 10 s | `SPEC §4.2` (WP:323) |
| ~60 s periodic re-handshake (key refresh) [D] | ~60 s | `SPEC §4.2` (WP:150–151). A re-key interval, not a recovery step |
| 131,072 packets per key ratchet [D] | 131,072 | `SPEC §4.2` (WP:193–194) |
| 3 × 1 s keepalive dead-peer detection [I] | KA 1 s, DPD_MISS 3 | `SPEC §4.2` / Appendix A I4 |
| "the same for every mechanism" | — | timers are engine constants (`GW`); `POL` contains 0 `timer_override` calls |
| Scatter, x = airtime, y = outage | documented (6.7, 2.52) · MOBIKE (43.3, 1.23) · ATSSS (98.6, 0.87) · always-warm (98.6, 0.86) · oracle (30.0, 0.54) | `H.overall.<mech>.emission_tx_s / .outage_per_transition_s` |
| Empty region | outage < 1 s and airtime below the always-on standby (98.6 s) | always-on = `H.overall.atsss.emission_tx_s` (= always-warm) |

## Page 3 — Solution in detail
| Printed | Value | Source |
|---|---|---|
| Strip window | run 35–70 s of S4:t304_ac, seed 300 | `HO.timelines.seed`; `s04.t` |
| p(t), adaptive threshold | p, th_warm (th is null whenever no warm decision is pending; drawn only where defined) | `s04.p / s04.th_warm` ← `HO…mech.never_go_dark.p / .th_warm` |
| warm start · p 0.73 ≥ 0.55 | p 0.726 at 50.9 s, th 0.55 | `s04.focal` (cross-checked: `warm_req` run starts 50.9, `p[50.9] = 0.726`) |
| ready 51.1 s · cutover 54.6 s · lead 3.7 s | 51.1, 54.6, 3.7 = 54.6 − 50.9 | `s04.focal`; `standby_ready` run starts 51.1; `cutover` at 54.6 |
| Carrying ribbon A/B, standby ribbon | A = Wi-Fi, B = cellular sub-flow | `s04.committed_b`, `standby_warming`, `standby_ready`; `class_a/class_b` |
| First predicted cycle (unlabelled) | warm 41.5 (p 0.644), ready 41.7, cutover 46.4 | `warm_req`, `standby_ready`, `cutover` runs |
| "ready before the active link stalls … one 0.1 s stall tick" | outage ticks [46.3, 46.4) and [54.5, 54.6); active link stays up (`active_up` = 1) | `s04.outage`, `s04.active_up` |
| "At 66.4 s bearer B fails with no warning … dark for 2.1 s" | outage [66.4, 68.5) = 21 ticks; p ≈ 0.08 at 66.5 | `s04.outage`, `active_up` (down 66.4–68.4), `p` |
| ROC-AUC 0.832 · logistic 0.640 | 0.8321, 0.6400 | `P.val_auc_gb / .val_auc_logreg` |
| 120/132 caught (~91%) at 0.65 | 120 of 132 | `P.val_event[threshold = 0.65].caught / .events` |
| median lead 4.9 s, bounded by the 5 s horizon | 4.9; H = 5 | `P.val_event[0.65].median_lead_s`; label horizon `FIND §1` |
| false-alarm tick fraction 0.177 | 0.17707 | `P.val_event[0.65].false_alarm_tick_frac` |
| N = 132 events over 40,073 validation ticks | 132; 40,073 | `P.val_event[*].events`; `P.n_val` |
| "validation split (by trace)" | fit/validation split of the TRAIN traces by `sha1(inst_id) % 4` | `katim-sim/predictor/train.py` lines 11, 71–72 |
| Inputs incl. "each tunnel's configured bearer class" | cls_5g 0.161, cls_sub 0.015; no sig/sinr/mode (modem) features used | `P.feature_importance` |
| Modem-hint variant −0.123; 6.0 s on S5 | −0.1230; 6.0238 | `H.overall.never_go_dark_modem.score`; `H.per_scenario.never_go_dark_modem.S5_mptcp_hsr[0]` |

## Page 4 — System design
| Printed | Value | Source |
|---|---|---|
| about 200 lines of readable logic | 208 non-blank, non-comment lines outside the generated block | `POL`, computed in `make-data.mjs` → `PDF_DATA.policySize.logic_lines_outside` |
| generated decision-tree block, about 1,300 lines | lines 160–1489 = 1,330 lines (`# >>> MODEL` … `# <<< MODEL`) | `POL` → `policySize.model_block / .model_block_lines` |
| policy.py (1,601 lines) | 1,601 | `POL` → `policySize.total_lines` |
| 8 QoS encryption planes, per-plane keys [D] | — | `SPEC §4`, Appendix B (R:70–73; WP:162–165) |
| red / crypto / black, one active cipher port [D] | — | `SPEC §4` (R:53–57) |
| Bearer router [I], selection only | — | `SPEC §4.5` |
| keepalives over the active bearer → telemetry | — | `SPEC §7.1` (estimates from the gateway's own keepalives and traffic) |
| GEO = MODEL | — | `SPEC §3.6` |

## Page 5 — Experimental method
| Printed | Value | Source |
|---|---|---|
| UCC 5G · 676 real switches | 676 | `FIND §8` |
| Lumos5G mmWave · 1,718 blockages | 1,718 | `FIND §8` |
| 7 datasets; GEO = measurement-parameterised model | — | `FIND §8`; `SPEC §3.6` |
| 8 scenarios + REAL / MODEL / STITCHED / SIMULTANEOUS | per scenario | `HO.meta.scenario_construction` |
| weights 1.0 · 1.0 · 0.3 · 0.3 · 0.2; gate shortfall × 10 | W_AOI, W_OUT, W_OVH, W_TX, W_VID; GATE_PENALTY | `EVAL` lines 34–35 |
| score = cost(documented) − cost(policy) − 10 × shortfall; documented design = 0 | — | `EVAL.scalar_score()` |
| AoI, outage, video scaled by documented; overhead, airtime by always-warm | sc_aoi/sc_out/sc_vid from base, sc_ovh/sc_tx from emref | `EVAL.scalar_score()`, `main()` (emref = always_warm.py) |
| evaluator SHA-256 a78f791c… recorded and unchanged | a78f791cb249… | `H.frozen_sha256["eval/evaluate.py"]` = `shasum -a 256 EVAL` (verified 9 Oct) |
| 100 ms tick, zero-order hold; split by trace; seeds 300–319 | — | `SPEC §2.1–2.2, §6.4`; `H.seeds` |
| Loop 1 keep rule (score must rise) | ≥ 0.005, alerts not hurt | `FIND §3` |
| Audit: over-fitted per-scenario constants → adaptive per-link threshold | — | `FIND §0` item 1 and "Decided" |
| Loop 2 fold-guarded: 7 iterations, kept no change | 7 | `FIND §0c` |
| Disclosure (seeds 100–119 audit; nothing tuned on held-out) | — | `FIND ★` / §0 item 5 |

## Page 6 — Results vs existing mechanisms
| Mechanism (sub-label) | Score / Outage / Airtime / Stale p95 / Alerts / Voice / Video | `H.overall.<key>` |
|---|---|---|
| Documented reactive (emulated 9001-R design) | −1.269 / 2.52 / 6.7 / 3.88 / 0.952 / 0.912 / 0.779 | `.documented` |
| MOBIKE-style (RFC 4555) | +0.166 / 1.23 / 43.3 / 2.26 / 0.986 / 0.962 / 0.803 | `.mobike` |
| ATSSS-style (TS 23.501 §5.32) | +0.097 / 0.87 / 98.6 / 2.17 / 0.991 / 0.970 / 0.783 | `.atsss` |
| Always-warm (naive make-before-break) | +0.142 / 0.86 / 98.6 / 2.16 / 0.992 / 0.972 / 0.789 | `.always_warm` |
| **Never Go Dark (shipped policy)** | **+0.373 / 0.83 / 56.7 / 2.00 / 0.992 / 0.981 / 0.844** | `.never_go_dark` |
| Oracle (ceiling, not a mechanism) | +0.759 / 0.54 / 30.0 / 1.89 / 0.995 / 0.982 / 0.841 | `.oracle` |

Fields: Score `.score`, Outage `.outage_per_transition_s`, Airtime `.emission_tx_s`, Stale p95 `.aoi_p95_s`,
Alerts `.alert_hit_rate_feasible`, Voice `.voice_within_hard_feasible`, Video `.video_goodput`.

| Stat callout | Value | Derivation |
|---|---|---|
| +0.373 best composite; next best MOBIKE +0.166 | 0.3730; 0.1658 | `.never_go_dark.score`; `.mobike.score` |
| 0.83 s at 56.7 s; ATSSS 0.87 s; 42% less than 98.6 s | 1 − 56.72/98.59 = 0.425 | `.never_go_dark` vs `.atsss` |
| 3× · 1.5× less outage than documented / MOBIKE | 2.520/0.828 = 3.04; 1.226/0.828 = 1.48 | `.documented`, `.mobike` vs `.never_go_dark` |
| "at more standby airtime than those two (6.7 s and 43.3 s)" | 56.7 > 43.3 > 6.7 | `.emission_tx_s` |
| Replay-fidelity line | outage and gate rates exact; airtime within one 100 ms tick | `FIND ★`; `HO.meta.reproduces_oneshot_exactly` |

## Page 7 — Per-scenario honesty
Bars = `H.per_scenario.<mech>.<scenario>` = [outage, airtime]. CI row = `NF.ngd_vs_atsss.<scenario>.outage_per_transition_s`
(`mean_diff`, `boot_ci_lo`, `boot_ci_hi`, `significant`, `favours`).

| Scenario (tag) | NGD / ATSSS / MOBIKE outage · airtime | NGD − ATSSS [95% CI] | Verdict |
|---|---|---|---|
| S2 5G→LEO (STITCHED) | 0.42/0.35/0.71 · 95/123/86 | +0.065 [−0.102, +0.229] | within noise |
| S3 5G→GEO (MODEL) | 0.67/0.52/0.94 · 93/115/81 | +0.150 [+0.050, +0.233] | ATSSS (the one loss) |
| S4 Wi-Fi edge (STITCHED) | 1.24/1.75/1.58 · 21/118/20 | −0.505 [−1.183, −0.071] | Never Go Dark |
| S5 HSR rail (SIMULTANEOUS) | 0.53/1.42/4.83 · 42/96/0 | −0.891 [−1.269, −0.545] | Never Go Dark |
| S6 collective 5G→GEO (MODEL) | 0.87/0.85/1.11 · 89/110/67 | +0.023 [−0.169, +0.185] | within noise |
| S7 Lumos walk (STITCHED) | 0.55/0.43/0.43 · 42/116/23 | +0.119 [−0.203, +0.451] | within noise |
| S8 Lumos drive (STITCHED) | 0.25/0.23/0.34 · 46/120/36 | +0.013 [−0.138, +0.163] | within noise |

- "Always with less standby airtime than ATSSS": every `NF.ngd_vs_atsss.*.emission_tx_s.significant = true, favours NGD`
  (−27.8, −22.4, −96.8, −53.8, −20.3, −74.2, −73.1 s).
- "MOBIKE uses less standby airtime than Never Go Dark in every scenario": MOBIKE vs NGD airtime
  86.0 vs 95.1 · 80.5 vs 92.9 · 19.5 vs 21.2 · 0.0 vs 41.8 · 67.0 vs 89.4 · 23.4 vs 41.8 · 36.2 vs 46.3.
- Tags: `HO.meta.scenario_construction`.

## Page 8 — Demo (one measured run)
| Printed | Value | Source |
|---|---|---|
| Console screenshot, t = 68 s | board 3.90 / 0.76 / 1.04 / 0.44 | `studio/pdf/assets/console_replay_t68.png`, re-shot 9 Oct from the current `studio/console/dist/console.html` with `render_console.mjs --seek 68` (lane label "emulated 9001-R design"; the older `screens/final` frame still said "KATIM 9001-R") |
| Documented reactive 3.90 s · standby 2.4 s · ≤400 ms 0.910 | | `proto reactive .dark_s / .standby_s / .voice_le_400ms` |
| MOBIKE 0.76 · 0.0 · 0.966 | | `proto mobike` |
| ATSSS 1.04 · 89.5 · 0.982 | | `proto atsss` |
| Never Go Dark 0.44 · 63.2 · 0.992 | | `proto ngd` |
| 8.9× · 2.4× · 1.7× (single measured run) | 3.90/0.44 = 8.86; 1.04/0.44 = 2.36; 0.76/0.44 = 1.73 | `proto *.dark_s` |
| 30% less standby airtime than ATSSS | 1 − 63.24/89.52 = 0.294 | `proto ngd/atsss .standby_s` |
| 1:53 film | 113.0 s | `studio/video/out/ngd_film_v6_*` (6780 frames @ 60 fps; same length since v3); `FIND §10` |
| collapsing at ~43.5 s | 43.46 | `proto reactive .dark_windows_s[0][0]` |

## Page 9 — QoS / QoE guidelines
| Printed | Value | Source |
|---|---|---|
| voice within 400 ms 0.89 → 0.42 (8 → 1 plane) | 0.8941 → 0.4228 | `PL["8|documented"]` → `PL["1|documented"]` `.voice_within_hard_feasible` |
| alerts on time 0.91 → 0.73 | 0.9094 → 0.7327 | same rows, `.alert_hit_rate_feasible` |
| 0.99 alert target | 0.99 | `SPEC §8.2` (ALERT_HIT_MIN) |
| thrashing 17–20 s p95 | 17.09 (1 plane), 19.70 (2 planes) | `PLv1["1|ours_P1+P2"]`, `PLv1["2|ours_P1+P2"]` `.aoi_p95_s`; `FIND §0b.2` |
| median lead 4.9 s; 120 of 132 | | `P.val_event[0.65]` |
| GEO handshake about 1.2–1.8 s at 600 ms RTT (2–3 round trips) | 2 × 0.6 to 3 × 0.6 s + 4 KB / 3 Mbps | `SPEC §4.3`; `GW` HS_ROUNDS = 2 (+1 with PQ), HS_BYTES = 4096; RTT `SPEC §3.6` |
| the one significant always-on edge is S3 | +0.150 [+0.050, +0.233] | `NF.ngd_vs_atsss.S3_5g_geo_drive` |
| 56.7 s vs 98.6 s at 0.83 vs 0.87 s | | `H.overall.never_go_dark / .atsss` |
| MCPTT 300 ms mouth-to-ear; G.114 400 ms planning limit | | 3GPP TS 22.179; ITU-T G.114 (`SPEC §5.3, §8.3`) |
| voice within 400 ms 0.912 documented, 0.981 NGD | | `H.overall.documented / .never_go_dark .voice_within_hard_feasible` |

## Page 10 — Real-life plan
| Printed | Value | Source |
|---|---|---|
| Phase 1 gate: ~91% caught at 0.177 false-alarm tick fraction | 120/132; 0.177 | `P.val_event[0.65]` |
| about 200 lines + generated block of about 1,300 lines | 208; 1,330 | `POL` (see page 4) |
| "the only bearer fact it uses is each tunnel's configured class" | cls_* features; no modem features | `P.feature_importance` |
| oracle ceiling 0.54 s | 0.5411 | `H.overall.oracle.outage_per_transition_s` |
| seeds 300–319, run once | | `H.seeds` |
| Proposed first step | a proposal, no numbers | — |

---

## Revision 2 changes (coordinator fix list) and what was verified
- **G1** "Documented KATIM" / "KATIM 9001-R" removed everywhere in our pages → "Documented reactive" + "emulated 9001-R design"; no "KATIM" left in the printed text (pdftotext check).
- **G2** Removed printed meta text: "Never 'beats KATIM' / 'zero outage'", "three-lens QA passed", "submission packet", "no strawman".
- **G3** Pages 4–7, 9, 10 rebalanced: larger visuals, the audit story (p5), stat callouts (p6), the paired-CI row (p7), the proposal (p10).
- **G4** "~100 lines" → "about 200 lines of readable logic plus a generated decision-tree block (about 1,300 lines of plain Python)", computed from `POL`.
- **G5** Inputs now include "each tunnel's configured bearer class"; "no modem counters, no router integration" kept.
- **G6** 0 em-dashes in the printed text.
- **Data fix found in this pass:** the p2/p6 scatter plotted `standby_tx_s` instead of `emission_tx_s` (it put MOBIKE at 0 s and NGD at 48.7 s). It now uses `emission_tx_s`, matching the table and the locked facts.
- **Rendering fix found in this pass:** a CSS rule (`svg text { fill; font-family }`) was overriding every chart label's colour and face. Removed, so amber NGD labels, verdict colours and the sans/mono split now render as designed.
- **p3** threshold drawn only where defined (it is null when no warm decision is pending; earlier versions drew those ticks as 0).

## Checks on `out/never-go-dark.pdf`
- Pages: 10 (`pdfinfo`). Page size: 1440 × 810 pt = 1920 × 1080 px @ 96 dpi (16:9).
- Fonts: every font embedded (`pdffonts`, no `emb = no` rows). A few symbols (→ ≤ ≥ √) fall back to embedded Menlo/SF.
- Size: see the build log (≈ 1.4 MB, < 15 MB).
- Rebuild: `node make-data.mjs && PLAYWRIGHT_MODULE=<path to playwright/index.js> node build.mjs`.
