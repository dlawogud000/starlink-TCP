#!/usr/bin/env python3
import json
import os
import re
import sys
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
from matplotlib import ticker
import matplotlib.pyplot as plt
import math

bin_size = float(os.environ.get("IPERF_AGG_BIN_SEC", "0.5"))

def bin_time(t):
    return math.floor(float(t) / bin_size + 0.5) * bin_size

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
if not os.path.isdir(path):
    print(f"[ERROR] Expected output directory, got: {path}", file=sys.stderr)
    sys.exit(1)


def load_json(p):
    with open(p) as f:
        return json.load(f)


def read_expected_ports(out_dir):
    """Return ports listed by run_iperf.sh, if iperf_ports.txt exists."""
    ports_file = os.path.join(out_dir, "iperf_ports.txt")
    if not os.path.exists(ports_file):
        return None
    ports = []
    with open(ports_file) as f:
        for line in f:
            port = line.strip()
            if port:
                ports.append(port)
    return ports or None


def discover_iperf_jsons(out_dir):
    """Return [(json_path, port_label)] supporting multiport and old single-file mode.

    Important: if multiport files exist, do NOT also read iperf.json.
    iperf.json may be a temporary backward-compatibility copy of one of the
    iperf_<port>.json files, and reading both would duplicate the same flow.
    """
    files = []
    expected_ports = read_expected_ports(out_dir)

    if expected_ports:
        # Prefer exactly the ports that were actually used in this run.
        for port in expected_ports:
            fp = os.path.join(out_dir, f"iperf_{port}.json")
            if os.path.exists(fp):
                files.append((fp, port))
    else:
        # Multiport files: iperf_<port>.json
        for name in sorted(os.listdir(out_dir)):
            m = re.fullmatch(r"iperf_(\d+)\.json", name)
            if m:
                files.append((os.path.join(out_dir, name), m.group(1)))

    # Old single-file fallback. Avoid double-counting when multiport files exist.
    single = os.path.join(out_dir, "iperf.json")
    if not files and os.path.exists(single):
        files.append((single, "single"))

    return files


json_files = discover_iperf_jsons(path)
if not json_files:
    print(f"[WARN] Missing iperf.json or iperf_<port>.json in {path}", file=sys.stderr)
    sys.exit(0)

# Aggregate across all iperf json files.
# Key is interval end time. Values are summed Mbps across ports/files.
aggregate_by_time = defaultdict(float)
aggregate_count_by_time = defaultdict(int)

# Per-flow interval series. Keys are globally unique: <port_label>:<socket>.
flow_times = defaultdict(list)
flow_throughputs = defaultdict(list)
flow_labels = {}

is_udp_any = False
num_streams_total = 0
udp_jitter_by_time = defaultdict(list)
udp_loss_by_time = defaultdict(list)

# Guard against iperf JSONs that repeat the same stream entry inside an interval.
seen_flow_points = set()

for json_path, port_label in json_files:
    try:
        data = load_json(json_path)
    except Exception as e:
        print(f"[WARN] Failed to read {json_path}: {e}", file=sys.stderr)
        continue

    test_start = data.get("start", {}).get("test_start", {})
    is_udp = str(test_start.get("protocol", "")).upper() == "UDP"
    is_udp_any = is_udp_any or is_udp
    num_streams = int(test_start.get("num_streams", 1) or 1)
    num_streams_total += num_streams

    # Use start.connected metadata to create stable labels.
    sid_to_label = {}
    for i, conn in enumerate(data.get("start", {}).get("connected", []), start=1):
        sid = conn.get("socket")
        if sid is None:
            continue
        local = f"{conn.get('local_host', '?')}:{conn.get('local_port', '?')}"
        remote = f"{conn.get('remote_host', '?')}:{conn.get('remote_port', '?')}"
        key = f"{port_label}:{int(sid)}"
        sid_to_label[int(sid)] = key
        flow_labels[key] = f"port{port_label}-flow{i} {local} -> {remote}"

    for interval in data.get("intervals", []):
        s = interval.get("sum", {})
        t = s.get("end")
        bw = s.get("bits_per_second")
        if t is not None and bw is not None:
            # iperf interval ends are normally identical across parallel processes.
            # Round to avoid tiny floating-point/string representation differences.
            t_key = bin_time(round(float(t), 6))
            aggregate_by_time[t_key] += float(bw) / 1e6
            aggregate_count_by_time[t_key] += 1
            if is_udp:
                udp_jitter_by_time[t_key].append(float(s.get("jitter_ms", 0.0)))
                udp_loss_by_time[t_key].append(float(s.get("lost_percent", 0.0)))

        for stream_idx, stream in enumerate(interval.get("streams", []), start=1):
            sid = stream.get("socket")
            if sid is None:
                key = f"{port_label}:nosocket{stream_idx}"
                flow_labels.setdefault(key, f"port{port_label}-flow{stream_idx}")
            else:
                sid = int(sid)
                key = sid_to_label.get(sid, f"{port_label}:{sid}")
                flow_labels.setdefault(key, f"port{port_label}-socket{sid}")

            st = stream.get("end")
            sbw = stream.get("bits_per_second")
            if st is None or sbw is None:
                continue

            st_f = float(st)
            point_id = (key, round(st_f, 6))
            if point_id in seen_flow_points:
                continue
            seen_flow_points.add(point_id)

            flow_times[key].append(st_f)
            flow_throughputs[key].append(float(sbw) / 1e6)

