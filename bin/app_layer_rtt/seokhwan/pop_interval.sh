#!/usr/bin/env bash
set -euo pipefail

POP_IP="$1"
OUT_FILE="$2"

if [ -z "$POP_IP" ] || [ -z "$OUT_FILE" ]; then
  echo "Usage: $0 <pop_ip> <out_file>"
  exit 1
fi

prev_ts=""

sudo ping -i 0.01 -W 1 "$POP_IP" | while read -r line; do
  ts=$(date +%s.%N)

  if echo "$line" | grep -q "time="; then
    if [ -n "$prev_ts" ]; then
      interval=$(awk -v t1="$ts" -v t0="$prev_ts" 'BEGIN {print t1 - t0}')
      echo "$ts $interval"
    fi
    prev_ts="$ts"
  fi
done >> "$OUT_FILE"