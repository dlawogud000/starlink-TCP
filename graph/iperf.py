#!/usr/bin/env python3
import json
import os
import sys
import matplotlib
matplotlib.use("Agg")
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

times = []
throughputs = []
jitters = []
losses = []
is_udp = data.get("start", {}).get("test_start", {}).get("protocol", "").upper() == "UDP"

for interval in data.get("intervals", []):
    s = interval.get("sum", {})
    t = s.get("end")
    bw = s.get("bits_per_second")
    if t is None or bw is None:
        continue
    times.append(float(t))
    throughputs.append(float(bw) / 1e6)  # Mbps

    if is_udp:
        jitters.append(float(s.get("jitter_ms", 0.0)))
        losses.append(float(s.get("lost_percent", 0.0)))

if not times:
    print("[WARN] No iperf interval data found", file=sys.stderr)
    sys.exit(0)

plt.figure()
plt.scatter(times, throughputs, s=10)
plt.xlabel("Time (s)")
plt.ylabel("Throughput (Mbps)")
plt.title("Throughput over Time")
plt.grid()
plt.savefig(os.path.join(path, "iperf3.png"), dpi=150, bbox_inches="tight")
plt.close()

if is_udp:
    plt.figure()
    plt.scatter(times[:len(jitters)], jitters, s=10)
    plt.xlabel("Time (s)")
    plt.ylabel("Jitter (ms)")
    plt.title("Server UDP Jitter over Time")
    plt.grid()
    plt.savefig(os.path.join(path, "server_udp_jitter.png"), dpi=150, bbox_inches="tight")
    plt.close()

    plt.figure()
    plt.scatter(times[:len(losses)], losses, s=10)
    plt.xlabel("Time (s)")
    plt.ylabel("Loss (%)")
    plt.title("Server UDP Loss over Time")
    plt.grid()
    plt.savefig(os.path.join(path, "server_udp_loss.png"), dpi=150, bbox_inches="tight")
    plt.close()