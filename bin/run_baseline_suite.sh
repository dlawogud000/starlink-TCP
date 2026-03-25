#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

for run in 1 2 3; do
  bash "$BASE_DIR/bin/run_experiment.sh" tcp cubic downlink "run${run}"
  sleep 30
  bash "$BASE_DIR/bin/run_experiment.sh" tcp bbr downlink "run${run}"
  sleep 30
  UDP_RATE=100M bash "$BASE_DIR/bin/run_experiment.sh" udp none downlink "run${run}"
  sleep 60
done
