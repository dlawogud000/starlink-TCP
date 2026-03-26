#!/usr/bin/env bash
set -euo pipefail

PROTOCOL="$1"
CC="$2"
DIRECTION="$3"
RUN_ID="$4"
OUT_DIR="$5"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

if [ "$PROTOCOL" = "tcp" ]; then
  sudo sysctl -w net.ipv4.tcp_congestion_control="$CC"
fi

cat > "$OUT_DIR/meta.txt" <<EOF
timestamp=$(date --iso-8601=seconds)
protocol=$PROTOCOL
cc=$CC
direction=$DIRECTION
run_id=$RUN_ID
server_ip=$SERVER_IP
server_http_url=$SERVER_HTTP_URL
ue_iface=$UE_IFACE
duration=$DURATION
kernel=$(uname -a)
tcp_cc_current=$(sysctl -n net.ipv4.tcp_congestion_control 2>/dev/null || true)
tcp_available=$(sysctl -n net.ipv4.tcp_available_congestion_control 2>/dev/null || true)
host=$(hostname)
EOF