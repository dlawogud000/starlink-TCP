#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

TMP_ROOT="${BASE_DIR}/tmp"
mkdir -p "$TMP_ROOT"

stop_pidfile() {
  local file="$1"
  local sig="${2:-TERM}"

  if [ -f "$file" ]; then
    local pid=""
    local pgid=""
    pid="$(cat "$file" 2>/dev/null || true)"

    if [ -n "$pid" ]; then
      kill "-$sig" -- "-$pid" 2>/dev/null || true
      sleep 0.2
      kill -KILL -- "-$pid" 2>/dev/null || true

      pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
      if [ -n "$pgid" ]; then
        kill "-$sig" -- "-$pgid" 2>/dev/null || true
        sleep 0.2
        kill -KILL -- "-$pgid" 2>/dev/null || true
      fi
    fi

    rm -f "$file"
  fi
}

stop_pidfile "$TMP_ROOT/app_rtt_receiver.pid" TERM
stop_pidfile "$TMP_ROOT/pop_interval.pid" TERM
stop_pidfile "$TMP_ROOT/pop_ping.pid" TERM
stop_pidfile "$TMP_ROOT/ss.pid" TERM
stop_pidfile "$TMP_ROOT/iface.pid" TERM
stop_pidfile "$TMP_ROOT/tcpdump.pid" INT


sudo pkill -f "tcpdump -i $STARLINK_IFACE" 2>/dev/null || true
sudo pkill -f "pop_interval.sh" 2>/dev/null || true
sudo pkill -f "ping.*$STARLINK_IFACE" 2>/dev/null || true
pkill -f "ss -tin dst $SERVER_IP" 2>/dev/null || true
pkill -f "ip -s link show dev $STARLINK_IFACE" 2>/dev/null || true
pkill -f "tc -s qdisc show dev $STARLINK_IFACE" 2>/dev/null || true
sudo pkill -f "ping -c 1 -W 1 -i 0.01" 2>/dev/null || true
pkill -f "bin/app_layer_rtt/tcp_ping_receiver" 2>/dev/null || true
sudo pkill -9 "iperf3 -B $SERVER_IP" 2>/dev/null || true

stty sane 2>/dev/null || true