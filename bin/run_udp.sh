#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  $0 --direction <uplink|downlink> --run-id <id> [--rate <100M|200M|1G>] [--parallel <n>] [--duration <sec>]

Example:
  $0 --direction uplink   --run-id test01 --rate 100M --parallel 1  --duration 300
  $0 --direction downlink --run-id test02 --rate 300M --parallel 10 --duration 180
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

DIRECTION=""
RUN_ID=""
UDP_RATE_ARG="${UDP_RATE:-100M}"
PARALLEL="1"
DURATION_ARG="${DURATION:-300}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --direction)
      DIRECTION="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
      shift 2
      ;;
    --rate)
      UDP_RATE_ARG="$2"
      shift 2
      ;;
    --parallel)
      PARALLEL="$2"
      shift 2
      ;;
    --duration)
      DURATION_ARG="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "[ERROR] Unknown argument: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$DIRECTION" || -z "$RUN_ID" ]]; then
  echo "[ERROR] Missing required arguments"
  usage
  exit 1
fi

if [[ "$DIRECTION" != "uplink" && "$DIRECTION" != "downlink" ]]; then
  echo "[ERROR] --direction must be one of: uplink, downlink"
  exit 1
fi

if ! [[ "$PARALLEL" =~ ^[0-9]+$ ]] || [[ "$PARALLEL" -lt 1 ]]; then
  echo "[ERROR] --parallel must be a positive integer"
  exit 1
fi

if ! [[ "$DURATION_ARG" =~ ^[0-9]+$ ]] || [[ "$DURATION_ARG" -lt 1 ]]; then
  echo "[ERROR] --duration must be a positive integer"
  exit 1
fi

echo "[INFO] Checking sudo credential..."
sudo -v

TS="$(date +%Y%m%d_%H%M%S)"
EXP_ID="${TS}_udp_none_${DIRECTION}_${RUN_ID}"
OUT_DIR="${BASE_DIR}/${LOG_ROOT}/udp/${DIRECTION}/${EXP_ID}"
TMP_ROOT="${BASE_DIR}/tmp"

mkdir -p "$OUT_DIR"
mkdir -p "$TMP_ROOT"

echo "[INFO] Experiment ID: $EXP_ID"

"$BASE_DIR/bin/collect_meta.sh" "udp" "none" "$DIRECTION" "$RUN_ID" "$OUT_DIR"
cat >> "$OUT_DIR/meta.txt" <<EOF
parallel=$PARALLEL
duration=$DURATION_ARG
udp_rate=$UDP_RATE_ARG
EOF

"$BASE_DIR/bin/sync_time_check.sh" > "$OUT_DIR/time_sync.txt" 2>&1 || true
"$BASE_DIR/bin/start_monitors.sh" "$OUT_DIR"

cleanup() {
  "$BASE_DIR/bin/stop_monitors.sh" "$OUT_DIR" || true
}

plot_graphs() {
  echo "[INFO] Generating plots..."
  python3 "$BASE_DIR/graph/iperf_jsh.py" "$OUT_DIR" \
    > "$OUT_DIR/plot_iperf.stdout.log" 2>&1 || true
}

trap cleanup EXIT

REVERSE_FLAG=""
if [[ "$DIRECTION" == "downlink" ]]; then
  REVERSE_FLAG="-R"
fi

iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" -p "$SERVER_PORT" -u $REVERSE_FLAG -4 \
  -P "$PARALLEL" \
  -b "$UDP_RATE_ARG" \
  -t "$DURATION_ARG" -i "$IPERF_INTERVAL" --json --get-server-output \
  > "$OUT_DIR/iperf.json" 2> "$OUT_DIR/iperf.stderr.log"

cleanup
trap - EXIT

plot_graphs

echo "[INFO] UDP experiment completed: $EXP_ID"