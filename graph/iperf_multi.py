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

if not os.path.exists(json_file):
    print(f"[WARN] Missing {json_file}", file=sys.stderr)
    sys.exit(0)

out_dir = os.path.join(path, "analyze_log", "throughput")
os.makedirs(out_dir, exist_ok=True)

with open(json_file) as f:
    data = json.load(f)

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
    print("[WARN] No iperf interval data found", file=sys.stderr)
    sys.exit(0)

pairs = sorted(zip(times, throughputs), key=lambda x: x[0])
times, throughputs = zip(*pairs)
times = list(times)
throughputs = list(throughputs)

def save_full_graph(x, y):
    plt.figure(figsize=(14, 5))
    plt.plot(x, y, linewidth=1)
    plt.scatter(x, y, s=10)
    plt.xlabel("Time (s)")
    plt.ylabel("Throughput (Mbps)")
    plt.title("Throughput over Time")
    plt.grid()
    plt.tight_layout()
    plt.savefig(os.path.join(out_dir, "throughput_full.png"), dpi=150, bbox_inches="tight")
    plt.close()

def save_split_graphs(x, y, window=60, tick=5):
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
        plt.ylabel("Throughput (Mbps)")
        plt.title(f"Throughput over Time ({int(start)}-{int(end)}s)")
        plt.grid()
        plt.tight_layout()
        plt.savefig(
            os.path.join(out_dir, f"throughput_{int(start)}_{int(end)}.png"),
            dpi=150,
            bbox_inches="tight"
        )
        plt.close()

save_full_graph(times, throughputs)
save_split_graphs(times, throughputs, window=60, tick=5)

print(f"[OK] Saved throughput graphs to: {out_dir}")