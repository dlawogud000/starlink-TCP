#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

sudo sysctl -w net.ipv4.tcp_congestion_control=bbr
sudo sysctl -w net.ipv4.tcp_leo_rwnd_pre_ms=200

for run in 3 4 5 6 7 8; do
  bash "$BASE_DIR/bin/run_experiment.sh" tcp bbr downlink 2 "rc_offset_45_pre_200_out_20_${run}"

  sleep 10
done
