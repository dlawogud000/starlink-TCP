#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

GATEWAY_IP="${GATEWAY_IP:-192.168.1.1}"
MAX_ATTEMPT="${MAX_ATTEMPT:-5}"

CURRENT_PGID=""
WATCHER_PID=""
FAIL_FLAG=""

cleanup_after_run() {
  echo "[CLEANUP] reload usb + stop monitors"
  bash "$BASE_DIR/bin/reload_usb.sh" || true
  sleep 5
  bash "$BASE_DIR/bin/stop_monitors.sh" || true
  sleep 10
}

cleanup_without_reload() {
  echo "[CLEANUP] stop monitors"
  bash "$BASE_DIR/bin/stop_monitors.sh" || true
  sleep 10
}

stop_current_experiment() {
  if [ -n "${CURRENT_PGID:-}" ]; then
    echo "[STOP] killing experiment process group: $CURRENT_PGID"
    kill -INT -- "-$CURRENT_PGID" 2>/dev/null || true
    sleep 2
    kill -TERM -- "-$CURRENT_PGID" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$CURRENT_PGID" 2>/dev/null || true
  fi
}

stop_watcher() {
  if [ -n "${WATCHER_PID:-}" ]; then
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
    WATCHER_PID=""
  fi
}

health_check_once() {
  # 1. 인터페이스가 UP/LOWER_UP인지 확인
  ip link show "$STARLINK_IFACE" | grep -q "LOWER_UP" || return 1

  # 2. IP가 붙어 있는지 확인
  ip addr show "$STARLINK_IFACE" | grep -q "$LOCAL_IP" || return 1

  # 3. policy routing이 원하는 인터페이스를 타는지 확인
  ip route get "$SERVER_IP" from "$LOCAL_IP" 2>/dev/null | grep -q "dev $STARLINK_IFACE" || return 1

  # 4. 게이트웨이 ARP/ICMP 확인
  ping -I "$STARLINK_IFACE" -c 1 -W 1 "$GATEWAY_IP" >/dev/null 2>&1 || return 1

  # 5. neighbor 상태 확인
  if ip neigh show "$GATEWAY_IP" dev "$STARLINK_IFACE" | grep -Eq "FAILED|INCOMPLETE"; then
    return 1
  fi

  return 0
}

start_health_watcher() {
  local flag_file="$1"
  local log_file="$2"
  local dmesg_start_count

  dmesg_start_count="$(sudo dmesg | grep -c "r8152 .*Tx status -71" || true)"

  (
    while true; do
      sleep 5

      if ! health_check_once; then
        echo "$(date --iso-8601=seconds) health_check_failed" >> "$log_file"
        echo "health_check_failed" > "$flag_file"
        stop_current_experiment
        exit 0
      fi

      dmesg_now_count="$(sudo dmesg | grep -c "r8152 .*Tx status -71" || true)"
      if [ "$dmesg_now_count" -gt "$dmesg_start_count" ]; then
        echo "$(date --iso-8601=seconds) r8152_tx_status_71_detected count=$dmesg_now_count start=$dmesg_start_count" >> "$log_file"
        echo "r8152_tx_status_71" > "$flag_file"
        stop_current_experiment
        exit 0
      fi
    done
  ) &

  WATCHER_PID=$!
}

run_one() {
  local cc="$1"
  local tag="$2"
  local run="$3"
  local attempt=1

  while [ "$attempt" -le "$MAX_ATTEMPT" ]; do
    local run_id="${tag}_${run}"
    local flag_file="$BASE_DIR/tmp/health_fail_${run_id}.flag"
    local health_log="$BASE_DIR/tmp/health_${run_id}.log"

    rm -f "$flag_file"

    echo "[RUN] $run_id attempt=$attempt"

    if ! health_check_once; then
      echo "[WARN] pre-run health check failed. recovering..."
      cleanup_after_run
      attempt=$((attempt + 1))
      continue
    fi

    #run setup
    setsid bash "$BASE_DIR/bin/run_experiment.sh" \
      tcp "$cc" downlink 1 "$run_id" &
    CURRENT_PGID="$!"

    start_health_watcher "$flag_file" "$health_log"

    STATUS=0
    wait "$CURRENT_PGID" || STATUS=$?

    stop_watcher
    CURRENT_PGID=""

    if [ -f "$flag_file" ]; then
      echo "[WARN] $run_id failed by health watcher: $(cat "$flag_file")"
      cleanup_after_run
      attempt=$((attempt + 1))
      continue
    fi

    if [ "$STATUS" -eq 0 ]; then
      echo "[OK] $run_id"
      cleanup_without_reload
      return 0
    fi

    echo "[WARN] $run_id exited with status=$STATUS"
    cleanup_after_run
    attempt=$((attempt + 1))
  done

  echo "[ERROR] ${tag}_${run} failed after $MAX_ATTEMPT attempts"
  return 1
}

on_int() {
  echo
  echo "[INT] Ctrl+C detected. stopping current experiment..."
  stop_current_experiment
}

trap on_int INT

sudo sysctl -w net.ipv4.tcp_congestion_control=cubic
sudo sysctl -w net.ipv4.tcp_leo_rwnd_enable=1
sudo sysctl -w net.ipv4.tcp_leo_dynamic_enable=1
for run in 1; do
  run_one "cubic" "redhat_ec2" "$run"
  sleep 60
done

# for pre in 150; do
#   sudo sysctl -w net.ipv4.tcp_leo_rwnd_enable=1
#   sudo sysctl -w net.ipv4.tcp_shrink_window=1
#   sudo sysctl -w net.ipv4.tcp_leo_rwnd_pre_ms="$pre"

#   for run in 1 2 3 4 5 6 7 8; do
#     run_one "cubic" "rc_45_${pre}_20" "$run"
#     sleep 60
#   done

#   sleep 10
# done

# sudo sysctl -w net.ipv4.tcp_congestion_control=bbr
# sudo sysctl -w net.ipv4.tcp_leo_rwnd_enable=0
# sudo sysctl -w net.ipv4.tcp_leo_dynamic_enable=0

# for run in 1; do
#   run_one "cubic" "normal_ec2" "$run"
#   sleep 60
# done