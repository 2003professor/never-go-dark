# Results

These are the **frozen outputs** of the evaluation — the authoritative numbers. The headline figures are in the root
[`README.md`](../README.md); the full write-up is the 10-slide deck in [`proposal/`](../proposal/) and the interactive
console (`site/`).

| File | What |
|---|---|
| `final_heldout_s300-319.json` | the one-shot **held-out** result (28 instances · 8 scenarios · seeds 300–319, run once); the composite scores, outage, airtime and gate rates for every mechanism. Policy + evaluator SHA-256 are recorded inside. |
| `predictor-report.json` | the collapse predictor (`no_modem` block = the shipped predictor): ROC-AUC, catch-rate vs false-alarm, median lead. |
| `prototype-repeats.json` | the real-WireGuard prototype batch: 3 bearer setups × 4 mechanisms × 5 runs (60 runs), per-run and mean ± SD voice-dark seconds. |
| `noise_floor.json` | paired-bootstrap 95% confidence intervals (B = 10,000) for Never Go Dark − ATSSS, per scenario. |
| `planes_experiment.json` | the reduced-QoS-planes experiment (8 / 2 / 1 planes) behind the QoS guideline. |

Each number in the deck and console traces to one of these files; provenance (REAL / MODEL / STITCHED / REAL SIMULTANEOUS)
is labelled throughout. The held-out test was run **once**; nothing was tuned on held-out data.
