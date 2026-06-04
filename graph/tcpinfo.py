#!/usr/bin/env python3
import csv
import os
import re
import sys
from collections import defaultdict

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

# flow_key -> list of metric samples
flows = defaultdict(list)
current_time = None
pending_flow_key = None

# ss header examples:
# ESTAB 0 0 192.168.1.150:38881 165.194.35.203:20075
# ESTAB 0 0 [addr]:port [addr]:port
HEADER_RE = re.compile(r"^(ESTAB|SYN-SENT|SYN-RECV|FIN-WAIT-1|FIN-WAIT-2|TIME-WAIT|CLOSE-WAIT|LAST-ACK|CLOSING)\b")
TIME_RE = re.compile(r"^\d+\.\d+$")

def normalize_endpoint(ep: str) -> str:
    ep = ep.strip()
    if ep.startswith("[") and "]:" in ep:
        host, port = ep.rsplit(":", 1)
        return f"{host.strip('[]')}:{port}"
    return ep

def parse_header(line: str):
    parts = line.split()
    if len(parts) < 5 or not HEADER_RE.match(parts[0]):
        return None
    local = normalize_endpoint(parts[3])
    peer = normalize_endpoint(parts[4])
    return f"{local}->{peer}"

def parse_metrics(line: str):
    def get_int(name, default=None):
        m = re.search(rf"\b{name}:(\d+)", line)
        return int(m.group(1)) if m else default

    def get_float(name, default=None):
        m = re.search(rf"\b{name}:([0-9.]+)", line)
        return float(m.group(1)) if m else default

    cwnd = get_int("cwnd")
    rtt = get_float("rtt")
    bytes_sent = get_int("bytes_sent", -1)
    bytes_acked = get_int("bytes_acked", -1)
    delivery_rate = get_float("delivery_rate")
    pacing_rate = get_float("pacing_rate")
    snd_wnd = get_int("snd_wnd")
    rcv_space = get_int("rcv_space")
    return {
        "cwnd": cwnd,
        "rtt": rtt,
        "bytes_sent": bytes_sent,
        "bytes_acked": bytes_acked,
        "delivery_rate": delivery_rate,
        "pacing_rate": pacing_rate,
        "snd_wnd": snd_wnd,
        "rcv_space": rcv_space,
    }

with open(logfile) as f:
    for raw in f:
        line = raw.strip()
        if not line:
            continue

        if TIME_RE.match(line):
            current_time = float(line)
            pending_flow_key = None
            continue

        key = parse_header(line)
        if key is not None:
            pending_flow_key = key
            continue

        if current_time is None or pending_flow_key is None:
            continue

        if "cwnd:" in line or "rtt:" in line:
            metrics = parse_metrics(line)
            if metrics["cwnd"] is None and metrics["rtt"] is None:
                continue
            metrics["time"] = current_time
            flows[pending_flow_key].append(metrics)
            # Metric line belongs to the immediately preceding socket header.
            pending_flow_key = None

if not flows:
    print("[WARN] No valid TCP info data found", file=sys.stderr)
    sys.exit(0)

# Normalize time globally.
t0 = min(sample["time"] for samples in flows.values() for sample in samples)
for samples in flows.values():
    for sample in samples:
        sample["time_rel"] = sample["time"] - t0

# Sort flows by maximum bytes_sent/acked so flow1 is usually the dominant/earliest flow.
def flow_sort_key(item):
    _key, samples = item
    return max((max(s.get("bytes_sent", -1), s.get("bytes_acked", -1)) for s in samples), default=-1)

sorted_flows = sorted(flows.items(), key=flow_sort_key, reverse=True)
flow_name = {key: f"flow{i}" for i, (key, _samples) in enumerate(sorted_flows, start=1)}

# CSV export with all samples.
csv_path = os.path.join(path, "tcpinfo_flows.csv")
with open(csv_path, "w", newline="") as f:
    writer = csv.writer(f)
    writer.writerow([
        "flow_id", "flow_tuple", "time_s", "cwnd", "rtt_ms",
        "bytes_sent", "bytes_acked", "delivery_rate", "pacing_rate",
        "snd_wnd", "rcv_space",
    ])
    for key, samples in sorted_flows:
        for s in samples:
            writer.writerow([
                flow_name[key], key, f"{s['time_rel']:.6f}",
                s.get("cwnd"), s.get("rtt"), s.get("bytes_sent"),
                s.get("bytes_acked"), s.get("delivery_rate"),
                s.get("pacing_rate"), s.get("snd_wnd"), s.get("rcv_space"),
            ])

# Helper for plotting a metric across all flows.
def plot_metric(metric, ylabel, title, filename, scatter=False):
    plt.figure(figsize=(12, 4))
    plotted = 0
    for key, samples in sorted_flows:
        xs = [s["time_rel"] for s in samples if s.get(metric) is not None]
        ys = [s[metric] for s in samples if s.get(metric) is not None]
        if not xs:
            continue
        if scatter:
            plt.scatter(xs, ys, s=5, label=flow_name[key], alpha=0.75)
        else:
            plt.plot(xs, ys, linewidth=0.9, label=flow_name[key], alpha=0.85)
        plotted += 1
    if plotted == 0:
        plt.close()
        return
    plt.xlabel("Time (s)")
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid()
    if plotted <= 16:
        plt.legend(ncol=2, fontsize=8)
    plt.savefig(os.path.join(path, filename), dpi=150, bbox_inches="tight")
    plt.close()

# Preserve old filenames, now with all flows overlaid.
plot_metric("cwnd", "cwnd", "cwnd over Time per Flow", "cwnd.png")
plot_metric("rtt", "RTT (ms)", "TCP RTT over Time per Flow", "tcp_rtt.png", scatter=True)

# Extra plots that are useful for rwnd experiments, generated only when fields exist.
plot_metric("snd_wnd", "snd_wnd (bytes)", "Sender-observed Receive Window per Flow", "snd_wnd.png")
plot_metric("rcv_space", "rcv_space (bytes)", "Receiver Space per Flow", "rcv_space.png")