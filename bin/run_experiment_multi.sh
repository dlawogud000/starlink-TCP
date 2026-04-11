#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 4 ]; then
  echo "Usage: $0 <protocol:tcp|udp|http> <cc:CUBIC|BBR|None> <direction:downlink|uplink> <run_id>"
  exit 1
fi

PROTOCOL="$1"
CC="$2"
DIRECTION="$3"
RUN_ID="$4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

TS="$(date +%Y%m%d_%H%M%S)"
EXP_ID="${TS}_${PROTOCOL}_${CC}_${DIRECTION}_${RUN_ID}"
OUT_DIR="${BASE_DIR}/${LOG_ROOT}/${EXP_ID}"
TMP_ROOT="${BASE_DIR}/tmp"

mkdir -p "$OUT_DIR"
mkdir -p "$TMP_ROOT"

echo "[INFO] Experiment ID: $EXP_ID"

"$BASE_DIR/bin/collect_meta.sh" "$PROTOCOL" "$CC" "$DIRECTION" "$RUN_ID" "$OUT_DIR"
"$BASE_DIR/bin/sync_time_check.sh" > "$OUT_DIR/time_sync.txt" 2>&1 || true

"$BASE_DIR/bin/start_monitors.sh" "$OUT_DIR"

cleanup() {
  "$BASE_DIR/bin/stop_monitors.sh" "$OUT_DIR" || true
}

plot_graphs() {
  echo "[INFO] Generating plots..."
  if [ "$PROTOCOL" = "tcp" ] || [ "$PROTOCOL" = "udp" ]; then
    python3 "$BASE_DIR/graph/iperf_multi.py" "$OUT_DIR" \
      > "$OUT_DIR/plot_iperf.stdout.log" || true
  fi

  if [ "$PROTOCOL" = "tcp" ]; then
    python3 "$BASE_DIR/graph/tcpinfo_multi.py" "$OUT_DIR" \
      > "$OUT_DIR/plot_tcpinfo.stdout.log" || true
  fi
}

trap cleanup EXIT

if [ "$PROTOCOL" = "tcp" ] || [ "$PROTOCOL" = "udp" ]; then
  "$BASE_DIR/bin/run_iperf_multi.sh" "$PROTOCOL" "$CC" "$DIRECTION" "$OUT_DIR"
elif [ "$PROTOCOL" = "http" ]; then
  "$BASE_DIR/bin/run_http_probe.sh" "$OUT_DIR"
else
  echo "[ERROR] Unknown protocol: $PROTOCOL"
  exit 1
fi

cleanup
trap - EXIT

plot_graphs

echo "[INFO] Experiment completed: $EXP_ID"