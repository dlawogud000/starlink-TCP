#!/usr/bin/env python3
import json
import os
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
from matplotlib import ticker
import matplotlib.pyplot as plt

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
json_file = os.path.join(path, "iperf.json")

if not os.path.exists(json_file):
    print(f"[WARN] Missing {json_file}", file=sys.stderr)
    sys.exit(0)

with open(json_file) as f:
    data = json.load(f)

test_start = data.get("start", {}).get("test_start", {})
is_udp = str(test_start.get("protocol", "")).upper() == "UDP"
num_streams = int(test_start.get("num_streams", 1) or 1)

# Aggregate interval series. iperf3 puts aggregate data in interval["sum"].
sum_times = []
sum_throughputs = []
jitters = []
losses = []

# Per-flow interval series. Keys are socket IDs when available.
flow_times = defaultdict(list)
flow_throughputs = defaultdict(list)
flow_labels = {}

# Use start.connected metadata to create stable labels.
for i, conn in enumerate(data.get("start", {}).get("connected", []), start=1):
    sid = conn.get("socket")
    if sid is None:
        continue
    local = f"{conn.get('local_host', '?')}:{conn.get('local_port', '?')}"
    remote = f"{conn.get('remote_host', '?')}:{conn.get('remote_port', '?')}"
    flow_labels[int(sid)] = f"flow{i} {local} -> {remote}"

for interval in data.get("intervals", []):
    s = interval.get("sum", {})
    t = s.get("end")
    bw = s.get("bits_per_second")
    if t is not None and bw is not None:
        sum_times.append(float(t))
        sum_throughputs.append(float(bw) / 1e6)  # Mbps
        if is_udp:
            jitters.append(float(s.get("jitter_ms", 0.0)))
            losses.append(float(s.get("lost_percent", 0.0)))

    for stream in interval.get("streams", []):
        sid = stream.get("socket")
        if sid is None:
            # Fallback for unusual JSON without socket id.
            sid = len(flow_times) + 1
        sid = int(sid)
        st = stream.get("end")
        sbw = stream.get("bits_per_second")
        if st is None or sbw is None:
            continue
        flow_times[sid].append(float(st))
        flow_throughputs[sid].append(float(sbw) / 1e6)

if not sum_times and not flow_times:
    print("[WARN] No iperf interval data found", file=sys.stderr)
    sys.exit(0)

# 1) Aggregate throughput plot. This preserves the old output filename.
if sum_times:
    plt.figure(figsize=(12, 4))
    plt.plot(sum_times, sum_throughputs, linewidth=1)
    ax = plt.gca()
    ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))
    ymax = max(sum_throughputs) if sum_throughputs else 0
    if ymax < 200:
        ax.set_ylim(bottom=0, top=max(150, ymax * 1.15))
    else:
        ax.set_ylim(bottom=0, top=max(350, ymax * 1.15))
    plt.xlabel("Time (s)")
    plt.ylabel("Throughput (Mbps)")
    plt.title(f"Aggregate Throughput over Time ({num_streams} flow{'s' if num_streams != 1 else ''})")
    plt.grid()
    plt.savefig(os.path.join(path, "iperf3.png"), dpi=150, bbox_inches="tight")
    plt.close()

# 2) Per-flow throughput plot.
if flow_times:
    plt.figure(figsize=(12, 4))
    for idx, sid in enumerate(sorted(flow_times), start=1):
        label = flow_labels.get(sid, f"flow{idx}")
        # Keep legend readable: show flow number, not the entire tuple.
        short_label = label.split()[0]
        plt.plot(flow_times[sid], flow_throughputs[sid], linewidth=0.9, alpha=0.6, label=short_label)

    ax = plt.gca()
    ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))
    plt.xlabel("Time (s)")
    plt.ylabel("Throughput per Flow (Mbps)")
    plt.title("Per-flow Throughput over Time")
    plt.grid()
    if len(flow_times) <= 16:
        plt.legend(ncol=2, fontsize=8)
    plt.savefig(os.path.join(path, "iperf3_flows.png"), dpi=150, bbox_inches="tight")
    plt.close()

# 3) CSV export for later analysis.
csv_path = os.path.join(path, "iperf3_flows.csv")
with open(csv_path, "w") as f:
    f.write("flow_id,time_s,throughput_mbps\n")
    for idx, sid in enumerate(sorted(flow_times), start=1):
        for t, bw in zip(flow_times[sid], flow_throughputs[sid]):
            f.write(f"flow{idx},{t},{bw}\n")

if is_udp and sum_times:
    plt.figure()
    plt.plot(sum_times[:len(jitters)], jitters, linewidth=1)
    plt.xlabel("Time (s)")
    plt.ylabel("Jitter (ms)")
    plt.title("UDP Aggregate Jitter over Time")
    plt.grid()
    plt.savefig(os.path.join(path, "server_udp_jitter.png"), dpi=150, bbox_inches="tight")
    plt.close()

    plt.figure()
    plt.plot(sum_times[:len(losses)], losses, linewidth=1)
    plt.xlabel("Time (s)")
    plt.ylabel("Loss (%)")
    plt.title("UDP Aggregate Loss over Time")
    plt.grid()
    plt.savefig(os.path.join(path, "server_udp_loss.png"), dpi=150, bbox_inches="tight")
    plt.close()