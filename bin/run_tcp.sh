#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<EOF
Usage:
  $0 --cc <cubic|bbr> --direction <uplink|downlink> --run-id <id> [--parallel <n>] [--duration <sec>] [--rwnd <size>]

Example:
  $0 --cc cubic --direction downlink --run-id test01 --parallel 10 --duration 300
  $0 --cc bbr   --direction downlink --run-id test02 --parallel 1  --duration 180 --rwnd 64K
EOF
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

CC="bbr"
DIRECTION=""
RUN_ID=""
PARALLEL="1"
DURATION_ARG="${DURATION:-300}"
RWND=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cc)
      CC="$2"
      shift 2
      ;;
    --direction)
      DIRECTION="$2"
      shift 2
      ;;
    --run-id)
      RUN_ID="$2"
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
    --rwnd)
      RWND="$2"
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

if [[ "$CC" != "cubic" && "$CC" != "bbr" ]]; then
  echo "[ERROR] --cc must be one of: cubic, bbr"
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

if [[ -n "$RWND" ]]; then
  if ! [[ "$RWND" =~ ^[0-9]+([KkMmGg])?$ ]]; then
    echo "[ERROR] --rwnd must be like 32K, 64K, 128K, 256K, 1M, or plain bytes"
    exit 1
  fi
fi

echo "[INFO] Checking sudo credential..."
sudo -v

TS="$(date +%Y%m%d_%H%M%S)"
EXP_ID="${TS}_tcp_${CC}_${DIRECTION}_${RUN_ID}"
OUT_DIR="${BASE_DIR}/${LOG_ROOT}/${CC}/${DIRECTION}/${EXP_ID}"
TMP_ROOT="${BASE_DIR}/tmp"

mkdir -p "$OUT_DIR"
mkdir -p "$TMP_ROOT"

echo "[INFO] Experiment ID: $EXP_ID"

"$BASE_DIR/bin/collect_meta.sh" "tcp" "$CC" "$DIRECTION" "$RUN_ID" "$OUT_DIR"

cat >> "$OUT_DIR/meta.txt" <<EOF
parallel=$PARALLEL
duration=$DURATION_ARG
rwnd=${RWND:-auto}
rwnd_method=iperf3_window
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

  python3 "$BASE_DIR/graph/tcpinfo_jsh.py" "$OUT_DIR" \
    > "$OUT_DIR/plot_tcpinfo.stdout.log" 2>&1 || true
}

trap cleanup EXIT

if [[ "$DIRECTION" == "uplink" ]]; then
  echo "[INFO] Setting local TCP congestion control to $CC"
  sudo sysctl -w net.ipv4.tcp_congestion_control="$CC" >/dev/null
else
  echo "[WARN] Downlink mode: sender is nsl3. Set CC on nsl3 manually:"
  echo "       sudo sysctl -w net.ipv4.tcp_congestion_control=$CC"
fi

IPERF_ARGS=(
  -B "$LOCAL_IP"
  -c "$SERVER_IP"
  -p "$SERVER_PORT"
  -4
  -P "$PARALLEL"
  -t "$DURATION_ARG"
  -i "$IPERF_INTERVAL"
  --json
)

if [[ "$DIRECTION" == "downlink" ]]; then
  IPERF_ARGS+=(-R)
fi

if [[ -n "$RWND" ]]; then
  IPERF_ARGS+=(-w "$RWND")
fi

echo "[INFO] iperf3 args: ${IPERF_ARGS[*]}" | tee "$OUT_DIR/iperf_command.txt"

iperf3 "${IPERF_ARGS[@]}" \
  > "$OUT_DIR/iperf.json" 2> "$OUT_DIR/iperf.stderr.log"

cleanup
trap - EXIT

plot_graphs

echo "[INFO] TCP experiment completed: $EXP_ID"
echo "[INFO] Output directory: $OUT_DIR"