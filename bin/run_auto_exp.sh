#!/usr/bin/env bash
set -euo pipefail

# ============================================================
# Automated NORMAL vs Auto-RedHAT experiment runner
#
# This version is matched to run_experiment.sh, which creates:
#
#   EXP_ID="${TS}_${PROTOCOL}_${CC}_${DIRECTION}_${FLOWS}flow_${RUN_ID}"
#   OUT_DIR="${BASE_DIR}/${LOG_ROOT}/${EXP_ID}"
#
# The RedHAT monitor must start BEFORE run_experiment.sh so that we can wait for
# ACTIVE_SLEEP.  Therefore monitor files are first written to a staging directory.
# Once run_experiment.sh creates its timestamped OUT_DIR, the automation records
# that directory.  After the monitor is stopped, its completed CSV/stdout files
# are moved into the SAME experiment result directory.
#
# Final result directory example:
#
#   <LOG_ROOT>/<timestamp>_tcp_cubic_downlink_2flow_<run_id>/
#       ...
#       redhat_monitor.csv
#       redhat_monitor.out
#       automation_preflight.txt
#       automation_health.log
#
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

# ------------------------------------------------------------
# Experiment settings
# ------------------------------------------------------------
CC="${CC:-cubic}"
DIRECTION="${DIRECTION:-downlink}"
FLOWS="${FLOWS:-2}"
TRANSPORT="${TRANSPORT:-tcp}"

PAIR_RUNS="${PAIR_RUNS:-3}"
BETWEEN_RUN_SLEEP="${BETWEEN_RUN_SLEEP:-30}"

GATEWAY_IP="${GATEWAY_IP:-192.168.1.1}"
MAX_ATTEMPT="${MAX_ATTEMPT:-5}"

MONITOR_SCRIPT="${MONITOR_SCRIPT:-$BASE_DIR/bin/redhat_monitor/redhat_auto_monitor.py}"
MONITOR_WAIT_TIMEOUT="${MONITOR_WAIT_TIMEOUT:-360}"
MONITOR_READY_PATTERN="${MONITOR_READY_PATTERN:-ACTIVE_SLEEP}"

# run_experiment.sh should create OUT_DIR almost immediately.
RESULT_DIR_WAIT_TIMEOUT="${RESULT_DIR_WAIT_TIMEOUT:-20}"

# 1: random NORMAL/REDHAT order in each pair.
# 0: fixed NORMAL -> REDHAT.
RANDOMIZE_PAIR_ORDER="${RANDOMIZE_PAIR_ORDER:-1}"

# Monitor arguments common to both NORMAL and REDHAT.
MONITOR_EXTRA_ARGS=(
  --interface "$STARLINK_IFACE"
)

# Staging is needed because the timestamped experiment directory does not exist
# until run_experiment.sh starts.
MONITOR_STAGE_ROOT="${MONITOR_STAGE_ROOT:-$BASE_DIR/tmp/redhat_monitor_stage}"
mkdir -p "$MONITOR_STAGE_ROOT" "$BASE_DIR/tmp"

# ------------------------------------------------------------
# Runtime state
# ------------------------------------------------------------
CURRENT_PGID=""
WATCHER_PID=""
MONITOR_PGID=""

MONITOR_CSV=""
MONITOR_STDOUT=""
CURRENT_RESULT_DIR=""

# ------------------------------------------------------------
# Utilities
# ------------------------------------------------------------
timestamp() {
  date --iso-8601=seconds
}

log() {
  echo "[$(timestamp)] $*"
}

require_file() {
  if [[ ! -f "$1" ]]; then
    echo "[ERROR] required file not found: $1" >&2
    exit 2
  fi
}

require_file "$BASE_DIR/config/experiment.conf"
require_file "$BASE_DIR/bin/run_experiment.sh"
require_file "$MONITOR_SCRIPT"

sudo -v

(
  while true; do
    sleep 60
    sudo -n -v || exit
  done
) &
SUDO_KEEPALIVE_PID=$!

# ------------------------------------------------------------
# Process handling
# ------------------------------------------------------------
stop_current_experiment() {
  if [[ -n "${CURRENT_PGID:-}" ]]; then
    log "[STOP] experiment process group=$CURRENT_PGID"
    kill -INT -- "-$CURRENT_PGID" 2>/dev/null || true
    sleep 2
    kill -TERM -- "-$CURRENT_PGID" 2>/dev/null || true
    sleep 2
    kill -KILL -- "-$CURRENT_PGID" 2>/dev/null || true
    CURRENT_PGID=""
  fi
}

