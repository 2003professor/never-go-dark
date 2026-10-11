# Results

The authoritative outputs of the evaluation. Start with [`FINDINGS.md`](FINDINGS.md) (the findings) and
[`CLAIMS.md`](CLAIMS.md) (every printed figure → its source file). The full write-up is the 10-slide deck in
[`proposal/`](../proposal/) and the interactive console (`site/`).

| File | What |
|---|---|
| `FINDINGS.md` | the findings: held-out results, per-scenario honesty, predictor, prototype, datasets & method (§8), limits |
| `CLAIMS.md` | the claims register — every number in the deck/console mapped to its source file, slide by slide |
| `final_heldout_s300-319.json` | the one-shot **held-out** result (28 instances · 8 scenarios · seeds 300–319, run once); composite scores, outage, airtime, gate rates. Policy + evaluator SHA-256 inside. |
| `heldout.json` | per-tick held-out timelines (the source of the p(t) strip; instance `S4:t304_ac`, seed 300) and the scenario construction |
| `predictor-report.json` | the collapse predictor (`no_modem` = shipped): ROC-AUC, catch-rate vs false-alarm, median lead |
| `prototype-repeats.json` | the real-WireGuard batch: 3 setups × 4 mechanisms × 5 runs (60 runs), per-run and mean ± SD voice-dark seconds |
| `noise_floor.json` | paired-bootstrap 95% CIs (B = 10,000) for Never Go Dark − ATSSS, per scenario |
| `planes_experiment.json` | the reduced-QoS-planes experiment (8 / 2 / 1 planes) behind the QoS guideline |

The single reference prototype run is in `../prototype/logs/result_{reactive,mobike,atsss,ngd}_final.json`. The held-out test was
run **once**; nothing was tuned on held-out data.
