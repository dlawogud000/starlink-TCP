#!/usr/bin/env python3
import re
import sys
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def parse_ss_rtt_log(path: Path):
    rows = []
    pattern = re.compile(
        r"^([0-9]+\.[0-9]+).*?"
        r"rtt_ms=([0-9.]+).*?"
        r"cwnd=([0-9]+).*?"
        r"bytes_sent=([0-9]+)"
    )

    with path.open("r", errors="ignore") as f:
        for line in f:
            if "no_data_connection" in line or "no_connection" in line:
                continue

            m = pattern.search(line)
            if not m:
                continue

            ts = float(m.group(1))
            rtt_ms = float(m.group(2))
            cwnd = int(m.group(3))
            bytes_sent = int(m.group(4))

            rows.append((ts, rtt_ms, cwnd, bytes_sent))

    return rows


def parse_pop_interval_log(path: Path):
    rows = []

    with path.open("r", errors="ignore") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 2:
                continue

            try:
                ts = float(parts[0])
                interval = float(parts[1])
                rows.append((ts, interval))
            except ValueError:
                continue

    return rows


def build_ss_throughput(ss_rows):
    thr_rows = []

    for i in range(1, len(ss_rows)):
        prev_ts, prev_rtt, prev_cwnd, prev_bytes = ss_rows[i - 1]
        curr_ts, curr_rtt, curr_cwnd, curr_bytes = ss_rows[i]

        dt = curr_ts - prev_ts
        dbytes = curr_bytes - prev_bytes

        if dt <= 0:
            continue

        if dbytes < 0:
            continue

        mid_ts = (prev_ts + curr_ts) / 2.0
        throughput_mbps = dbytes * 8.0 / dt / 1_000_000.0

        thr_rows.append({
            "ts": mid_ts,
            "start": prev_ts,
            "end": curr_ts,
            "throughput_mbps": throughput_mbps,
            "rtt_ms": curr_rtt,
            "cwnd": curr_cwnd,
            "bytes_delta": dbytes,
            "dt": dt,
        })

    return thr_rows


def nearest_throughput(thr_rows, target_ts):
    if not thr_rows:
        return None

    return min(thr_rows, key=lambda x: abs(x["ts"] - target_ts))


def containing_throughput(thr_rows, target_ts):
    candidates = [
        row for row in thr_rows
        if row["start"] <= target_ts <= row["end"]
    ]

    if candidates:
        return min(candidates, key=lambda x: abs(x["ts"] - target_ts))

    return nearest_throughput(thr_rows, target_ts)


