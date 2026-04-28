#!/usr/bin/env python3
import csv
import json
import math
import os
import sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <out_dir>", file=sys.stderr)
    sys.exit(1)

out_dir = sys.argv[1]
manifest_file = os.path.join(out_dir, "chunk_manifest.csv")

if not os.path.exists(manifest_file):
    print(f"[WARN] Missing {manifest_file}", file=sys.stderr)
    sys.exit(0)

rows = []
with open(manifest_file, newline="") as f:
    reader = csv.DictReader(f)
    for row in reader:
        rows.append(row)

if not rows:
    print("[WARN] Empty chunk_manifest.csv", file=sys.stderr)
    sys.exit(0)

global_t0 = float(rows[0]["start_epoch"])

times = []
throughputs = []

udp_times = []
udp_losses = []
udp_jitters = []

summary_lines = []
total_bytes = 0.0
total_interval_seconds = 0.0
total_lost = 0
total_packets = 0
weighted_jitter_sum = 0.0
weighted_jitter_weight = 0.0
protocol_seen = None

for row in rows:
    idx = int(row["chunk_idx"])
    start_epoch = float(row["start_epoch"])
    end_epoch = float(row["end_epoch"])
    json_file = os.path.join(out_dir, row["json_file"])

    if not os.path.exists(json_file):
        summary_lines.append(f"chunk{idx}: missing {row['json_file']}")
        continue

    with open(json_file) as f:
        data = json.load(f)

    protocol = data.get("start", {}).get("test_start", {}).get("protocol", "UNKNOWN").upper()
    protocol_seen = protocol if protocol_seen is None else protocol_seen

    intervals = data.get("intervals", [])
    chunk_bytes = 0.0
    chunk_secs = 0.0

    chunk_lost = 0
    chunk_packets = 0
    chunk_weighted_jitter = 0.0
    chunk_jitter_weight = 0.0

    for interval in intervals:
        s = interval.get("sum", {})
        t_end = s.get("end")
        bps = s.get("bits_per_second")
        secs = s.get("seconds")
        byt = s.get("bytes")

        if t_end is None or bps is None or secs is None or byt is None:
            continue

        # chunk-local time -> global time
        rel_time = (start_epoch - global_t0) + float(t_end)
        times.append(rel_time)
        throughputs.append(float(bps) / 1e6)

        chunk_bytes += float(byt)
        chunk_secs += float(secs)

        if protocol == "UDP":
            jitter = float(s.get("jitter_ms", 0.0))
            lost = int(s.get("lost_packets", 0))
            packets = int(s.get("packets", 0))

            udp_times.append(rel_time)
            udp_losses.append((100.0 * lost / packets) if packets > 0 else 0.0)
            udp_jitters.append(jitter)

            chunk_lost += lost
            chunk_packets += packets
            chunk_weighted_jitter += jitter * float(secs)
            chunk_jitter_weight += float(secs)

    total_bytes += chunk_bytes
    total_interval_seconds += chunk_secs

    avg_mbps = (chunk_bytes * 8.0 / 1e6 / chunk_secs) if chunk_secs > 0 else 0.0

    if protocol == "UDP":
        total_lost += chunk_lost
        total_packets += chunk_packets
        weighted_jitter_sum += chunk_weighted_jitter
        weighted_jitter_weight += chunk_jitter_weight
        loss_pct = (100.0 * chunk_lost / chunk_packets) if chunk_packets > 0 else 0.0
        avg_jitter = (chunk_weighted_jitter / chunk_jitter_weight) if chunk_jitter_weight > 0 else 0.0
        summary_lines.append(
            f"chunk{idx}: avg_mbps={avg_mbps:.3f}, bytes={int(chunk_bytes)}, "
            f"loss_pct={loss_pct:.3f}, avg_jitter_ms={avg_jitter:.3f}, "
            f"wall_start={start_epoch:.6f}, wall_end={end_epoch:.6f}"
        )
    else:
        summary_lines.append(
            f"chunk{idx}: avg_mbps={avg_mbps:.3f}, bytes={int(chunk_bytes)}, "
            f"wall_start={start_epoch:.6f}, wall_end={end_epoch:.6f}"
        )

if not times:
    print("[WARN] No interval data found across chunks", file=sys.stderr)
    sys.exit(0)

# Combined throughput graph
plt.figure(figsize=(len(times) * 0.03, 4))
plt.plot(times, throughputs)
plt.xlabel("Time (s)")
plt.ylabel("Throughput (Mbps)")
plt.title("Chunked iperf Throughput over Time")
plt.grid()
plt.savefig(os.path.join(out_dir, "chunked_iperf3.png"), dpi=150, bbox_inches="tight")
plt.close()

if protocol_seen == "UDP" and udp_times:
    plt.figure(figsize=(len(udp_times) * 0.03, 4))
    plt.plot(udp_times, udp_losses)
    plt.xlabel("Time (s)")
    plt.ylabel("Loss (%)")
    plt.title("Chunked UDP Loss over Time")
    plt.grid()
    plt.savefig(os.path.join(out_dir, "chunked_udp_loss.png"), dpi=150, bbox_inches="tight")
    plt.close()

    plt.figure(figsize=(len(udp_times) * 0.03, 4))
    plt.plot(udp_times, udp_jitters)
    plt.xlabel("Time (s)")
    plt.ylabel("Jitter (ms)")
    plt.title("Chunked UDP Jitter over Time")
    plt.grid()
    plt.savefig(os.path.join(out_dir, "chunked_udp_jitter.png"), dpi=150, bbox_inches="tight")
    plt.close()

total_avg_mbps = (total_bytes * 8.0 / 1e6 / total_interval_seconds) if total_interval_seconds > 0 else 0.0
wall_total_seconds = float(rows[-1]["end_epoch"]) - global_t0
wall_avg_mbps = (total_bytes * 8.0 / 1e6 / wall_total_seconds) if wall_total_seconds > 0 else 0.0

summary_path = os.path.join(out_dir, "chunked_summary.txt")
with open(summary_path, "w") as f:
    f.write(f"protocol={protocol_seen}\n")
    f.write(f"num_chunks={len(rows)}\n")
    f.write(f"total_bytes={int(total_bytes)}\n")
    f.write(f"active_transfer_seconds={total_interval_seconds:.6f}\n")
    f.write(f"wall_clock_seconds={wall_total_seconds:.6f}\n")
    f.write(f"avg_mbps_active_only={total_avg_mbps:.6f}\n")
    f.write(f"avg_mbps_wall_clock={wall_avg_mbps:.6f}\n")

    if protocol_seen == "UDP":
        total_loss_pct = (100.0 * total_lost / total_packets) if total_packets > 0 else 0.0
        avg_jitter = (weighted_jitter_sum / weighted_jitter_weight) if weighted_jitter_weight > 0 else 0.0
        f.write(f"total_packets={total_packets}\n")
        f.write(f"total_lost_packets={total_lost}\n")
        f.write(f"loss_pct={total_loss_pct:.6f}\n")
        f.write(f"avg_jitter_ms={avg_jitter:.6f}\n")

    f.write("\n[per_chunk]\n")
    for line in summary_lines:
        f.write(line + "\n")