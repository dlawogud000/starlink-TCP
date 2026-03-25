#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

ping "$SERVER_IP" -i "$PING_INTERVAL" -D > "$OUT_DIR/ping.log"