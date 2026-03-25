#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="${1:-}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

for name in ping tcpdump ss iface; do
  PID_FILE="$TMP_ROOT/${name}.pid"
  if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE")"
    kill "$PID" 2>/dev/null || true
    rm -f "$PID_FILE"
  fi
done

sleep 1
sudo pkill -f "tcpdump -i $UE_IFACE" 2>/dev/null || true