sum_times = sorted(aggregate_by_time.keys())
sum_throughputs = [aggregate_by_time[t] for t in sum_times]

if not sum_times and not flow_times:
    print("[WARN] No iperf interval data found", file=sys.stderr)
    sys.exit(0)

# 1) Aggregate throughput plot across every iperf_<port>.json.
# This preserves the old output filename: iperf3.png.
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
    plt.title(f"Aggregate Throughput over Time ({num_streams_total} flow{'s' if num_streams_total != 1 else ''})")
    plt.grid()
    plt.savefig(os.path.join(path, "iperf3.png"), dpi=150, bbox_inches="tight")
    plt.close()

# Aggregate CSV export for later analysis.
with open(os.path.join(path, "iperf3_aggregate.csv"), "w") as f:
    f.write("time_s,total_throughput_mbps,num_ports_present\n")
    for t, bw in zip(sum_times, sum_throughputs):
        f.write(f"{t},{bw},{aggregate_count_by_time[t]}\n")

# 2) Per-flow throughput plot.
if flow_times:
    plt.figure(figsize=(12, 4))
    for idx, key in enumerate(sorted(flow_times), start=1):
        label = flow_labels.get(key, f"flow{idx}")
        # Keep legend readable: show port-flow label, not the entire tuple.
        short_label = label.split()[0]
        plt.plot(flow_times[key], flow_throughputs[key], linewidth=0.9, alpha=0.6, label=short_label)

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

# 3) Per-flow CSV export for later analysis.
with open(os.path.join(path, "iperf3_flows.csv"), "w") as f:
    f.write("flow_id,port,socket,time_s,throughput_mbps\n")
    for idx, key in enumerate(sorted(flow_times), start=1):
        port, socket = key.split(":", 1) if ":" in key else ("unknown", key)
        for t, bw in zip(flow_times[key], flow_throughputs[key]):
            f.write(f"flow{idx},{port},{socket},{t},{bw}\n")

# 4) UDP aggregate jitter/loss across files if present.
if is_udp_any and sum_times:
    avg_jitters = []
    avg_losses = []
    for t in sum_times:
        js = udp_jitter_by_time.get(t, [])
        ls = udp_loss_by_time.get(t, [])
        avg_jitters.append(sum(js) / len(js) if js else 0.0)
        avg_losses.append(sum(ls) / len(ls) if ls else 0.0)

    plt.figure()
    plt.plot(sum_times, avg_jitters, linewidth=1)
    plt.xlabel("Time (s)")
    plt.ylabel("Jitter (ms)")
    plt.title("UDP Aggregate Jitter over Time")
    plt.grid()
    plt.savefig(os.path.join(path, "server_udp_jitter.png"), dpi=150, bbox_inches="tight")
    plt.close()

    plt.figure()
    plt.plot(sum_times, avg_losses, linewidth=1)
    plt.xlabel("Time (s)")
    plt.ylabel("Loss (%)")
    plt.title("UDP Aggregate Loss over Time")
    plt.grid()
    plt.savefig(os.path.join(path, "server_udp_loss.png"), dpi=150, bbox_inches="tight")
    plt.close()