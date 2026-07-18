#!/usr/bin/env bash
set -euo pipefail

SERVER_IP="$1"
DURATION_SEC="$2"

if [ $# -lt 2 ]; then
    echo "Usage: $0 <server_ip> <duration_sec> [tag]"
    exit 1
fi

TAG="${3:-pop_interval}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

TS="$(date +"%Y%m%d_%H%M%S")"
OUT_DIR="$BASE_DIR/logs/${TS}_${TAG}"

mkdir -p "$OUT_DIR"

POP_IP="$("$BASE_DIR/bin/get_pop_ip.sh" "$SERVER_IP")"

echo "[INFO] SERVER_IP: $SERVER_IP" | tee "$OUT_DIR/info.txt"
echo "[INFO] POP_IP: $POP_IP" | tee -a "$OUT_DIR/info.txt"
echo "[INFO] DURATION_SEC: $DURATION_SEC" | tee -a "$OUT_DIR/info.txt"
echo "$POP_IP" > "$OUT_DIR/pop_ip.txt"

echo "[INFO] Start pop_interval measurement"

sudo timeout "$DURATION_SEC" bash "$BASE_DIR/bin/pop_interval.sh" \
    "$POP_IP" \
    "$OUT_DIR/pop_interval.log" \
    > "$OUT_DIR/pop_interval.stdout.log" 2>&1 || true

echo "[INFO] Done"
echo "[INFO] Output directory: $OUT_DIR"

python3 "$BASE_DIR/graph/pop_ping_interval.py" "$OUT_DIR"
echo "[INFO] Generated plot: $OUT_DIR/pop_interval.png"