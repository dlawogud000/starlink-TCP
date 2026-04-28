#!/usr/bin/env python3
import re
import sys
from pathlib import Path

from matplotlib import ticker
import matplotlib.pyplot as plt


def parse_pop_ping_log(path: Path):
    rows = []

    # 예:
    # 1714451234.123456 64 bytes from ... time=23.1 ms
    pattern = re.compile(r"^(\d+\.\d+).*time=([\d.]+)\s*ms")

    with path.open("r", errors="ignore") as f:
        for line in f:
            m = pattern.search(line)
            if not m:
                continue

            ts = float(m.group(1))
            rtt_ms = float(m.group(2))
            rows.append((ts, rtt_ms))

    return rows


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <OUT_DIR>")
        sys.exit(1)

    out_dir = Path(sys.argv[1])
    log_path = out_dir / "pop_ping.log"

    if not log_path.exists():
        print(f"[ERROR] Missing file: {log_path}")
        sys.exit(1)

    rows = parse_pop_ping_log(log_path)

    if not rows:
        print("[ERROR] No RTT samples found in pop_ping.log")
        sys.exit(1)

    t0 = rows[0][0]
    times = [ts - t0 for ts, _ in rows]
    rtts = [rtt for _, rtt in rows]

    plt.figure(figsize=(12, 4))
    plt.plot(times, rtts, linewidth=1)
    ax = plt.gca()
    ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
    plt.xlabel("Time since start (s)")
    plt.ylabel("RTT to POP (ms)")
    plt.title("POP Ping RTT")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    out_png = out_dir / "pop_ping_rtt.png"
    plt.savefig(out_png, dpi=200)
    print(f"[INFO] Saved: {out_png}")


if __name__ == "__main__":
    main()