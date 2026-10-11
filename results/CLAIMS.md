# Claims register — every printed number → its source

Each figure in the deck and console traces to a frozen file in this repository. Paths are repo-relative. Rounding: outage 2 dp,
airtime 1 dp, scores/rates 3 dp. "Standby airtime" = `emission_tx_s` (secondary/standby transmit).

**Sources:** `results/final_heldout_s300-319.json` (held-out one-shot) · `results/predictor-report.json` (`no_modem` = shipped) ·
`results/noise_floor.json` (paired-bootstrap CIs) · `results/planes_experiment.json` + `simulator/results/planes_experiment_v1_thrashing.json` ·
`results/prototype-repeats.json` (60-run batch) · `prototype/logs/result_{reactive,mobike,atsss,ngd}_final.json` (single reference run) ·
`results/heldout.json` (per-tick held-out timelines) · `simulator/policies/policy.py` · `simulator/SPEC.md` · `results/FINDINGS.md`.

## Slide 1 — Executive summary
| Printed | Source |
|---|---|
| Cover race 3.90 / 0.76 / 1.04 / 0.44 s (voice-dark, 4 mechanisms) | `prototype/logs/result_*_final.json .dark_s` |
| Bearer-A capacity line, collapse t+44 s, recover t+62 s | the measured UCC 5G drive (run through the prototype) |
| "best composite… ; 42% less standby airtime" | `results/final_heldout_s300-319.json` (see slide 6) |

## Slide 2 — Problem & existing mechanisms
| Printed | Source |
|---|---|
| Documented reactive 2.52 s · 6.7 s; MOBIKE 1.23 s · 43.3 s; ATSSS 0.87 s · 98.6 s | `final_heldout_s300-319.json .overall.{documented,mobike,atsss}` |
| Timers: 10 s retry · ~60 s re-handshake · 131,072-packet ratchet · 3×1 s DPD | `simulator/SPEC.md §4.2` ([D]/[I]) |

## Slide 3 — Proposed adaptation
| Printed | Source |
|---|---|
| p(t), adaptive threshold 0.55, warm/ready/cutover, "B fails… 2.1 s dark" | `results/heldout.json` → instance `S4:t304_ac`, seed 300 |
| ROC-AUC 0.832 / logistic 0.640 · 120/132 (~91%) @0.65 · lead 4.9 s · false-alarm 0.177 · N=132 / 40,073 ticks | `results/predictor-report.json` (`no_modem`) |
| modem-hint variant −0.123, 6.0 s on S5 | `final_heldout_s300-319.json` (`never_go_dark_modem`) |

## Slide 4 — System design
| Printed | Source |
|---|---|
| ~200 lines of logic + ~1,300-line generated block; policy.py 1,601 lines | `simulator/policies/policy.py` (counted) |
| 8 QoS planes [D], red/crypto/black, bearer router [I] | `simulator/SPEC.md §4` |

## Slide 5 — Experimental method
| Printed | Source |
|---|---|
| 7 datasets (UCC 5G 676 switches; Lumos5G 1,718 blockages; …); GEO = model | `results/FINDINGS.md §8`; datasets per `SOURCES.md` |
| 8 scenarios + REAL/MODEL/STITCHED/SIMULTANEOUS construction | `results/heldout.json` meta; `simulator/SPEC.md §3, §6` |
| score weights 1.0/1.0/0.3/0.3/0.2; evaluator SHA-256 a78f791c | `simulator/eval/evaluate.py`; `final_heldout_s300-319.json .frozen_sha256` |
| disclosure (seeds 100–119 audit; nothing tuned on held-out) | `results/FINDINGS.md §8` |

## Slide 6 — Results vs existing mechanisms
Full table (score / outage / airtime / stale p95 / alerts / voice / video) = `final_heldout_s300-319.json .overall.<mech>`:
documented −1.269 / 2.52 / 6.7 / 3.88 / 0.952 / 0.912 / 0.779 · mobike +0.166 / 1.23 / 43.3 / 2.26 / 0.986 / 0.962 / 0.803 ·
atsss +0.097 / 0.87 / 98.6 / 2.17 / 0.991 / 0.970 / 0.783 · always-warm +0.142 / 0.86 / 98.6 / 2.16 / 0.992 / 0.972 / 0.789 ·
**never_go_dark +0.373 / 0.83 / 56.7 / 2.00 / 0.992 / 0.981 / 0.844** · oracle +0.759 / 0.54 / 30.0 / 1.89 / 0.995 / 0.982 / 0.841.
Callouts: +0.373 (next MOBIKE +0.166); 42% = 1 − 56.7/98.6; 3× = 2.52/0.83, 1.5× = 1.23/0.83.
Per-scenario strip (CIs) = `results/noise_floor.json .ngd_vs_atsss.<scenario>`.

## Slide 7 — Implementation (real WireGuard)
| Printed | Source |
|---|---|
| Single run: 3.90 / 0.76 / 1.04 / 0.44 s; standby 2.4 / 0.0 / 89.5 / 63.2 s; ≤400 ms 0.910 / 0.966 / 0.982 / 0.992 | `prototype/logs/result_*_final.json` |
| 8.9× / 2.4× / 1.7×; 29% this run (1 − 63.2/89.5); −31% over 5 runs | same; `results/prototype-repeats.json` |
| 60-run means 0.42±0.24 / 3.60±0.69 / 0.50±0.07 / 0.00±0.00 / 6.10±0.75; 6 set aside | `results/prototype-repeats.json .setups[].mechanisms[].summary` |

## Slide 8 — Feasibility & rollout
| Printed | Source |
|---|---|
| ~91% caught @0.177 false-alarm (phase-1 gate) | `results/predictor-report.json` |
| ~200 + ~1,300 lines; oracle ceiling 0.54 s; "60 real-tunnel runs" | `simulator/policies/policy.py`; `final_heldout_s300-319.json .overall.oracle`; `prototype-repeats.json` |

## Slide 9 — Impact + QoS guidance
| Printed | Source |
|---|---|
| voice 0.89 → 0.42, alerts 0.91 → 0.73 (8 → 1 plane) | `results/planes_experiment.json` |
| thrashing 17–20 s p95 (guideline 2) | `simulator/results/planes_experiment_v1_thrashing.json` |
| lead 4.9 s, 120/132; 56.7 vs 98.6 s; voice 0.912 → 0.981 | `predictor-report.json`; `final_heldout_s300-319.json` |

## Slide 10 — Team, assumptions, supporting materials
Supporting materials cited: the interactive console (`site/`), the 1:58 film, and the evidence pack — the result files in `results/`,
this claims register, and the simulator + evaluator (`simulator/`). Assumptions A1–A4 and the disclosure: `results/FINDINGS.md`.
