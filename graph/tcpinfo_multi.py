#!/usr/bin/env python3
import os
import re
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
logfile = os.path.join(path, "ss_tcpinfo.log")

if not os.path.exists(logfile):
    print(f"[WARN] Missing {logfile}", file=sys.stderr)
    sys.exit(0)

times = []
cwnds = []
rtts = []
rcv_rtts = []

current_time = None
best_entry = None

def parse_metrics(line):
    cwnd = None
    rtt = None
    rcv_rtt = None
    bytes_sent = -1

    m = re.search(r"cwnd:(\d+)", line)
    if m:
        cwnd = int(m.group(1))

    m = re.search(r"rtt:([0-9.]+)", line)
    if m:
        rtt = float(m.group(1))
    
    m = re.search(r"rcv_rtt=([0-9.]+)", line)
    if m:
        rcv_rtt = float(m.group(1))

    m = re.search(r"bytes_sent:(\d+)", line)
    if m:
        bytes_sent = int(m.group(1))

    return cwnd, rtt, rcv_rtt, bytes_sent

def flush_best():
    if best_entry is not None:
        times.append(best_entry["time"])
        cwnds.append(best_entry["cwnd"])
        rtts.append(best_entry["rtt"])
        rcv_rtts.append(best_entry["rcv_rtt"])

with open(logfile) as f:
    for line in f:
        line = line.strip()

        if re.match(r"^\d+\.\d+$", line):
            flush_best()
            current_time = float(line)
            best_entry = None
            continue

        if "cwnd:" in line and "rtt:" in line and "rcv_rtt=" in line:
            cwnd, rtt, rcv_rtt, bytes_sent = parse_metrics(line)
            if cwnd is None or rtt is None or rcv_rtt is None or current_time is None:
                continue

            if best_entry is None or bytes_sent > best_entry["bytes_sent"]:
                best_entry = {
                    "time": current_time,
                    "cwnd": cwnd,
                    "rtt": rtt,
                    "rcv_rtt": rcv_rtt,
                    "bytes_sent": bytes_sent,
                }

flush_best()

if not times:
    print("[WARN] No valid TCP info data found", file=sys.stderr)
    sys.exit(0)

t0 = times[0]
times = [t - t0 for t in times]

plt.figure()
plt.plot(times, cwnds)
plt.xlabel("Time (s)")
plt.ylabel("cwnd")
plt.title("cwnd over Time")
plt.grid()
plt.savefig(os.path.join(path, "cwnd.png"), dpi=150, bbox_inches="tight")
plt.close()

plt.figure()
plt.scatter(times, rtts, s=10)
plt.xlabel("Time (s)")
plt.ylabel("RTT (ms)")
plt.title("TCP RTT over Time")
plt.grid()
plt.savefig(os.path.join(path, "tcp_rtt.png"), dpi=150, bbox_inches="tight")
plt.close()

plt.figure()
plt.scatter(times, rcv_rtts, s=10)
plt.xlabel("Time (s)")
plt.ylabel("RCV_RTT (ms)")
plt.title("TCP RCV_RTT over Time")
plt.grid()
plt.savefig(os.path.join(path, "tcp_rcv_rtt.png"), dpi=150, bbox_inches="tight")
plt.close()