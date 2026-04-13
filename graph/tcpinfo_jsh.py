#!/usr/bin/env python3
import math
import os
import re
import sys
import statistics

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


def load_meta(meta_path):
    meta = {}
    if not os.path.exists(meta_path):
        return meta

    with open(meta_path) as f:
        for line in f:
            line = line.strip()
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            meta[k.strip()] = v.strip()
    return meta


def safe_int(x, default=None):
    try:
        return int(x)
    except Exception:
        return default


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
    if not x:
        return

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


meta = load_meta(meta_file)
protocol = meta.get("protocol", "tcp").lower()
direction = meta.get("direction", "unknown").lower()
parallel = safe_int(meta.get("parallel"), 1)

if protocol != "tcp":
    print("[INFO] tcpinfo.py skipped (protocol is not tcp)")
    sys.exit(0)

# 현재 구조에서는 uplink sender 측 tcpinfo 해석이 가장 자연스러움
if direction != "uplink":
    print("[INFO] tcpinfo.py skipped (direction is not uplink; sender-side tcpinfo is recommended)")
    sys.exit(0)

mode = "single" if parallel == 1 else "multi"
title_prefix = f"TCP {mode} {direction}"

cwnd_dir = os.path.join(path, "analyze_log", "cwnd")
rtt_dir = os.path.join(path, "analyze_log", "rtt")
os.makedirs(cwnd_dir, exist_ok=True)
os.makedirs(rtt_dir, exist_ok=True)

times = []
cwnds = []
rtts = []

current_time = None
current_entries = []


def flush_block():
    global current_entries

    if current_time is None or not current_entries:
        current_entries = []
        return

    block_cwnds = [e["cwnd"] for e in current_entries if e["cwnd"] is not None]
    block_rtts = [e["rtt"] for e in current_entries if e["rtt"] is not None]

    if not block_cwnds or not block_rtts:
        current_entries = []
        return

    # single이면 사실상 값 하나일 가능성이 큼
    # multi면 median 집계
    if mode == "single":
        chosen = max(current_entries, key=lambda e: e["bytes_sent"])
        times.append(current_time)
        cwnds.append(chosen["cwnd"])
        rtts.append(chosen["rtt"])
    else:
        times.append(current_time)
        cwnds.append(statistics.median(block_cwnds))
        rtts.append(statistics.median(block_rtts))

    current_entries = []


with open(logfile) as f:
    for raw in f:
        line = raw.strip()

        if re.match(r"^\d+\.\d+$", line):
            flush_block()
            current_time = float(line)
            current_entries = []
            continue

        if "cwnd:" in line and "rtt:" in line:
            cwnd, rtt, bytes_sent = parse_metrics(line)
            if cwnd is None or rtt is None or current_time is None:
                continue

            current_entries.append({
                "cwnd": cwnd,
                "rtt": rtt,
                "bytes_sent": bytes_sent,
            })

flush_block()

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

save_full_graph(
    times_cwnd,
    cwnds,
    "cwnd",
    f"{title_prefix} cwnd over Time",
    cwnd_dir,
    "cwnd_full.png",
)
save_split_graphs(
    times_cwnd,
    cwnds,
    "cwnd",
    f"{title_prefix} cwnd over Time",
    cwnd_dir,
    "cwnd",
    window=60,
    tick=5,
)

save_full_graph(
    times_rtt,
    rtts,
    "RTT (ms)",
    f"{title_prefix} RTT over Time",
    rtt_dir,
    "rtt_full.png",
)
save_split_graphs(
    times_rtt,
    rtts,
    "RTT (ms)",
    f"{title_prefix} RTT over Time",
    rtt_dir,
    "rtt",
    window=60,
    tick=5,
)

print(f"[OK] Saved cwnd graphs to: {cwnd_dir}")
print(f"[OK] Saved rtt graphs to: {rtt_dir}")