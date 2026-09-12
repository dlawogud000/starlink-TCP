#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Usage
# ============================================================
if [ $# -lt 2 ]; then
    echo "Usage: $0 <server_ip> <duration_sec> [tag]"
    exit 1
fi

SERVER_IP="$1"
DURATION_SEC="$2"
TAG="${3:-multi_interval}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ============================================================
# Targets
# ============================================================
# Starlink/local gateway.
GATEWAY_IP="${GATEWAY_IP:-192.168.1.1}"

# Public Internet target.
PUBLIC_IP="${PUBLIC_IP:-1.1.1.1}"

# Optional second Internet target.
PUBLIC2_IP="${PUBLIC2_IP:-8.8.8.8}"

# Get the current POP IP dynamically.
POP_IP="$("$BASE_DIR/bin/get_pop_ip.sh" "$SERVER_IP")"

if [ -z "$POP_IP" ]; then
    echo "[ERROR] Failed to obtain POP IP"
    exit 1
fi

# ============================================================
# Output directory
# ============================================================
TS="$(date +"%Y%m%d_%H%M%S")"
OUT_DIR="$BASE_DIR/logs/${TS}_${TAG}"

mkdir -p "$OUT_DIR"

# Separate subdirectories make it possible to reuse
# graph/pop_ping_interval.py without changing that script.
POP_DIR="$OUT_DIR/pop"
PUBLIC_DIR="$OUT_DIR/public_1_1_1_1"
PUBLIC2_DIR="$OUT_DIR/public_8_8_8_8"
GATEWAY_DIR="$OUT_DIR/gateway"

mkdir -p \
    "$POP_DIR" \
    "$PUBLIC_DIR" \
    "$PUBLIC2_DIR" \
    "$GATEWAY_DIR"

# ============================================================
# Metadata
# ============================================================
{
    echo "[INFO] SERVER_IP: $SERVER_IP"
    echo "[INFO] POP_IP: $POP_IP"
    echo "[INFO] PUBLIC_IP: $PUBLIC_IP"
    echo "[INFO] PUBLIC2_IP: $PUBLIC2_IP"
    echo "[INFO] GATEWAY_IP: $GATEWAY_IP"
    echo "[INFO] DURATION_SEC: $DURATION_SEC"
    echo "[INFO] START_TIME: $(date --iso-8601=ns)"
} | tee "$OUT_DIR/info.txt"

echo "$POP_IP"     > "$POP_DIR/target_ip.txt"
echo "$PUBLIC_IP"  > "$PUBLIC_DIR/target_ip.txt"
echo "$PUBLIC2_IP" > "$PUBLIC2_DIR/target_ip.txt"
echo "$GATEWAY_IP" > "$GATEWAY_DIR/target_ip.txt"

# ============================================================
# Process management
# ============================================================
PIDS=()

cleanup() {
    echo
    echo "[INFO] Stopping interval measurements..."

    for pid in "${PIDS[@]:-}"; do
        kill -TERM "$pid" 2>/dev/null || true
    done

    sleep 0.5

    for pid in "${PIDS[@]:-}"; do
        kill -KILL "$pid" 2>/dev/null || true
    done
}

trap cleanup INT TERM

# ============================================================
# Measurement helper
# ============================================================
start_interval_measurement() {
    local target_ip="$1"
    local target_dir="$2"
    local label="$3"

    echo "[INFO] Starting $label interval measurement: $target_ip"

    sudo timeout "$DURATION_SEC" \
        bash "$BASE_DIR/bin/pop_interval.sh" \
        "$target_ip" \
        "$target_dir/pop_interval.log" \
        > "$target_dir/pop_interval.stdout.log" 2>&1 &

    PIDS+=("$!")
}

# ============================================================
# Start all measurements at almost the same time
# ============================================================
echo
echo "[INFO] Starting simultaneous interval measurements..."

START_EPOCH="$(date +%s.%N)"
echo "$START_EPOCH" > "$OUT_DIR/start_time_epoch.txt"

start_interval_measurement \
    "$POP_IP" \
    "$POP_DIR" \
    "POP"

start_interval_measurement \
    "$PUBLIC_IP" \
    "$PUBLIC_DIR" \
    "1.1.1.1"

start_interval_measurement \
    "$PUBLIC2_IP" \
    "$PUBLIC2_DIR" \
    "8.8.8.8"

start_interval_measurement \
    "$GATEWAY_IP" \
    "$GATEWAY_DIR" \
    "Gateway"

# ============================================================
# Wait for all measurements
# ============================================================
for pid in "${PIDS[@]}"; do
    wait "$pid" || true
done

PIDS=()
trap - INT TERM

END_EPOCH="$(date +%s.%N)"
echo "$END_EPOCH" > "$OUT_DIR/end_time_epoch.txt"

echo
echo "[INFO] All measurements completed"

# ============================================================
# Generate individual plots
# ============================================================
plot_one() {
    local dir="$1"
    local label="$2"

    echo "[INFO] Generating $label plot..."

    python3 "$BASE_DIR/graph/pop_ping_interval.py" "$dir" \
        > "$dir/plot.stdout.log" 2>&1 || {
            echo "[WARN] Failed to generate plot for $label"
        }
}

plot_one "$POP_DIR" "POP"
plot_one "$PUBLIC_DIR" "1.1.1.1"
plot_one "$PUBLIC2_DIR" "8.8.8.8"
plot_one "$GATEWAY_DIR" "Gateway"

echo
echo "[INFO] Done"
echo "[INFO] Output directory: $OUT_DIR"
echo
echo "[INFO] Logs:"
echo "  POP      : $POP_DIR/pop_interval.log"
echo "  1.1.1.1  : $PUBLIC_DIR/pop_interval.log"
echo "  8.8.8.8  : $PUBLIC2_DIR/pop_interval.log"
echo "  Gateway  : $GATEWAY_DIR/pop_interval.log"