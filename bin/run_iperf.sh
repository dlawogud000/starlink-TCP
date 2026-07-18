#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 5 ]; then
  echo "Usage: $0 <protocol:tcp|udp> <cc:cubic|bbr|none> <direction:downlink|uplink> <flows> <out_dir>"
  exit 1
fi

PROTOCOL="$1"
CC="$2"
DIRECTION="$3"
FLOWS="$4"
OUT_DIR="$5"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

mkdir -p "$OUT_DIR"

normalize_ports() {
  local s="${1:-}"
  s="${s//,/ }"
  for p in $s; do
    [ -n "$p" ] && echo "$p"
  done
}

IPERF_PORTS=()
while read -r p; do
  IPERF_PORTS+=("$p")
done < <(normalize_ports "${SERVER_PORTS:-$SERVER_PORT}")

if [ "${#IPERF_PORTS[@]}" -eq 0 ]; then
  IPERF_PORTS=("$SERVER_PORT")
fi

if ! [[ "$FLOWS" =~ ^[0-9]+$ ]] || [ "$FLOWS" -le 0 ]; then
  echo "[ERROR] flows must be a positive integer: $FLOWS"
  exit 1
fi

if [ "$FLOWS" -gt "${#IPERF_PORTS[@]}" ]; then
  echo "[ERROR] requested flows=$FLOWS but only ${#IPERF_PORTS[@]} ports are configured: ${IPERF_PORTS[*]}"
  echo "        Set SERVER_PORTS in config/experiment.conf, e.g. SERVER_PORTS=\"20075,20076,20077,20078\""
  exit 1
fi

# if [ "$PROTOCOL" = "tcp" ]; then
#   sudo sysctl -w net.ipv4.tcp_congestion_control="$CC" > /dev/null 2>&1
# fi

REVERSE_FLAG=""
if [ "$DIRECTION" = "downlink" ]; then
  REVERSE_FLAG="-R"
fi

: > "$OUT_DIR/iperf_ports.txt"
PIDS=()

for idx in $(seq 0 $((FLOWS - 1))); do
  PORT="${IPERF_PORTS[$idx]}"
  echo "$PORT" >> "$OUT_DIR/iperf_ports.txt"

  if [ "$PROTOCOL" = "tcp" ]; then
    echo "[INFO] Starting TCP iperf flow on port $PORT"
    iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" -p "$PORT" $REVERSE_FLAG -4 \
      -t "$DURATION" -i "$IPERF_INTERVAL" --json \
      > "$OUT_DIR/iperf_${PORT}.json" \
      2> "$OUT_DIR/iperf_${PORT}.stderr.log" &
    PIDS+=("$!")
  elif [ "$PROTOCOL" = "udp" ]; then
    UDP_RATE="${UDP_RATE}"
    echo "[INFO] Starting UDP iperf flow on port $PORT rate=$UDP_RATE"
    iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" -p "$PORT" -u $REVERSE_FLAG -4 \
      -b "$UDP_RATE" -t "$DURATION" -i "$IPERF_INTERVAL" --json --get-server-output \
      > "$OUT_DIR/iperf_${PORT}.json" \
      2> "$OUT_DIR/iperf_${PORT}.stderr.log" &
    PIDS+=("$!")
  else
    echo "[ERROR] Unsupported protocol: $PROTOCOL"
    exit 1
  fi
done

FAIL=0
for pid in "${PIDS[@]}"; do
  if ! wait "$pid"; then
    FAIL=1
  fi
done

# Backward compatibility for old graph scripts in the one-flow case.
if [ "$FLOWS" -eq 1 ]; then
  FIRST_PORT="${IPERF_PORTS[0]}"
  [ -f "$OUT_DIR/iperf_${FIRST_PORT}.json" ] && cp -f "$OUT_DIR/iperf_${FIRST_PORT}.json" "$OUT_DIR/iperf.json"
  [ -f "$OUT_DIR/iperf_${FIRST_PORT}.stderr.log" ] && cp -f "$OUT_DIR/iperf_${FIRST_PORT}.stderr.log" "$OUT_DIR/iperf.stderr.log"
fi

exit "$FAIL"