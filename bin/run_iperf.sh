#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 4 ]; then
  echo "Usage: $0 <protocol:tcp|udp> <cc:cubic|bbr|none> <direction:downlink|uplink> <out_dir>"
  exit 1
fi

PROTOCOL="$1"
CC="$2"
DIRECTION="$3"
OUT_DIR="$4"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

if [ "$PROTOCOL" = "tcp" ]; then
  sudo sysctl -w net.ipv4.tcp_congestion_control="$CC"
fi

REVERSE_FLAG=""
if [ "$DIRECTION" = "downlink" ]; then
  REVERSE_FLAG="-R"
fi

if [ "$PROTOCOL" = "tcp" ]; then
  iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" $REVERSE_FLAG -t "$DURATION" -i 1 --json \
    > "$OUT_DIR/iperf.json" 2> "$OUT_DIR/iperf.stderr.log"
elif [ "$PROTOCOL" = "udp" ]; then
  UDP_RATE="${UDP_RATE:-100M}"
  iperf3 -B "$LOCAL_IP" -c "$SERVER_IP" -u $REVERSE_FLAG -b "$UDP_RATE" -t "$DURATION" -i 1 --json \
    > "$OUT_DIR/iperf.json" 2> "$OUT_DIR/iperf.stderr.log"
else
  echo "[ERROR] Unsupported protocol: $PROTOCOL"
  exit 1
fi