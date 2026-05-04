#!/usr/bin/env python3
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
        sys.exit(1)

    out_dir = sys.argv[1]
    json_file = os.path.join(out_dir, "iperf.json")

    if not os.path.exists(json_file):
        print(f"[WARN] Missing {json_file}", file=sys.stderr)
        sys.exit(0)

    with open(json_file, "r") as f:
        data = json.load(f)

    times = []
    throughputs_mbps = []

    for interval in data.get("intervals", []):
        s = interval.get("sum", {})
        t = s.get("end")
        bps = s.get("bits_per_second")

        if t is None or bps is None:
            continue

        times.append(float(t))
        throughputs_mbps.append(float(bps) / 1e6)

    if not times:
        print("[WARN] No iperf interval data found", file=sys.stderr)
        sys.exit(0)

    avg_mbps = sum(throughputs_mbps) / len(throughputs_mbps)
    max_mbps = max(throughputs_mbps)
    min_mbps = min(throughputs_mbps)

    plt.figure(figsize=(12, 5))
    plt.plot(times, throughputs_mbps, linewidth=1.2)
    plt.scatter(times, throughputs_mbps, s=8)

    plt.xlabel("Time (s)")
    plt.ylabel("Throughput (Mbps)")
    plt.title("iperf3 Throughput over Time")
    plt.grid(True)
    plt.tight_layout()

    out_png = os.path.join(out_dir, "iperf3.png")
    plt.savefig(out_png, dpi=150, bbox_inches="tight")
    plt.close()

    summary_file = os.path.join(out_dir, "iperf_summary.txt")
    with open(summary_file, "w") as f:
        f.write(f"intervals={len(throughputs_mbps)}\n")
        f.write(f"avg_mbps={avg_mbps:.6f}\n")
        f.write(f"min_mbps={min_mbps:.6f}\n")
        f.write(f"max_mbps={max_mbps:.6f}\n")

    print(f"[OK] Saved throughput graph to: {out_png}")
    print(f"[OK] Saved summary to: {summary_file}")
    print(f"[INFO] avg={avg_mbps:.3f} Mbps min={min_mbps:.3f} Mbps max={max_mbps:.3f} Mbps")


if __name__ == "__main__":
    main()