stop_monitor() {
  if [[ -n "${MONITOR_PGID:-}" ]]; then
    log "[STOP] monitor process group=$MONITOR_PGID"

    sudo -n kill -INT -- "-$MONITOR_PGID" 2>/dev/null || true
    sleep 1
    sudo -n kill -TERM -- "-$MONITOR_PGID" 2>/dev/null || true
    sleep 1
    sudo -n kill -KILL -- "-$MONITOR_PGID" 2>/dev/null || true

    wait "$MONITOR_PGID" 2>/dev/null || true
    MONITOR_PGID=""
  fi
}

stop_watcher() {
  if [[ -n "${WATCHER_PID:-}" ]]; then
    kill "$WATCHER_PID" 2>/dev/null || true
    wait "$WATCHER_PID" 2>/dev/null || true
    WATCHER_PID=""
  fi
}

# Move monitor files only after the monitor has stopped.  This is robust even if
# LOG_ROOT and tmp happen to be on different filesystems.
move_monitor_artifacts_into_result() {
  local result_dir="${1:-}"

  if [[ -z "$result_dir" || ! -d "$result_dir" ]]; then
    return 0
  fi

  if [[ -n "${MONITOR_CSV:-}" && -f "$MONITOR_CSV" ]]; then
    mv -f "$MONITOR_CSV" "$result_dir/redhat_monitor.csv"
    MONITOR_CSV="$result_dir/redhat_monitor.csv"
  fi

  if [[ -n "${MONITOR_STDOUT:-}" && -f "$MONITOR_STDOUT" ]]; then
    mv -f "$MONITOR_STDOUT" "$result_dir/redhat_monitor.out"
    MONITOR_STDOUT="$result_dir/redhat_monitor.out"
  fi

  log "[ARTIFACT] monitor files -> $result_dir"
}

cleanup_after_run() {
  local result_dir="${1:-$CURRENT_RESULT_DIR}"

  stop_watcher
  stop_current_experiment
  stop_monitor
  move_monitor_artifacts_into_result "$result_dir"

  log "[CLEANUP] reload USB + stop experiment monitors"
  bash "$BASE_DIR/bin/reload_usb.sh" || true
  sleep 5
  bash "$BASE_DIR/bin/stop_monitors.sh" || true
  sleep 10

  CURRENT_RESULT_DIR=""

  if [[ -n "${SUDO_KEEPALIVE_PID:-}" ]]; then
    kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
  fi
}

cleanup_without_reload() {
  local result_dir="${1:-$CURRENT_RESULT_DIR}"

  stop_watcher
  stop_current_experiment
  stop_monitor
  move_monitor_artifacts_into_result "$result_dir"

  log "[CLEANUP] stop experiment monitors"
  bash "$BASE_DIR/bin/stop_monitors.sh" || true
  sleep 10

  CURRENT_RESULT_DIR=""
 
  if [[ -n "${SUDO_KEEPALIVE_PID:-}" ]]; then
    kill "$SUDO_KEEPALIVE_PID" 2>/dev/null || true
  fi
}

on_exit() {
  stop_watcher
  stop_current_experiment
  stop_monitor
  move_monitor_artifacts_into_result "${CURRENT_RESULT_DIR:-}"
}

on_int() {
  echo
  log "[INT] Ctrl+C detected"
  exit 130
}

trap on_exit EXIT
trap on_int INT TERM

# ------------------------------------------------------------
# Network/USB health
# ------------------------------------------------------------
health_check_once() {
  ip link show "$STARLINK_IFACE" | grep -q "LOWER_UP" || return 1

  ip addr show "$STARLINK_IFACE" | grep -q "$LOCAL_IP" || return 1

  ip route get "$SERVER_IP" from "$LOCAL_IP" 2>/dev/null \
    | grep -q "dev $STARLINK_IFACE" || return 1

  ping -I "$STARLINK_IFACE" -c 1 -W 1 "$GATEWAY_IP" >/dev/null 2>&1 || return 1

  if ip neigh show "$GATEWAY_IP" dev "$STARLINK_IFACE" \
      | grep -Eq "FAILED|INCOMPLETE"; then
    return 1
  fi

  return 0
}

