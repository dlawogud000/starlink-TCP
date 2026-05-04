#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 4 ]; then
  echo "Usage: $0 <protocol:tcp|udp> <cc:cubic|bbr|none> <direction:downlink|uplink> <run_id> [chunk_seconds=60] [num_chunks=5] [gap_seconds=1]"
  exit 1
fi

PROTOCOL="$1"
CC="$2"
DIRECTION="$3"
RUN_ID="$4"
CHUNK_SECONDS="${5:-60}"
NUM_CHUNKS="${6:-5}"
GAP_SECONDS="${7:-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

TS="$(date +%Y%m%d_%H%M%S)"
EXP_ID="${TS}_${PROTOCOL}_${CC}_${DIRECTION}_${RUN_ID}_chunked"
OUT_DIR="${BASE_DIR}/${LOG_ROOT}/${EXP_ID}"
TMP_ROOT="${BASE_DIR}/tmp"

mkdir -p "$OUT_DIR"
mkdir -p "$TMP_ROOT"

echo "[INFO] Chunked experiment ID: $EXP_ID"

# meta
"$BASE_DIR/bin/collect_meta.sh" "$PROTOCOL" "$CC" "$DIRECTION" "$RUN_ID" "$OUT_DIR"
cat >> "$OUT_DIR/meta.txt" <<EOF
chunk_seconds=$CHUNK_SECONDS
num_chunks=$NUM_CHUNKS
gap_seconds=$GAP_SECONDS
total_planned_duration=$((CHUNK_SECONDS * NUM_CHUNKS))
EOF

"$BASE_DIR/bin/sync_time_check.sh" > "$OUT_DIR/time_sync.txt" 2>&1 || true

# start monitors once for the whole chunked run
"$BASE_DIR/bin/start_monitors.sh" "$OUT_DIR"

cleanup() {
  "$BASE_DIR/bin/stop_monitors.sh" "$OUT_DIR" || true
}
trap cleanup EXIT

RUN_START_EPOCH="$(date +%s.%N)"
MANIFEST="$OUT_DIR/chunk_manifest.csv"
echo "chunk_idx,start_epoch,end_epoch,duration_seconds,json_file,stderr_file" > "$MANIFEST"

REVERSE_FLAG=""
if [ "$DIRECTION" = "downlink" ]; then
  REVERSE_FLAG="-R"
fi

if [ "$PROTOCOL" = "tcp" ]; then
  sudo sysctl -w net.ipv4.tcp_congestion_control="$CC" >/dev/null
fi

for i in $(seq 1 "$NUM_CHUNKS"); do
  CHUNK_JSON="$OUT_DIR/iperf_chunk${i}.json"
  CHUNK_STDERR="$OUT_DIR/iperf_chunk${i}.stderr.log"

  echo "[INFO] Starting chunk $i/$NUM_CHUNKS"

  CHUNK_START_EPOCH="$(date +%s.%N)"

  if [ "$PROTOCOL" = "tcp" ]; then
    iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" -p "$SERVER_PORT" \
      $REVERSE_FLAG \
      -t "$CHUNK_SECONDS" -i "$IPERF_INTERVAL" --json \
      > "$CHUNK_JSON" 2> "$CHUNK_STDERR"
  elif [ "$PROTOCOL" = "udp" ]; then
    UDP_RATE="${UDP_RATE:-100M}"
    iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" -p "$SERVER_PORT" \
      -u $REVERSE_FLAG -b "$UDP_RATE" \
      -t "$CHUNK_SECONDS" -i "$IPERF_INTERVAL" --json \
      > "$CHUNK_JSON" 2> "$CHUNK_STDERR"
  else
    echo "[ERROR] Unsupported protocol for chunked experiment: $PROTOCOL"
    exit 1
  fi

  CHUNK_END_EPOCH="$(date +%s.%N)"
  echo "${i},${CHUNK_START_EPOCH},${CHUNK_END_EPOCH},${CHUNK_SECONDS},$(basename "$CHUNK_JSON"),$(basename "$CHUNK_STDERR")" >> "$MANIFEST"

  if [ "$i" -lt "$NUM_CHUNKS" ]; then
    sleep "$GAP_SECONDS"
  fi
done

cleanup
trap - EXIT

echo "[INFO] Generating plots..."

python3 "$BASE_DIR/graph/chunked_iperf.py" "$OUT_DIR" \
  > "$OUT_DIR/plot_chunked_iperf.stdout.log" 2>&1 || true

if [ "$PROTOCOL" = "tcp" ]; then
  python3 "$BASE_DIR/graph/tcpinfo.py" "$OUT_DIR" \
    > "$OUT_DIR/plot_tcpinfo.stdout.log" 2>&1 || true
fi

echo "[INFO] Chunked experiment completed: $EXP_ID"