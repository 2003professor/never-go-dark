# KATIM continuity simulator — reproduction wrapper (SPEC §10.3)
PY ?= python3

.PHONY: reproduce test heldout clean

# one command: run every baseline on the TRAIN split -> results + figures
reproduce:
	$(PY) run_baselines.py

# fast unit tests (ZOH, gap->unusable, handshake, outage, no-leak, split)
test:
	$(PY) tests/test_sim.py

# Trace processing (raw datasets -> traces/processed/) is a separate, dataset-
# specific step, not a target here: the measured data is not bundled (third-party
# licences). The unified schema and per-dataset mapping are in SPEC.md 3.3-3.8;
# sources and how to obtain them are in ../SOURCES.md.

# held-out evaluation of the editable policy (does NOT print during the loop)
heldout:
	$(PY) -m eval.evaluate --split heldout --policy policies/policy.py --seeds heldout

clean:
	rm -f results/bar.csv results/bar.md results/figures/*.png
