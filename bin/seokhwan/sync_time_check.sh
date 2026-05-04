#!/usr/bin/env bash
set -euo pipefail

echo "===== date ====="
date --iso-8601=ns || true

echo "===== timedatectl ====="
timedatectl || true

echo "===== chronyc tracking ====="
chronyc tracking || true

echo "===== chronyc sources ====="
chronyc sources || true