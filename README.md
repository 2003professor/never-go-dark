# Never Go Dark

**Predictive make-before-break for encrypted tunnels** — a software-only adaptation of the documented **KATIM Gateway 9001-R**, built
from public documents only.

ATRC *Advanced Technology Pioneers 2026* · EDGE track · Team 4590 — Mohammad Al Ali · Aya Elsayed · Thani Almarzooqi.

> ### ▶ Interactive console — **best viewed on a computer, not a phone**
> **https://2003professor.github.io/never-go-dark/**
> Replay one real 5G collapse, watch Never Go Dark hold the link, browse the held-out results, and (with the prototype running) a live mode.

---

## What this is

A team on the move carries encrypted voice, video and positions over one bearer at a time. When that link drops, the encrypted tunnel has
to be rebuilt, and for a few seconds everything goes dark. **Never Go Dark** predicts the collapse from telemetry the gateway already has on
its encrypted side, warms a backup tunnel early, and cuts over **before** the active link fails — so the tunnel stays up across the handover.
It is software-only: no new hardware, no modem or router integration, and the crypto core is untouched; only the *timing* of standard
handshakes changes.

Everything here is derived from **public documentation** of the KATIM 9001-R. It is **not affiliated with or endorsed by EDGE**, and uses no
EDGE/KATIM logos or trademarks.

## Headline results

Evaluated **once** on a held-out split (28 instances across 8 scenarios, 7 real datasets, seeds 300–319):

| Mechanism | Composite score | Outage /transition | Standby airtime | Voice ≤400 ms |
|---|---|---|---|---|
| Documented reactive (emulated 9001-R) | −1.269 | 2.52 s | 6.7 s | 0.912 |
| MOBIKE-style (RFC 4555) | +0.166 | 1.23 s | 43.3 s | 0.962 |
| ATSSS active-standby (TS 23.501) | +0.097 | 0.87 s | 98.6 s | 0.970 |
| Always-warm | +0.142 | 0.86 s | 98.6 s | 0.972 |
| **Never Go Dark** | **+0.373** | **0.83 s** | **56.7 s** | **0.981** |
| Oracle (perfect-foresight *ceiling*, not a mechanism) | +0.759 | 0.54 s | 30.0 s | 0.982 |

- **Best composite of every existing mechanism we emulated** — next best MOBIKE at +0.166.
- **ATSSS-level continuity at ~42% less standby airtime** (56.7 s vs 98.6 s, at 0.83 vs 0.87 s outage).
- **Collapse predictor** (no modem counters): ROC-AUC **0.832**, **120 / 132** collapses caught (~91%), median lead **4.9 s**.
- **Confirmed on real WireGuard tunnels:** the shipped policy, run unmodified, had the smallest voice-dark gap — **0.44 s** vs 3.90 s
  (documented), 1.04 s (ATSSS), 0.76 s (MOBIKE); repeated **60 runs** across 3 bearer setups, least-dark in all three.

Full numbers: the headline table above, the frozen result files in [`results/`](results/), and the 10-slide deck in [`proposal/`](proposal/).

## Repository layout

| Path | What |
|---|---|
| `simulator/` | the frozen simulator: `sim/` (engine, gateway, bearers, scenarios), `policies/` (`policy.py` = the shipped Never Go Dark policy, plus the baselines), `predictor/` (the no-modem collapse predictor), `eval/` (the frozen evaluator + the one-shot held-out test), `results/` (result JSONs), `SPEC.md` |
| `prototype/` | the real WireGuard-in-Docker prototype: `scripts/` (bearer replay with `tc netem`, the controllers, HQ services), `docker-compose.yml`, `run_demo.sh`, `run_batch.py` (the 60-run batch), `live_server.py` (host-side live telemetry feed) |
| `results/` | the frozen result JSONs: held-out one-shot, predictor, the 60-run prototype repeats, noise-floor CIs, planes experiment |
| `reference/` | `CONTEXT.md`, `ARCHITECTURE-MAP.md` (the documented/inferred KATIM mapping), `TRACES.md` (the datasets) |
| `proposal/` | the 10-slide proposal deck (PDF) |
| `site/` | the interactive console + the proposal deck (PDF), served by GitHub Pages |

## How to run

**Simulator** (Python 3, numpy):
```bash
cd simulator && make        # or: python3 -m eval.evaluate
python3 predictor/train.py  # trains the collapse predictor
```

**Prototype** (real WireGuard in two Docker containers):
```bash
cd prototype
docker compose up -d
./run_demo.sh ngd           # modes: reactive | mobike | atsss | ngd  (~100 s)
python3 run_batch.py        # the resumable 60-run batch
# live view: python3 live_server.py  → open the console's LIVE mode at localhost:8099
```

**Interactive console:** open **https://2003professor.github.io/never-go-dark/** (or `site/console.html` locally — it is a single
self-contained offline file). **Best viewed on a desktop or laptop**, not a phone.

## Data & provenance

Real measured datasets are **not redistributed** here (third-party licences) — every source is cited in [`SOURCES.md`](SOURCES.md) and
`reference/TRACES.md`, and anyone can obtain them from the originals. Provenance is labelled throughout: **REAL** (measured), **MODEL** (the
GEO bearer is a measurement-parameterised model, not a replay), **STITCHED** (independently-measured legs, only time-aligned), **REAL
SIMULTANEOUS** (the HSR rail capture). The oracle is a perfect-foresight *ceiling*, not a mechanism. The held-out test was run **once**;
nothing was tuned on held-out data.

## AI assistance

AI tools (Claude) aided in the research and design of this project. The team can explain the solution, its decisions and its results in
their own words.

## License

Our code is released under the **MIT License** (see [`LICENSE`](LICENSE)). Third-party datasets are **not included**; see `SOURCES.md` for
their own licences and how to obtain them.