abort_current_transport_from_watcher() {
  if [[ -n "${CURRENT_PGID:-}" ]]; then
    kill -INT -- "-$CURRENT_PGID" 2>/dev/null || true
    sleep 2
    kill -TERM -- "-$CURRENT_PGID" 2>/dev/null || true
  fi
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
        echo "$(timestamp) health_check_failed" >> "$log_file"
        echo "health_check_failed" > "$flag_file"
        abort_current_transport_from_watcher
        exit 0
      fi

      if [[ -n "${MONITOR_PGID:-}" ]] \
          && ! sudo -n kill -0 "$MONITOR_PGID" 2>/dev/null; then
        echo "$(timestamp) monitor_died" >> "$log_file"
        echo "monitor_died" > "$flag_file"
        abort_current_transport_from_watcher
        exit 0
      fi

      local dmesg_now_count
      dmesg_now_count="$(sudo dmesg | grep -c "r8152 .*Tx status -71" || true)"

      if [[ "$dmesg_now_count" -gt "$dmesg_start_count" ]]; then
        echo "$(timestamp) r8152_tx_status_71_detected count=$dmesg_now_count start=$dmesg_start_count" \
          >> "$log_file"
        echo "r8152_tx_status_71" > "$flag_file"
        abort_current_transport_from_watcher
        exit 0
      fi
    done
  ) &

  WATCHER_PID=$!
}

# ------------------------------------------------------------
# Monitor
# ------------------------------------------------------------
start_redhat_monitor() {
  local mode="$1"
  local run_id="$2"
  local attempt="$3"

  local stage_dir="$MONITOR_STAGE_ROOT/${run_id}_attempt${attempt}"
  rm -rf "$stage_dir"
  mkdir -p "$stage_dir"

  MONITOR_STDOUT="$stage_dir/redhat_monitor.out"
  MONITOR_CSV="$stage_dir/redhat_monitor.csv"

  local cmd=(
    python3 -u "$MONITOR_SCRIPT"
    "${MONITOR_EXTRA_ARGS[@]}"
    --log "$MONITOR_CSV"
  )

  if [[ "$mode" == "redhat" ]]; then
    cmd+=(--manage-enable)
  fi

  log "[MONITOR] start mode=$mode"
  log "[MONITOR] staging csv=$MONITOR_CSV"

  # setsid outside sudo: $! is the process-group leader used by cleanup.
  local monitor_pidfile="$stage_dir/monitor.pid"
  rm -f "$monitor_pidfile"

  sudo -n setsid bash -c '
      pidfile="$1"
      shift

      echo "$$" > "$pidfile"
      exec "$@"
  ' bash "$monitor_pidfile" "${cmd[@]}" \
      >"$MONITOR_STDOUT" 2>&1 &

  local sudo_launcher_pid=$!

  local waited=0

  while [[ ! -s "$monitor_pidfile" && "$waited" -lt 50 ]]; do
      sleep 0.1
      waited=$((waited + 1))
  done

  if [[ ! -s "$monitor_pidfile" ]]; then
      log "[ERROR] monitor PID file was not created"
      wait "$sudo_launcher_pid" 2>/dev/null || true
      tail -n 100 "$MONITOR_STDOUT" || true
      return 1
  fi

  MONITOR_PGID="$(cat "$monitor_pidfile")"

  sleep 1

  if ! sudo -n kill -0 "$MONITOR_PGID" 2>/dev/null; then
      log "[ERROR] monitor exited immediately"
      tail -n 100 "$MONITOR_STDOUT" || true
      return 1
  fi

  return 0
}

wait_for_monitor_ready() {
  local deadline=$((SECONDS + MONITOR_WAIT_TIMEOUT))

  log "[WAIT] monitor -> $MONITOR_READY_PATTERN"

  while (( SECONDS < deadline )); do
    if [[ -n "${MONITOR_PGID:-}" ]] \
        && ! sudo -n kill -0 "$MONITOR_PGID" 2>/dev/null; then
      log "[ERROR] monitor died before ready"
      tail -n 100 "$MONITOR_STDOUT" || true
      return 1
    fi

    if [[ -f "$MONITOR_CSV" ]] \
        && grep -q "$MONITOR_READY_PATTERN" "$MONITOR_CSV"; then
      log "[READY] monitor reached $MONITOR_READY_PATTERN"
      return 0
    fi

    if [[ -f "$MONITOR_STDOUT" ]] \
        && grep -q "$MONITOR_READY_PATTERN" "$MONITOR_STDOUT"; then
      log "[READY] monitor reached $MONITOR_READY_PATTERN"
      return 0
    fi

    sleep 1
  done

  log "[ERROR] monitor readiness timeout (${MONITOR_WAIT_TIMEOUT}s)"
  tail -n 100 "$MONITOR_STDOUT" || true
  return 1
}

