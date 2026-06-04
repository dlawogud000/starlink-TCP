#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="$1"
DIRECTION="$2"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

TMP_ROOT="${BASE_DIR}/tmp"

mkdir -p "$OUT_DIR"
mkdir -p "$TMP_ROOT"

# tcpdump
nohup sudo tcpdump -i "$STARLINK_IFACE" -s "$TCPDUMP_SNAPLEN" -w "$OUT_DIR/ue_tcpdump.pcap" host "$SERVER_IP" \
  > "$OUT_DIR/tcpdump_stdout.log" 2>&1 &
echo $! > "$TMP_ROOT/tcpdump.pid"

# ss / tcpinfo
if [ "$DIRECTION" = "downlink" ]; then
  SS_FILTER="src $SERVER_IP"
else
  SS_FILTER="dst $SERVER_IP"
fi

nohup sudo bash -c "
while true; do
  date +%s.%N
  ss -tin $SS_FILTER || true
  sleep $SS_INTERVAL
done
" > "$OUT_DIR/ss_tcpinfo.log" 2>&1 &
echo $! > "$TMP_ROOT/ss.pid"

# ping
nohup bash "$BASE_DIR/bin/run_ping.sh" "$OUT_DIR" > "$OUT_DIR/ping_stdout.log" 2>&1 &
echo $! > "$TMP_ROOT/ping.pid"

# interface stats
nohup bash -c "
while true; do
  date +%s.%N
  ip -s link show dev $STARLINK_IFACE || true
  tc -s qdisc show dev $STARLINK_IFACE || true
  sleep $IFACE_INTERVAL
done
" > "$OUT_DIR/iface_stats.log" 2>&1 &
echo $! > "$TMP_ROOT/iface.pid"

# POP ping disabled
echo "[WARN] POP ping disabled for this experiment" | tee "$OUT_DIR/pop_ip.txt"