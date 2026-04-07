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
logfile = os.path.join(path, "ss_rtt.log")

if not os.path.exists(logfile):
    print(f"[WARN] Missing {logfile}", file=sys.stderr)
    sys.exit(0)

times = []
rtts = []

with open(logfile) as f:
    for line in f:
        line = line.strip()
        if not line or "no_connection" in line or "no_data_connection" in line:
            continue

        m = re.search(r"^([0-9]+\.[0-9]+)\s+rtt_ms=([0-9.]+)", line)
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

plt.savefig(os.path.join(path, "ss_rtt.png"), dpi=150, bbox_inches="tight")
plt.close()