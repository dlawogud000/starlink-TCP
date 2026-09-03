#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 5 ]; then
  echo "Usage: $0 <protocol:tcp|udp|http> <cc:CUBIC|BBR|None> <direction:downlink|uplink> <flows> <run_id>"
  exit 1
fi

PROTOCOL="$1"
CC="$2"
DIRECTION="$3"
FLOWS="$4"
RUN_ID="$5"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

TS="$(date +%Y%m%d_%H%M%S)"
EXP_ID="${TS}_${PROTOCOL}_${CC}_${DIRECTION}_${FLOWS}flow_${RUN_ID}"
OUT_DIR="${BASE_DIR}/${LOG_ROOT}/${EXP_ID}"
TMP_ROOT="${BASE_DIR}/tmp"

mkdir -p "$OUT_DIR"
mkdir -p "$TMP_ROOT"

echo "[INFO] Experiment ID: $EXP_ID"

echo "$(date +%s.%N)" > "$OUT_DIR/client_start_time_epoch.txt"

"$BASE_DIR/bin/collect_meta.sh" "$PROTOCOL" "$CC" "$DIRECTION" "$FLOWS" "$RUN_ID" "$OUT_DIR"
"$BASE_DIR/bin/sync_time_check.sh" > "$OUT_DIR/time_sync.txt" 2>&1 || true

"$BASE_DIR/bin/start_monitors.sh" "$OUT_DIR" "$DIRECTION"

# start_app_rtt_receiver() {
#   if [ "${PROTOCOL}" != "tcp" ]; then
#     return 0
#   fi

#   local port="${APP_RTT_PORT:-}"
#   if [ -z "$port" ]; then
#     echo "[INFO] APP_RTT_PORT is not set. Skipping app-level RTT receiver."
#     return 0
#   fi

#   local rtt_bin="$BASE_DIR/bin/app_layer_rtt/udp_ping_receiver"
#   if [ ! -x "$rtt_bin" ]; then
#     echo "[WARN] app-level RTT receiver not executable or not found: $rtt_bin"
#     echo "       Build it first, e.g. gcc -O2 -Wall -Wextra udp_ping_receiver.c -o bin/app_layer_rtt/udp_ping_receiver"
#     return 0
#   fi

#   echo "[INFO] Starting app-level RTT receiver: server=$SERVER_IP port=$port bind=$LOCAL_IP"
#   setsid "$rtt_bin" "$SERVER_IP" "$port" "$LOCAL_IP" \
#     > "$OUT_DIR/app_rtt_receiver.stdout.log" \
#     2> "$OUT_DIR/app_rtt_receiver.stderr.log" &
#   echo $! > "$TMP_ROOT/app_rtt_receiver.pid"

#   # Give the TCP connection a short chance to reach the server-side RTT sender before iperf starts.
#   sleep "${APP_RTT_WARMUP_SEC:-0.3}"
# }

# stop_app_rtt_receiver() {
#   if [ -f "$TMP_ROOT/app_rtt_receiver.pid" ]; then
#     local pid
#     pid="$(cat "$TMP_ROOT/app_rtt_receiver.pid" 2>/dev/null || true)"
#     if [ -n "$pid" ]; then
#       kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
#       sleep 0.2
#       kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
#     fi
#     rm -f "$TMP_ROOT/app_rtt_receiver.pid"
#   fi
# }

cleanup() {
  # stop_app_rtt_receiver || true
  "$BASE_DIR/bin/stop_monitors.sh" "$OUT_DIR" || true
}

normalize_ports_for_plot() {
  local s="${1:-}"
  s="${s//,/ }"
  for p in $s; do
    [ -n "$p" ] && echo "$p"
  done
}

write_used_iperf_ports_for_plot() {
  # graph/iperf.py should read only the ports used in this run.
  # This avoids reading stale/compatibility iperf.json together with iperf_<port>.json.
  local ports_file="$OUT_DIR/iperf_ports.txt"

  if [ -s "$ports_file" ]; then
    return 0
  fi

  local count=0
  : > "$ports_file"
  while read -r p; do
    [ -z "$p" ] && continue
    if [ "$count" -lt "$FLOWS" ]; then
      echo "$p" >> "$ports_file"
      count=$((count + 1))
    fi
  done < <(normalize_ports_for_plot "${SERVER_PORTS:-$SERVER_PORT}")
}

plot_graphs() {
  echo "[INFO] Generating plots..."

  if [ "$PROTOCOL" = "tcp" ] || [ "$PROTOCOL" = "udp" ]; then
    write_used_iperf_ports_for_plot

    # Multiport-aware graph/iperf.py reads iperf_ports.txt and all corresponding
    # iperf_<port>.json files, then generates:
    #   iperf3.png             aggregate throughput across active ports
    #   iperf3_flows.png       per-port/per-flow throughput
    #   iperf3_aggregate.csv   aggregate CSV
    #   iperf3_flows.csv       per-flow CSV
    # Do not create temporary iperf.json or call this once per port, because that
    # can duplicate the same flow when iperf.py is already multiport-aware.
    python3 "$BASE_DIR/graph/iperf.py" "$OUT_DIR"       > "$OUT_DIR/plot_iperf.stdout.log" 2>&1 || true
  fi

  if [ "$PROTOCOL" = "tcp" ]; then
    python3 "$BASE_DIR/graph/tcpinfo.py" "$OUT_DIR"       > "$OUT_DIR/plot_tcpinfo.stdout.log" 2>&1 || true
  fi

  python3 "$BASE_DIR/graph/pop_ping_interval.py" "$OUT_DIR"     > "$OUT_DIR/plot_pop_interval.stdout.log" 2>&1 || true

  python3 "$BASE_DIR/graph/pop_ping.py" "$OUT_DIR"     > "$OUT_DIR/pop_ping.stdout.log" 2>&1 || true
}

trap cleanup EXIT

# start_app_rtt_receiver

if [ "$PROTOCOL" = "tcp" ] || [ "$PROTOCOL" = "udp" ]; then
  "$BASE_DIR/bin/run_iperf.sh" "$PROTOCOL" "$CC" "$DIRECTION" "$FLOWS" "$OUT_DIR"
elif [ "$PROTOCOL" = "http" ]; then
  "$BASE_DIR/bin/run_http_probe.sh" "$OUT_DIR"
else
  echo "[ERROR] Unknown protocol: $PROTOCOL"
  exit 1
fi

cleanup
trap - EXIT

plot_graphs

echo "[INFO] Experiment completed: $EXP_ID"