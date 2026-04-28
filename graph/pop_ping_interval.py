#!/usr/bin/env python3
import sys
from pathlib import Path
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


def load_data(path):
    ts = []
    intervals = []

    with open(path, "r") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 2:
                continue
            ts.append(float(parts[0]))
            intervals.append(float(parts[1]))

    return ts, intervals


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <OUT_DIR>")
        sys.exit(1)

    out_dir = Path(sys.argv[1])
    log_path = out_dir / "pop_interval.log"

    if not log_path.exists():
        print(f"[ERROR] Missing file: {log_path}")
        sys.exit(1)

    ts, intervals = load_data(log_path)

    if not ts:
        print("[ERROR] No data")
        sys.exit(1)

    t0 = ts[0]
    times = [t - t0 for t in ts]

    plt.figure(figsize=(12, 4))
    plt.plot(times, intervals, linewidth=1)
    #plt.scatter(times, intervals, s=5)

    ax = plt.gca()
    ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))

    plt.xlabel("Time since start (s)")
    plt.ylabel("Response interval (s)")
    plt.title("POP Ping Response Interval")

    plt.grid(True, which="major", alpha=0.3)
    plt.grid(True, which="minor", alpha=0.1)

    plt.tight_layout()

    out_png = out_dir / "pop_interval.png"
    plt.savefig(out_png, dpi=200)

    print(f"[INFO] Saved: {out_png}")


if __name__ == "__main__":
    main()