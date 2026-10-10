#!/bin/bash
# One prototype run: real trace on bearer A, GEO model on bearer B, one mechanism.
# usage: ./run_demo.sh <reactive|mobike|atsss|p2|ngd> [run_id]   (ngd = the shipped Never Go Dark policy file)
set -euo pipefail
MODE=$1
RUN=${2:-${MODE}_$(date +%H%M%S)}
TRACE=${TRACE:-/traces/ucc5g/Download__Driving__B_2020.02.27_20.35.57.csv}
START=${START:-280}
DUR=${DUR:-90}
VIDEO_MBPS=${VIDEO_MBPS:-1.5}
# optional (export_timelines.py / setups.json): sim-exported bearer timelines + router bearer classes
TL_ARGS=""
[ -n "${TIMELINE_A:-}" ] && TL_ARGS="$TL_ARGS --timeline-a $TIMELINE_A"
[ -n "${TIMELINE_B:-}" ] && TL_ARGS="$TL_ARGS --timeline-b $TIMELINE_B"
CLASS_A=${CLASS_A:-5G}
CLASS_B=${CLASS_B:-GEO}
cd "$(dirname "$0")"

docker exec katim-vehicle python3 /app/scripts/controller.py --reset
T0=$(docker exec katim-vehicle python3 -c 'import time; print(time.time() + 4)')
for role in hq vehicle; do
  docker exec -d katim-$role python3 /app/scripts/linkdrive.py --role $role --run "$RUN" \
    --trace "$TRACE" --start "$START" --duration "$DUR" --t0 "$T0" $TL_ARGS
done
if [ "$MODE" = "ngd" ]; then
  docker exec -d katim-vehicle python3 /app/scripts/controller_ngd.py --run "$RUN" --duration "$DUR" --t0 "$T0" --class-a "$CLASS_A" --class-b "$CLASS_B"
else
  docker exec -d katim-vehicle python3 /app/scripts/controller.py --mode "$MODE" --run "$RUN" \
    --duration "$DUR" --t0 "$T0"
fi
sleep 4
docker exec -d katim-vehicle python3 /app/scripts/sender.py --run "$RUN" --duration "$DUR" --kind voice
docker exec -d katim-vehicle python3 /app/scripts/sender.py --run "$RUN" --duration "$DUR" --kind video --video-mbps "$VIDEO_MBPS"
docker exec -d katim-vehicle python3 /app/scripts/sender.py --run "$RUN" --duration "$DUR" --kind bulk
echo "[$RUN] running $MODE for ${DUR}s on $(basename "$TRACE") from t=$START ${TL_ARGS:+(timelines:$TL_ARGS)} classes $CLASS_A/$CLASS_B ..."
sleep $((DUR + 4))
python3 analyze.py "$RUN" "$MODE"
