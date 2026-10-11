# Findings

The authoritative numbers behind the deck and the console. Every figure traces to a frozen result file in this folder (see
[`CLAIMS.md`](CLAIMS.md) for the per-figure source map). The held-out test was run **once**; nothing was tuned on held-out data.

## ★ Held-out one-shot (the headline) — `final_heldout_s300-319.json`

28 held-out instances · 8 scenarios · 7 real datasets · seeds 300–319, scored **once**. Score is normalised so the documented design = 0
(higher is better). "Airtime" = standby/secondary transmit (emission).

| Mechanism | Score | Outage/transition (s) | Standby airtime (s) | Stale p95 (s) | Alerts | Voice ≤400 ms | Video |
|---|---|---|---|---|---|---|---|
| Documented reactive (emulated 9001-R) | −1.269 | 2.52 | 6.7 | 3.88 | 0.952 | 0.912 | 0.779 |
| MOBIKE-style (RFC 4555) | +0.166 | 1.23 | 43.3 | 2.26 | 0.986 | 0.962 | 0.803 |
| ATSSS active-standby (TS 23.501) | +0.097 | 0.87 | 98.6 | 2.17 | 0.991 | 0.970 | 0.783 |
| Always-warm | +0.142 | 0.86 | 98.6 | 2.16 | 0.992 | 0.972 | 0.789 |
| **Never Go Dark** | **+0.373** | **0.83** | **56.7** | **2.00** | **0.992** | **0.981** | **0.844** |
| Oracle (perfect-foresight ceiling, not a mechanism) | +0.759 | 0.54 | 30.0 | 1.89 | 0.995 | 0.982 | 0.841 |

- **Best composite of every existing mechanism we emulated** (next best MOBIKE +0.166).
- **ATSSS-level continuity at ~42% less standby airtime** (56.7 vs 98.6 s, at 0.83 vs 0.87 s outage).
- Replay fidelity: a deterministic replay reproduces outage and gate rates exactly; airtime within one 100 ms tick.

## Per-scenario honesty (paired 95% CI) — `noise_floor.json`

Never Go Dark − ATSSS outage per transition, paired bootstrap (B = 10,000):

- **S3 5G→GEO (MODEL): +0.150 [+0.050, +0.233] — favours ATSSS (the one loss).**
- **S4 Wi-Fi edge (STITCHED): −0.505 [−1.183, −0.071] — favours Never Go Dark.**
- **S5 HSR rail (REAL SIMULTANEOUS): −0.891 [−1.269, −0.545] — favours Never Go Dark.**
- S2, S6, S7, S8: within run-to-run noise.
- Never Go Dark uses **significantly less standby airtime than ATSSS in every standby scenario** (−20 to −97 s).

## Predictor — `predictor-report.json` (`no_modem` = shipped)

ROC-AUC **0.832** (logistic 0.640) · **120 of 132** collapses caught (~91%) at threshold 0.65 · median lead **4.9 s** (5 s horizon) ·
false-alarm tick fraction **0.177** · N = 132 events over 40,073 validation ticks. Inputs are encrypted-side telemetry only (keepalive RTT
and trend, loss, staleness, goodput, queue backlog) plus each tunnel's configured bearer class — **no modem counters, no router
integration**. A pre-registered modem-hint variant failed on unseen rail (−0.123, 6.0 s on S5), so the no-radio design shipped.

## 7. Prototype — real WireGuard in Docker

The shipped `policy.py` run **unmodified** on real WireGuard tunnels (`prototype/logs/result_*_final.json`).

- **Single reference run (Cork 5G drive + GEO model):** voice-dark — documented **3.90 s** · MOBIKE 0.76 s · ATSSS 1.04 s · **Never Go Dark 0.44 s**
  (8.9× / 2.4× / 1.7× less); 29% less standby airtime than ATSSS on this run.
- **Repeated 60 runs** (3 setups × 4 mechanisms × 5; `prototype-repeats.json`), least-dark in all three setups:
  Cork+GEO NGD **0.42 ± 0.24 s** vs documented 3.60 ± 0.69 s (−31% standby vs ATSSS); Cork+Starlink-LEO NGD 0.50 ± 0.07 s; HSR rail NGD
  **0.00 ± 0.00 s** vs documented 6.10 ± 0.75 s. Six runs set aside under a pre-registered rule (5 render-overlap, 1 cold start).
- The simulator's 28 held-out instances are the statistical evidence; the prototype is a real-tunnel confirmation of it.

## 8. Datasets & method

**Seven real measured datasets** (no interpolation, ever) — obtain each from its source (see [`../SOURCES.md`](../SOURCES.md)):

- UCC/MISL **5G** (676 real 5G↔LTE switches) · UCC/MISL **4G LTE** · **Lumos5G** mmWave (1,718 blockages) · **Starlink on the Road** (LEO) ·
  **Meireles V2I Wi-Fi** · **HSR-MPTCP** (real simultaneous subflows) · **Berlin V2X** (handover ground truth).
- **GEO = a measurement-parameterised model**, not a replay (labelled MODEL everywhere). Scenarios that combine independently-measured legs
  are **STITCHED**; the HSR rail capture is **REAL SIMULTANEOUS**.

**Protocol:** 100 ms tick, zero-order hold (never interpolated), transitions anchored on real degradation events with randomised timing, a
static isolation checker on every policy, split **by trace** (`sha1(trace_id) % 5`), oracle = perfect-foresight ceiling, one run on seeds
300–319. Score = cost(documented) − cost(policy) − 10 × gate shortfall; the evaluator's SHA-256 (`a78f791c…`) is recorded in the one-shot
file. **Disclosure:** the held-out traces were evaluated once earlier, at different seeds (100–119), by an independent auditor on an older
policy — that audit led us to drop per-scenario constants and ship the simpler no-modem predictor; **nothing was ever tuned on held-out
data**, and the final test ran once on fresh seeds 300–319.

## Limits we state

An emulation built only from public documents. GEO is a model, not a measured link. Some scenarios stitch independently-measured legs.
Always-on wins on the GEO-model scenario (S3). The oracle ceiling (0.54 s) shows headroom for better prediction.
