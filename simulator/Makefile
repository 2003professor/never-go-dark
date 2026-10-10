# KATIM continuity simulator — reproduction wrapper (SPEC §10.3)
PY ?= python3

.PHONY: reproduce test traces heldout clean

# one command: run every baseline on the TRAIN split -> results + figures
reproduce:
	$(PY) run_baselines.py

# fast unit tests (ZOH, gap->unusable, handshake, outage, no-leak, split)
test:
	$(PY) tests/test_sim.py

# (re)build processed traces from raw with the dataset parsers. The processed
# CSVs are already committed; this is only needed if raw/ changes. Trace
# parsing is deliberately SEPARATE from evaluation (evaluation needs no network).
traces:
	@for p in traces/parsers/parse_*.py; do echo "== $$p =="; $(PY) $$p || true; done

# held-out evaluation of the editable policy (does NOT print during the loop)
heldout:
	$(PY) -m eval.evaluate --split heldout --policy policies/policy.py --seeds heldout

clean:
	rm -f results/bar.csv results/bar.md results/figures/*.png
