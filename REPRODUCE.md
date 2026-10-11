# Reproducing the results

## Install
Python 3.9+, then: `pip install -r requirements.txt`

## Without any datasets (quick verification)
- `cd simulator && make test` — the invariant tests. Data-dependent tests skip cleanly when
  the datasets are absent (a fresh clone gets **12 passed, 27 skipped, 0 failed**).
- The frozen outputs are already in [`results/`](results/), and
  [`results/CLAIMS.md`](results/CLAIMS.md) maps **every** figure in the deck and console to its
  source file. The headline one-shot held-out result is
  [`results/final_heldout_s300-319.json`](results/final_heldout_s300-319.json).

So every number in the deck can be checked against a committed file with no datasets at all.

## Full reproduction from the measured data
The datasets are third-party and **not redistributed** (see [`SOURCES.md`](SOURCES.md)).

1. **Obtain the datasets.** Download each from its source (SOURCES.md) and unpack it under
   `simulator/traces/raw/<dataset>/`. If unsure of the exact path, run step 2 first — each
   parser prints the path and source URL it needs when a raw file is missing.
2. **Parse raw → processed:** `cd simulator && make traces`
   (writes `traces/processed/<dataset>/`; datasets with missing raw are reported and skipped).
3. **Baselines on the train split:** `make reproduce` (→ `results/` + figures).
4. **The one-shot held-out test:** `python3 -m eval.final_heldout`
   (→ `results/final_heldout_s300-319.json`; it refuses to overwrite an existing one-shot, so
   move the committed file aside first to re-run).
5. **Train the collapse predictor:** `python3 predictor/train.py`.

## A note on determinism
Runs are seeded (`numpy.random.Generator(PCG64(seed))`), so a given machine reproduces
bit-for-bit. Across different numpy/BLAS builds, composite scores can shift by ~1e-4 from
floating-point rounding — well inside the reported precision and the stated "airtime within one
100 ms tick" tolerance; outage and gate rates are identical. (We verified the public code
regenerates the committed held-out numbers to displayed precision, max deviation 1.8e-4.)