# ------------------------------------------------------------
# Kernel state
# ------------------------------------------------------------
read_sysctl() {
  sysctl -n "$1" 2>/dev/null
}

prepare_normal_kernel() {
  sudo sysctl -w net.ipv4.tcp_leo_rwnd_enable=0 >/dev/null
  sudo sysctl -w net.ipv4.tcp_leo_dynamic_enable=0 >/dev/null
}

validate_kernel_mode() {
  local mode="$1"

  local rwnd_enable dynamic_enable period_ms offset_ms
  rwnd_enable="$(read_sysctl net.ipv4.tcp_leo_rwnd_enable)"
  dynamic_enable="$(read_sysctl net.ipv4.tcp_leo_dynamic_enable)"
  period_ms="$(read_sysctl net.ipv4.tcp_leo_rwnd_period_ms)"
  offset_ms="$(read_sysctl net.ipv4.tcp_leo_rwnd_offset_ms)"

  log "[KERNEL] mode=$mode enable=$rwnd_enable dynamic=$dynamic_enable period_ms=$period_ms offset_ms=$offset_ms"

  case "$mode" in
    redhat)
      [[ "$rwnd_enable" == "1" && "$dynamic_enable" == "1" ]]
      ;;
    normal)
      [[ "$rwnd_enable" == "0" && "$dynamic_enable" == "0" ]]
      ;;
    *)
      return 1
      ;;
  esac
}

