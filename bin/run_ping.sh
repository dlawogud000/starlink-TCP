#!/usr/bin/env bash
set -euo pipefail

OUT_DIR="$1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
source "$BASE_DIR/config/experiment.conf"

END_TS=$(( $(date +%s) + DURATION ))

while [ "$(date +%s)" -lt "$END_TS" ]; do
  NOW="$(date +%s.%N)"

  ss -tin dst "$SERVER_IP:$SERVER_PORT" 2>/dev/null \
    | awk -v now="$NOW" '
        BEGIN {
          best_bytes_sent = -1
          best_rtt = ""
          best_rttvar = ""
          best_cwnd = ""
          best_retrans = ""
          best_conn = ""
          found = 0
          conn = ""
        }

        /^ESTAB/ {
          conn = $0
          found = 1
          next
        }

        found && /rtt:/ {
          line = $0

          rtt = ""
          rttvar = ""
          cwnd = ""
          retrans = ""
          bytes_sent = -1
          data_segs_out = -1

          if (line ~ /rtt:[0-9.]+\/[0-9.]+/) {
            tmp = line
            sub(/.*rtt:/, "", tmp)
            sub(/ .*/, "", tmp)
            split(tmp, a, "/")
            rtt = a[1]
            rttvar = a[2]
          }

          if (line ~ /cwnd:[0-9]+/) {
            tmp = line
            sub(/.*cwnd:/, "", tmp)
            sub(/ .*/, "", tmp)
            cwnd = tmp
          }

          if (line ~ /retrans:[^ ]+/) {
            tmp = line
            sub(/.*retrans:/, "", tmp)
            sub(/ .*/, "", tmp)
            retrans = tmp
          }

          if (line ~ /bytes_sent:[0-9]+/) {
            tmp = line
            sub(/.*bytes_sent:/, "", tmp)
            sub(/ .*/, "", tmp)
            bytes_sent = tmp + 0
          }

          if (line ~ /data_segs_out:[0-9]+/) {
            tmp = line
            sub(/.*data_segs_out:/, "", tmp)
            sub(/ .*/, "", tmp)
            data_segs_out = tmp + 0
          }

          # data connection 선택 기준:
          # 1) bytes_sent가 가장 큰 연결
          # 2) 동률이면 data_segs_out가 큰 연결
          if (bytes_sent > best_bytes_sent ||
              (bytes_sent == best_bytes_sent && data_segs_out > best_data_segs_out)) {
            best_bytes_sent = bytes_sent
            best_data_segs_out = data_segs_out
            best_rtt = rtt
            best_rttvar = rttvar
            best_cwnd = cwnd
            best_retrans = retrans
            best_conn = conn
          }

          found = 0
        }

        END {
          if (best_bytes_sent >= 0) {
            print now, \
                  "rtt_ms=" best_rtt, \
                  "rttvar_ms=" best_rttvar, \
                  "cwnd=" best_cwnd, \
                  "retrans=" best_retrans, \
                  "bytes_sent=" best_bytes_sent, \
                  "data_segs_out=" best_data_segs_out, \
                  "conn=\"" best_conn "\""
          } else {
            print now, "no_data_connection"
          }
        }
      ' >> "$OUT_DIR/ss_rtt.log"

  sleep "${SS_INTERVAL:-0.05}"
done