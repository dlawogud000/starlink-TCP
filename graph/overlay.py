#!/usr/bin/env python3
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


if len(sys.argv) < 3:
    print(f"Usage: {sys.argv[0]} <out_dir> <direction:uplink|downlink>", file=sys.stderr)
    sys.exit(1)

OUT_DIR = sys.argv[1]
DIRECTION = sys.argv[2].lower()

if DIRECTION not in ("uplink", "downlink"):
    print("[ERROR] direction must be uplink or downlink", file=sys.stderr)
    sys.exit(1)

SS_LOG = os.path.join(OUT_DIR, "ss_tcpinfo.log")
POP_LOG = os.path.join(OUT_DIR, "pop_interval.log")

THRESHOLD_MS = 50.0
BYTE_FIELD = "bytes_received" if DIRECTION == "downlink" else "bytes_sent"


def parse_ss_metrics(line):
    m = re.search(r"bytes_sent:(\d+)", line)
    bytes_sent = int(m.group(1)) if m else None

    m = re.search(r"bytes_received:(\d+)", line)
    bytes_received = int(m.group(1)) if m else None

    if BYTE_FIELD == "bytes_sent" and bytes_sent is None:
        return None
    if BYTE_FIELD == "bytes_received" and bytes_received is None:
        return None

    rtt = None
    cwnd = None
    delivery_rate = None
    pacing_rate = None

    m = re.search(r"rtt:([0-9.]+)", line)
    if m:
        rtt = float(m.group(1))

    m = re.search(r"cwnd:(\d+)", line)
    if m:
        cwnd = int(m.group(1))

    m = re.search(r"delivery_rate\s+([0-9.]+)bps", line)
    if m:
        delivery_rate = float(m.group(1))

    m = re.search(r"pacing_rate\s+([0-9.]+)bps", line)
    if m:
        pacing_rate = float(m.group(1))

    return {
        "bytes_sent": bytes_sent,
        "bytes_received": bytes_received,
        "byte_counter": bytes_received if BYTE_FIELD == "bytes_received" else bytes_sent,
        "rtt": rtt,
        "cwnd": cwnd,
        "delivery_rate": delivery_rate,
        "pacing_rate": pacing_rate,
    }


def load_ss_samples(path):
    samples = []

    current_time = None
    best = None

    def flush():
        nonlocal best
        if best is not None:
            samples.append(best)
        best = None

    with open(path, "r", errors="ignore") as f:
        for raw in f:
            line = raw.strip()

            if re.match(r"^\d+\.\d+$", line):
                flush()
                current_time = float(line)
                continue

            if current_time is None:
                continue

            metrics = parse_ss_metrics(line)
            if metrics is None:
                continue

            # 여러 ESTAB 중 bulk flow 선택
            # uplink: bytes_sent 최대
            # downlink: bytes_received 최대
            if best is None or metrics["byte_counter"] > best["byte_counter"]:
                best = {
                    "time": current_time,
                    **metrics,
                }

    flush()
    return samples


def compute_throughput(samples):
    times = []
    mbps = []

    prev = None

    for s in samples:
        if prev is None:
            prev = s
            continue

        dt = s["time"] - prev["time"]
        db = s["byte_counter"] - prev["byte_counter"]

        if dt <= 0:
            prev = s
            continue

        # 재연결/flow reset 방어
        if db < 0:
            prev = s
            continue

        throughput_mbps = db * 8 / dt / 1e6

        times.append(s["time"])
        mbps.append(throughput_mbps)

        prev = s

    return times, mbps


def load_pop_interval(path):
    times = []
    intervals_ms = []

    if not os.path.exists(path):
        return times, intervals_ms

    with open(path, "r", errors="ignore") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 2:
                continue

            try:
                t = float(parts[0])
                interval_ms = float(parts[1]) * 1000.0
            except ValueError:
                continue

            times.append(t)
            intervals_ms.append(interval_ms)

    return times, intervals_ms


def detect_handovers(pop_times, intervals_ms, threshold_ms):
    events = []

    for t, interval in zip(pop_times, intervals_ms):
        if interval >= threshold_ms:
            events.append(t)

    return events


if not os.path.exists(SS_LOG):
    print(f"[WARN] Missing {SS_LOG}", file=sys.stderr)
    sys.exit(0)

samples = load_ss_samples(SS_LOG)

if len(samples) < 2:
    print("[WARN] Not enough ss samples", file=sys.stderr)
    sys.exit(0)

ss_times, throughput_mbps = compute_throughput(samples)

if not ss_times:
    print("[WARN] No throughput data computed", file=sys.stderr)
    sys.exit(0)

pop_times, pop_intervals_ms = load_pop_interval(POP_LOG)
events_abs = detect_handovers(pop_times, pop_intervals_ms, THRESHOLD_MS)

t0 = ss_times[0]

ss_x = [t - t0 for t in ss_times]
pop_x = [t - t0 for t in pop_times]
events_x = [t - t0 for t in events_abs if t >= t0]

fig, ax1 = plt.subplots(figsize=(14, 5))

ax1.plot(ss_x, throughput_mbps, linewidth=1)
ax1.set_xlabel("Time since start (s)")
ax1.set_ylabel(f"Throughput from {BYTE_FIELD} (Mbps)")
if DIRECTION == "uplink":
    ax1.set_ylim(bottom=0, top=150)
elif DIRECTION == "downlink":
    ax1.set_ylim(bottom=0, top=max(300, max(throughput_mbps)))
ax1.grid(True, which="major", alpha=0.4)

ax1.xaxis.set_major_locator(ticker.MultipleLocator(10))
ax1.xaxis.set_minor_locator(ticker.MultipleLocator(1))

if pop_times:
    ax2 = ax1.twinx()
    ax2.plot(pop_x, pop_intervals_ms, linewidth=0.8, alpha=0.5, color="red")
    ax2.set_ylabel("POP response interval (ms)")

for ev in events_x:
    ax1.axvline(ev, linestyle="--", linewidth=0.8, alpha=0.5, color="green")

plt.title(f"Throughput and Response Interval")
plt.tight_layout()

out_png = os.path.join(OUT_DIR, f"ss_throughput_and_interval.png")
plt.savefig(out_png, dpi=200)
plt.close()

print(f"[INFO] Saved: {out_png}")