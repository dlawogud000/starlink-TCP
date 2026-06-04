#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

END_TS=$(( $(date +%s) + DURATION ))

while [ "$(date +%s)" -lt "$END_TS" ]; do
  NOW="$(date +%s.%N)"
  RESULT="$(curl -o /dev/null -s \
    -w "time_starttransfer=%{time_starttransfer} time_total=%{time_total} http_code=%{http_code}" \
    "$SERVER_HTTP_URL" || true)"
  echo "$NOW $RESULT" >> "$OUT_DIR/http_probe.log"
  sleep "$HTTP_INTERVAL"
done