def pearson_corr(x, y):
    if len(x) < 2:
        return None
    if np.std(x) == 0 or np.std(y) == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <LOG_DIR> <SAVE_DIR>")
        sys.exit(1)

    log_dir = Path(sys.argv[1])
    save_dir = Path(sys.argv[2])

    save_dir.mkdir(parents=True, exist_ok=True)

    ss_path = log_dir / "ss_rtt.log"
    pop_path = log_dir / "pop_interval.log"

    if not ss_path.exists():
        print(f"[ERROR] Missing file: {ss_path}")
        sys.exit(1)

    if not pop_path.exists():
        print(f"[ERROR] Missing file: {pop_path}")
        sys.exit(1)

    ss_rows = parse_ss_rtt_log(ss_path)
    pop_rows = parse_pop_interval_log(pop_path)

    if len(ss_rows) < 2:
        print("[ERROR] Not enough ss_rtt.log data")
        sys.exit(1)

    if not pop_rows:
        print("[ERROR] No pop_interval.log data")
        sys.exit(1)

    thr_rows = build_ss_throughput(ss_rows)

    if not thr_rows:
        print("[ERROR] Could not build throughput from ss_rtt.log")
        sys.exit(1)

    aligned = []

    for pop_ts, pop_interval in pop_rows:
        event_ts = pop_ts - pop_interval

        thr = containing_throughput(thr_rows, event_ts)
        if thr is None:
            continue

        aligned.append({
            "pop_ts": pop_ts,
            "event_ts": event_ts,
            "pop_interval": pop_interval,
            "thr_ts": thr["ts"],
            "throughput_mbps": thr["throughput_mbps"],
            "rtt_ms": thr["rtt_ms"],
            "cwnd": thr["cwnd"],
        })

    if len(aligned) < 2:
        print("[ERROR] Not enough aligned data")
        sys.exit(1)

    pop_intervals = np.array([x["pop_interval"] for x in aligned])
    throughputs = np.array([x["throughput_mbps"] for x in aligned])
    rtts = np.array([x["rtt_ms"] for x in aligned])
    cwnds = np.array([x["cwnd"] for x in aligned])

    corr_interval_thr = pearson_corr(pop_intervals, throughputs)
    corr_interval_rtt = pearson_corr(pop_intervals, rtts)
    corr_interval_cwnd = pearson_corr(pop_intervals, cwnds)

    t0 = min(aligned[0]["event_ts"], thr_rows[0]["ts"])
    aligned_times = np.array([x["event_ts"] - t0 for x in aligned])
    thr_times = np.array([x["ts"] - t0 for x in thr_rows])
    thr_values = np.array([x["throughput_mbps"] for x in thr_rows])

    result_name = log_dir.name

    out_txt = save_dir / f"{result_name}_ss_correlation_result.txt"
    out_png = save_dir / f"{result_name}_ss_pop_interval_throughput.png"
    out_scatter = save_dir / f"{result_name}_ss_pop_interval_throughput_scatter.png"

    with out_txt.open("w") as f:
        f.write("Correlation analysis using ss_rtt.log bytes_sent\n")
        f.write("================================================\n\n")
        f.write(f"log_dir: {log_dir}\n")
        f.write(f"save_dir: {save_dir}\n\n")

        f.write(f"ss_rtt samples: {len(ss_rows)}\n")
        f.write(f"ss throughput samples: {len(thr_rows)}\n")
        f.write(f"pop interval samples: {len(pop_rows)}\n")
        f.write(f"aligned samples: {len(aligned)}\n\n")

        f.write("[Pearson correlation]\n")
        f.write(f"pop_interval vs ss_throughput_mbps: {corr_interval_thr}\n")
        f.write(f"pop_interval vs ss_rtt_ms: {corr_interval_rtt}\n")
        f.write(f"pop_interval vs ss_cwnd: {corr_interval_cwnd}\n\n")

        f.write("[Aligned data]\n")
        f.write("event_ts pop_interval throughput_mbps rtt_ms cwnd thr_ts\n")
        for row in aligned:
            f.write(
                f"{row['event_ts']} "
                f"{row['pop_interval']} "
                f"{row['throughput_mbps']} "
                f"{row['rtt_ms']} "
                f"{row['cwnd']} "
                f"{row['thr_ts']}\n"
            )

    fig, ax1 = plt.subplots(figsize=(12, 5))

    ax1.plot(
        aligned_times,
        pop_intervals,
        linewidth=1,
        label="POP ping response interval"
    )
    ax1.set_xlabel("Time since start (s)")
    ax1.set_ylabel("POP response interval (s)")
    ax1.grid(True, alpha=0.3)

    ax2 = ax1.twinx()
    ax2.plot(
        thr_times,
        thr_values,
        linewidth=1,
        alpha=0.8,
        label="SS-based throughput"
    )
    ax2.set_ylabel("SS throughput (Mbps)")

    plt.title("POP Ping Interval vs SS-based TCP Throughput")

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper right")

    fig.tight_layout()
    plt.savefig(out_png, dpi=200)
    plt.close()

    plt.figure(figsize=(6, 5))
    plt.scatter(pop_intervals, throughputs, s=12)
    plt.xlabel("POP response interval (s)")
    plt.ylabel("SS throughput (Mbps)")
    plt.title("Correlation: POP Interval vs SS Throughput")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_scatter, dpi=200)
    plt.close()

    print(f"[INFO] Saved: {out_txt}")
    print(f"[INFO] Saved: {out_png}")
    print(f"[INFO] Saved: {out_scatter}")
    print(f"[INFO] pop_interval vs ss_throughput_mbps correlation = {corr_interval_thr}")


if __name__ == "__main__":
    main()