# ------------------------------------------------------------
# Locate the exact timestamped OUT_DIR created by run_experiment.sh
# ------------------------------------------------------------
wait_for_result_dir() {
  local run_id="$1"
  local launch_epoch="$2"
  local deadline=$((SECONDS + RESULT_DIR_WAIT_TIMEOUT))
  local log_base="$BASE_DIR/$LOG_ROOT"
  local suffix="${TRANSPORT}_${CC}_${DIRECTION}_${FLOWS}flow_${run_id}"

  while (( SECONDS < deadline )); do
    local matches=()
    local d
    local newest=""
    local newest_mtime=0

    shopt -s nullglob
    matches=("$log_base"/*_"$suffix")
    shopt -u nullglob

    for d in "${matches[@]}"; do
      [[ -d "$d" ]] || continue

      local mt
      mt="$(stat -c %Y "$d" 2>/dev/null || echo 0)"

      # Ignore result directories from previous attempts/runs.
      if (( mt < launch_epoch )); then
        continue
      fi

      if (( mt >= newest_mtime )); then
        newest="$d"
        newest_mtime="$mt"
      fi
    done

    if [[ -n "$newest" ]]; then
      printf '%s\n' "$newest"
      return 0
    fi

    sleep 0.2
  done

  return 1
}

write_preflight() {
  local mode="$1"
  local run_id="$2"
  local result_dir="$3"

  {
    echo "run_id=$run_id"
    echo "mode=$mode"
    echo "timestamp=$(timestamp)"
    echo "rwnd_enable=$(read_sysctl net.ipv4.tcp_leo_rwnd_enable)"
    echo "dynamic_enable=$(read_sysctl net.ipv4.tcp_leo_dynamic_enable)"
    echo "period_ms=$(read_sysctl net.ipv4.tcp_leo_rwnd_period_ms)"
    echo "offset_ms=$(read_sysctl net.ipv4.tcp_leo_rwnd_offset_ms)"
    echo "monitor_staging_csv=$MONITOR_CSV"
    echo "result_dir=$result_dir"
  } > "$result_dir/automation_preflight.txt"
}

# ------------------------------------------------------------
# One complete run
# ------------------------------------------------------------
run_one() {
  local mode="$1"       # normal | redhat
  local pair_no="$2"
  local attempt=1

  while (( attempt <= MAX_ATTEMPT )); do
    local run_id="${mode}_${pair_no}"
    local flag_file="$BASE_DIR/tmp/health_fail_${run_id}.flag"

    CURRENT_RESULT_DIR=""
    rm -f "$flag_file"

    log "============================================================"
    log "[RUN] id=$run_id mode=$mode attempt=$attempt/$MAX_ATTEMPT"
    log "============================================================"

    # Every condition begins from known OFF state.
    prepare_normal_kernel

    if ! health_check_once; then
      log "[WARN] pre-run health check failed"
      cleanup_after_run ""
      attempt=$((attempt + 1))
      continue
    fi

    if ! start_redhat_monitor "$mode" "$run_id" "$attempt"; then
      log "[WARN] monitor startup failed"
      cleanup_after_run ""
      attempt=$((attempt + 1))
      continue
    fi

    if ! wait_for_monitor_ready; then
      log "[WARN] monitor did not reach stable ACTIVE_SLEEP"
      cleanup_after_run ""
      attempt=$((attempt + 1))
      continue
    fi

    if ! validate_kernel_mode "$mode"; then
      log "[WARN] kernel state does not match requested mode=$mode"
      cleanup_after_run ""
      attempt=$((attempt + 1))
      continue
    fi

    # run_experiment.sh creates its timestamped OUT_DIR here.
    local launch_epoch
    launch_epoch="$(date +%s)"

    log "[EXPERIMENT] starting run_experiment.sh"

    setsid bash "$BASE_DIR/bin/run_experiment.sh" \
      "$TRANSPORT" "$CC" "$DIRECTION" "$FLOWS" "$run_id" &
    CURRENT_PGID="$!"

    # Find the exact OUT_DIR just created by run_experiment.sh.
    local result_dir
    if ! result_dir="$(wait_for_result_dir "$run_id" "$launch_epoch")"; then
      log "[ERROR] could not locate run_experiment OUT_DIR"
      stop_current_experiment
      cleanup_after_run ""
      attempt=$((attempt + 1))
      continue
    fi

    CURRENT_RESULT_DIR="$result_dir"

    log "[RESULT] $CURRENT_RESULT_DIR"

    # Automation metadata can now go directly into the real experiment folder.
    local health_log="$CURRENT_RESULT_DIR/automation_health.log"
    : > "$health_log"
    write_preflight "$mode" "$run_id" "$CURRENT_RESULT_DIR"

    start_health_watcher "$flag_file" "$health_log"

    local status=0
    wait "$CURRENT_PGID" || status=$?

    stop_watcher
    CURRENT_PGID=""

    if [[ -f "$flag_file" ]]; then
      log "[WARN] run failed by watcher: $(cat "$flag_file")"
      cleanup_after_run "$CURRENT_RESULT_DIR"
      attempt=$((attempt + 1))
      continue
    fi

    if [[ "$status" -eq 0 ]]; then
      log "[OK] $run_id"
      cleanup_without_reload "$CURRENT_RESULT_DIR"
      return 0
    fi

    log "[WARN] run_experiment exited status=$status"
    cleanup_after_run "$CURRENT_RESULT_DIR"
    attempt=$((attempt + 1))
  done

  log "[ERROR] mode=$mode pair=$pair_no failed after $MAX_ATTEMPT attempts"
  return 1
}

# ------------------------------------------------------------
# Experiment plan
# ------------------------------------------------------------
sudo sysctl -w net.ipv4.tcp_congestion_control="$CC"

for ((pair=1; pair<=PAIR_RUNS; pair++)); do
  if [[ "$RANDOMIZE_PAIR_ORDER" == "1" ]] && (( RANDOM % 2 )); then
    ORDER=(normal redhat)
  elif [[ "$RANDOMIZE_PAIR_ORDER" == "1" ]]; then
    ORDER=(redhat normal)
  else
    ORDER=(normal redhat)
  fi

  log "[PAIR] $pair/$PAIR_RUNS order=${ORDER[*]}"

  for mode in "${ORDER[@]}"; do
    run_one "$mode" "$pair"

    log "[SLEEP] ${BETWEEN_RUN_SLEEP}s before next condition"
    sleep "$BETWEEN_RUN_SLEEP"
  done
done

log "[DONE] all NORMAL/REDHAT runs completed"
