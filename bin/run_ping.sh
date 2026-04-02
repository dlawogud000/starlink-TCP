#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

TCPINFO_LOG="$OUT_DIR/ss_tcpinfo.log"
RTT_LOG="$OUT_DIR/ss_rtt.log"

# ss_tcpinfo.log가 생성될 때까지 잠시 대기
while [ ! -f "$TCPINFO_LOG" ]; do
  sleep 0.1
done

# 새로 추가되는 내용만 따라가며 parsing
tail -n 0 -F "$TCPINFO_LOG" 2>/dev/null | awk '
  function reset_best() {
    best_found = 0
    best_bytes_sent = -1
    best_data_segs_out = -1
    best_sendq = -1
    best_unacked = -1

    best_rtt = ""
    best_rttvar = ""
    best_cwnd = ""
    best_bytes = ""
    best_retrans = ""
    best_conn = ""
  }

  function flush_block() {
    if (ts == "")
      return

    if (best_found) {
      print ts, \
            "rtt_ms=" best_rtt, \
            "rttvar_ms=" best_rttvar, \
            "cwnd=" best_cwnd, \
            "bytes_sent=" best_bytes, \
            "retrans=" best_retrans, \
            "conn=\"" best_conn "\""
    } else {
      print ts, "no_data_connection"
    }
    fflush()
  }

  BEGIN {
    ts = ""
    conn_state = ""
    conn_sendq = -1
    conn_line = ""
    reset_best()
  }

  /^[0-9]+\.[0-9]+$/ {
    flush_block()
    ts = $0
    conn_state = ""
    conn_sendq = -1
    conn_line = ""
    reset_best()
    next
  }

  /^State[[:space:]]/ {
    next
  }

  /^[A-Z0-9-]+[[:space:]]+[0-9]+[[:space:]]+[0-9]+[[:space:]]+/ {
    conn_state = $1
    conn_sendq = $3 + 0
    conn_line = $0
    next
  }

  conn_state == "ESTAB" && /(^|[[:space:]])rtt:[0-9.]+\/[0-9.]+/ {
    line = $0

    rtt = ""
    rttvar = ""
    cwnd = ""
    bytes_sent = -1
    data_segs_out = -1
    unacked = -1
    retrans = ""

    # rtt:rttvar만 추출. minrtt와 구분
    if (match(line, /(^|[[:space:]])rtt:[0-9.]+\/[0-9.]+/)) {
      tmp = substr(line, RSTART, RLENGTH)
      sub(/^[[:space:]]*rtt:/, "", tmp)
      sub(/^[[:space:]]+/, "", tmp)
      split(tmp, a, "/")
      rtt = a[1]
      rttvar = a[2]
    }

    if (match(line, /(^|[[:space:]])cwnd:[0-9]+/)) {
      tmp = substr(line, RSTART, RLENGTH)
      sub(/^[[:space:]]*cwnd:/, "", tmp)
      sub(/^[[:space:]]+/, "", tmp)
      cwnd = tmp
    }

    if (match(line, /(^|[[:space:]])bytes_sent:[0-9]+/)) {
      tmp = substr(line, RSTART, RLENGTH)
      sub(/^[[:space:]]*bytes_sent:/, "", tmp)
      sub(/^[[:space:]]+/, "", tmp)
      bytes_sent = tmp + 0
    }

    if (match(line, /(^|[[:space:]])data_segs_out:[0-9]+/)) {
      tmp = substr(line, RSTART, RLENGTH)
      sub(/^[[:space:]]*data_segs_out:/, "", tmp)
      sub(/^[[:space:]]+/, "", tmp)
      data_segs_out = tmp + 0
    }

    if (match(line, /(^|[[:space:]])unacked:[0-9]+/)) {
      tmp = substr(line, RSTART, RLENGTH)
      sub(/^[[:space:]]*unacked:/, "", tmp)
      sub(/^[[:space:]]+/, "", tmp)
      unacked = tmp + 0
    }

    if (match(line, /(^|[[:space:]])retrans:[^[:space:]]+/)) {
      tmp = substr(line, RSTART, RLENGTH)
      sub(/^[[:space:]]*retrans:/, "", tmp)
      sub(/^[[:space:]]+/, "", tmp)
      retrans = tmp
    }

    # data flow 선택 규칙
    # 1) bytes_sent 큰 것
    # 2) 동률이면 data_segs_out 큰 것
    # 3) 동률이면 Send-Q 큰 것
    # 4) 동률이면 unacked 큰 것
    if (!best_found ||
        bytes_sent > best_bytes_sent ||
        (bytes_sent == best_bytes_sent && data_segs_out > best_data_segs_out) ||
        (bytes_sent == best_bytes_sent && data_segs_out == best_data_segs_out && conn_sendq > best_sendq) ||
        (bytes_sent == best_bytes_sent && data_segs_out == best_data_segs_out && conn_sendq == best_sendq && unacked > best_unacked)) {

      best_found = 1
      best_bytes_sent = bytes_sent
      best_data_segs_out = data_segs_out
      best_sendq = conn_sendq
      best_unacked = unacked

      best_rtt = rtt
      best_rttvar = rttvar
      best_cwnd = cwnd
      best_bytes = bytes_sent
      best_retrans = retrans
      best_conn = conn_line
    }

    conn_state = ""
    conn_sendq = -1
    conn_line = ""
    next
  }

  END {
    flush_block()
  }
' >> "$RTT_LOG"