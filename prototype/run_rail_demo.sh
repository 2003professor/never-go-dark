#!/bin/bash
# Self-contained LIVE demo: high-speed rail, two carriers captured simultaneously.
# Drives both bearers from the committed timelines in ./timelines/ (rail_a.csv / rail_b.csv),
# so it needs NO external trace file — unlike the Cork 5G setup, whose UCC drive trace is
# not bundled. This is the guaranteed-runnable booth demo.
#
# Prereqs (once per boot): Docker running, then  docker compose up -d  (brings up
# katim-hq + katim-vehicle), and optionally  python3 live_server.py  for the console LIVE feed.
#
# usage: ./run_rail_demo.sh [reactive|mobike|atsss|p2|ngd] [run_id]   (default mode: ngd)
set -euo pipefail
cd "$(dirname "$0")"
MODE=${1:-ngd}
RUN=${2:-rail_${MODE}_$(date +%H%M%S)}

# Rail setup parameters (mirror the "rail" entry in setups.json). Bearer A/B come from the
# committed timelines; START is irrelevant for timeline-driven replay but kept for the logs.
TIMELINE_A=/app/timelines/rail_a.csv \
TIMELINE_B=/app/timelines/rail_b.csv \
START=2 DUR=90 \
CLASS_A=cellular-subflow CLASS_B=cellular-subflow \
./run_demo.sh "$MODE" "$RUN"
