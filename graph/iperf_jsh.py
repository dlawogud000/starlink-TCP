#!/usr/bin/env python3
import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import MultipleLocator


if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
    sys.exit(1)

path = sys.argv[1]
json_file = os.path.join(path, "iperf.json")
meta_file = os.path.join(path, "meta.txt")

if not os.path.exists(json_file):
    print(f"[WARN] Missing {json_file}", file=sys.stderr)
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


def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default


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


def parse_throughput(data):
    times = []
    throughputs = []

    for interval in data.get("intervals", []):
        s = interval.get("sum", {})
        t = s.get("end")
        bw = s.get("bits_per_second")
        if t is None or bw is None:
            continue

        times.append(float(t))
        throughputs.append(float(bw) / 1e6)  # Mbps

    if not times:
        return [], []

    pairs = sorted(zip(times, throughputs), key=lambda x: x[0])
    times, throughputs = zip(*pairs)
    return list(times), list(throughputs)


def parse_udp_metrics_from_data(data, receiver_only=False):
    times = []
    jitters = []
    losses = []

    for interval in data.get("intervals", []):
        s = interval.get("sum", {})
        t = s.get("end")
        jitter = s.get("jitter_ms")
        loss = s.get("lost_percent")

        if t is None or jitter is None or loss is None:
            continue

        if receiver_only:
            sender_flag = s.get("sender")
            if sender_flag is True:
                continue

        times.append(float(t))
        jitters.append(float(jitter))
        losses.append(float(loss))

    if not times:
        return [], [], []

    pairs = sorted(zip(times, jitters, losses), key=lambda x: x[0])
    times = [p[0] for p in pairs]
    jitters = [p[1] for p in pairs]
    losses = [p[2] for p in pairs]
    return times, jitters, losses


def parse_server_output_json(data):
    """
    --get-server-output 사용 시 iperf3 버전에 따라
    server_output_json 이 dict 이거나 str(JSON text)일 수 있음
    """
    soj = data.get("server_output_json")

    if isinstance(soj, dict):
        return soj

    if isinstance(soj, str):
        try:
            return json.loads(soj)
        except Exception:
            return None

    return None


with open(json_file) as f:
    data = json.load(f)

meta = load_meta(meta_file)

protocol = data.get("start", {}).get("test_start", {}).get("protocol", "").lower()
direction = meta.get("direction", "unknown").lower()

parallel = safe_int(meta.get("parallel"), None)
if parallel is None:
    intervals = data.get("intervals", [])
    if intervals:
        parallel = len(intervals[0].get("streams", []))
    else:
        parallel = 1

mode = "single" if parallel == 1 else "multi"

title_prefix = f"{protocol.upper()} {mode} {direction}".strip()

# Throughput output dir
throughput_dir = os.path.join(path, "analyze_log", "throughput")
os.makedirs(throughput_dir, exist_ok=True)

times, throughputs = parse_throughput(data)

if not times:
    print("[WARN] No iperf interval data found", file=sys.stderr)
    sys.exit(0)

save_full_graph(
    times,
    throughputs,
    "Throughput (Mbps)",
    f"{title_prefix} Throughput over Time",
    throughput_dir,
    "throughput_full.png",
)
save_split_graphs(
    times,
    throughputs,
    "Throughput (Mbps)",
    f"{title_prefix} Throughput over Time",
    throughput_dir,
    "throughput",
    window=60,
    tick=5,
)

print(f"[OK] Saved throughput graphs to: {throughput_dir}")

# UDP extra graphs
if protocol == "udp":
    udp_dir = os.path.join(path, "analyze_log", "udp")
    os.makedirs(udp_dir, exist_ok=True)

    udp_times = []
    udp_jitters = []
    udp_losses = []

    # downlink: client receiver 값이 local json에 보통 존재
    # uplink: server receiver 값이 더 의미 크므로 server_output_json 우선
    if direction == "uplink":
        server_data = parse_server_output_json(data)
        if server_data is not None:
            udp_times, udp_jitters, udp_losses = parse_udp_metrics_from_data(
                server_data, receiver_only=False
            )

        if not udp_times:
            # fallback: local json에서 receiver-side sum만 시도
            udp_times, udp_jitters, udp_losses = parse_udp_metrics_from_data(
                data, receiver_only=True
            )
    else:
        udp_times, udp_jitters, udp_losses = parse_udp_metrics_from_data(
            data, receiver_only=False
        )

    if udp_times:
        save_full_graph(
            udp_times,
            udp_jitters,
            "Jitter (ms)",
            f"{title_prefix} UDP Jitter over Time",
            udp_dir,
            "jitter_full.png",
        )
        save_split_graphs(
            udp_times,
            udp_jitters,
            "Jitter (ms)",
            f"{title_prefix} UDP Jitter over Time",
            udp_dir,
            "jitter",
            window=60,
            tick=5,
        )

        save_full_graph(
            udp_times,
            udp_losses,
            "Loss (%)",
            f"{title_prefix} UDP Loss over Time",
            udp_dir,
            "loss_full.png",
        )
        save_split_graphs(
            udp_times,
            udp_losses,
            "Loss (%)",
            f"{title_prefix} UDP Loss over Time",
            udp_dir,
            "loss",
            window=60,
            tick=5,
        )

        print(f"[OK] Saved UDP graphs to: {udp_dir}")
    else:
        print("[INFO] No UDP jitter/loss data found")