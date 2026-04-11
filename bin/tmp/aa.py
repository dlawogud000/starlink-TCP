#!/usr/bin/env python3
import os
import re
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

logfile = "/home/dlawogud/git/starlink-TCP/logs/20260409_142149_tcp_bbr_downlink_1/ss_tcpinfo.log"

times = []
rtts = []

with open(logfile) as f:
    for line in f:
        line = line.strip()
        if not line or "no_connection" in line or "no_data_connection" in line:
            continue

        m = re.search(r"^([0-9]+\.[0-9]+)\s+rcv_rtt=([0-9.]+)", line)
        if m:
            times.append(float(m.group(1)))
            rtts.append(float(m.group(2)))

if not times:
    print("[WARN] No RTT data found in ss_rtt.log", file=sys.stderr)
    sys.exit(0)

t0 = times[0]
times = [t - t0 for t in times]

plt.figure()
plt.scatter(times, rtts, s=10)
plt.xlabel("Time (s)")
plt.ylabel("RTT (ms)")
plt.title("RTT over Time")
plt.grid()
plt.close()