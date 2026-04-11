#!/usr/bin/env python3
import math
import os
import re
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
logfile = os.path.join(path, "ss_tcpinfo.log")
meta_file = os.path.join(path, "meta.txt")

if not os.path.exists(logfile):
    print(f"[WARN] Missing {logfile}", file=sys.stderr)
    sys.exit(0)

direction = None
if os.path.exists(meta_file):
    with open(meta_file) as f:
        for line in f:
            line = line.strip()
            if line.startswith("direction="):
                direction = line.split("=", 1)[1].strip()
                break

if direction != "uplink":
    print("[INFO] tcpinfo_multi.py skipped (direction is not uplink)")
    sys.exit(0)

cwnd_dir = os.path.join(path, "analyze_log", "cwnd")
rtt_dir = os.path.join(path, "analyze_log", "rtt")
os.makedirs(cwnd_dir, exist_ok=True)
os.makedirs(rtt_dir, exist_ok=True)

times = []
cwnds = []
rtts = []

current_time = None
best_entry = None

def parse_metrics(line):
    cwnd = None
    rtt = None
    bytes_sent = -1

    m = re.search(r"cwnd:(\d+)", line)
    if m:
        cwnd = int(m.group(1))

    m = re.search(r"rtt:([0-9.]+)", line)
    if m:
        rtt = float(m.group(1))

    m = re.search(r"bytes_sent:(\d+)", line)
    if m:
        bytes_sent = int(m.group(1))

    return cwnd, rtt, bytes_sent

def flush_best():
    global best_entry
    if best_entry is not None:
        times.append(best_entry["time"])
        cwnds.append(best_entry["cwnd"])
        rtts.append(best_entry["rtt"])

with open(logfile) as f:
    for line in f:
        line = line.strip()

        if re.match(r"^\d+\.\d+$", line):
            flush_best()
            current_time = float(line)
            best_entry = None
            continue

        if "cwnd:" in line and "rtt:" in line:
            cwnd, rtt, bytes_sent = parse_metrics(line)
            if cwnd is None or rtt is None or current_time is None:
                continue

            if best_entry is None or bytes_sent > best_entry["bytes_sent"]:
                best_entry = {
                    "time": current_time,
                    "cwnd": cwnd,
                    "rtt": rtt,
                    "bytes_sent": bytes_sent,
                }

flush_best()

if not times:
    print("[WARN] No valid TCP info data found", file=sys.stderr)
    sys.exit(0)

t0 = times[0]
times = [t - t0 for t in times]

cwnd_pairs = sorted(zip(times, cwnds), key=lambda x: x[0])
times_cwnd, cwnds = zip(*cwnd_pairs)
times_cwnd = list(times_cwnd)
cwnds = list(cwnds)

rtt_pairs = sorted(zip(times, rtts), key=lambda x: x[0])
times_rtt, rtts = zip(*rtt_pairs)
times_rtt = list(times_rtt)
rtts = list(rtts)

def save_full_graph(x, y, ylabel, title, save_dir, filename):
    plt.figure(figsize=(14, 5))
    plt.plot(x, y, linewidth=1)
    plt.scatter(x, y, s=10)
    plt.xlabel("Time (s)")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid()
    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, filename), dpi=150, bbox_inches="tight")
    plt.close()

def save_split_graphs(x, y, ylabel, title_prefix, save_dir, file_prefix, window=60, tick=5):
    max_time = max(x)
    num_windows = math.ceil(max_time / window)

    for i in range(num_windows):
        start = i * window
        end = min((i + 1) * window, max_time)

        xs = []
        ys = []
        for tx, ty in zip(x, y):
            if start <= tx <= end:
                xs.append(tx)
                ys.append(ty)

        if not xs:
            continue

        plt.figure(figsize=(16, 5))
        plt.plot(xs, ys, linewidth=1)
        plt.scatter(xs, ys, s=10)
        plt.xlim(start, end)

        ax = plt.gca()
        ax.xaxis.set_major_locator(MultipleLocator(tick))
        plt.xticks(rotation=90, fontsize=8)

        plt.xlabel("Time (s)")
        plt.ylabel(ylabel)
        plt.title(f"{title_prefix} ({int(start)}-{int(end)}s)")
        plt.grid()
        plt.tight_layout()
        plt.savefig(
            os.path.join(save_dir, f"{file_prefix}_{int(start)}_{int(end)}.png"),
            dpi=150,
            bbox_inches="tight"
        )
        plt.close()

save_full_graph(times_cwnd, cwnds, "cwnd", "cwnd over Time", cwnd_dir, "cwnd_full.png")
save_split_graphs(times_cwnd, cwnds, "cwnd", "cwnd over Time", cwnd_dir, "cwnd", window=60, tick=5)

save_full_graph(times_rtt, rtts, "RTT (ms)", "TCP RTT over Time", rtt_dir, "rtt_full.png")
save_split_graphs(times_rtt, rtts, "RTT (ms)", "TCP RTT over Time", rtt_dir, "rtt", window=60, tick=5)

print(f"[OK] Saved cwnd graphs to: {cwnd_dir}")
print(f"[OK] Saved rtt graphs to: {rtt_dir}")