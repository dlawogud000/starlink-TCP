#!/usr/bin/env python3
import sys
from pathlib import Path
import matplotlib as mpl
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
    mpl.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "Nimbus Roman"],
    "mathtext.fontset": "stix",
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "font.size": 14,
    })

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

    # START_TIME = 100
    # MAX_TIME = 120
    t0 = ts[0]
    times = [t - t0 for t in ts]
    # times = [t for t in times if t <= MAX_TIME]
    intervals = intervals[:len(times)]

    plt.figure(figsize=(max(times)/20, 4))
    plt.plot(times, intervals, linewidth=1)
    #plt.scatter(times, intervals, s=5)

    ax = plt.gca()
    ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
    ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))

    # max_time = MAX_TIME
    # for t in range(10, int(max_time) + 1, 15):
    #     if t == 115 : plt.axvline(x=t, color="red", linestyle="--", linewidth=1,  alpha=0.7, label="Expected Handover Events")
    #     else : plt.axvline(x=t, color="red", linestyle="--", linewidth=1,  alpha=0.7)

    plt.xlabel("Time since start (s)", fontsize=18)
    plt.ylabel("Response interval (s)", fontsize=18)
    plt.tick_params(axis="both", labelsize=16)

    plt.grid(True, which="major", alpha=0.3)
    plt.grid(True, which="minor", alpha=0.1)
    plt.legend(fontsize=18, loc="upper right")

    plt.tight_layout()

    out_png = out_dir / "pop_interval.pdf"
    plt.savefig(out_png, dpi=200, bbox_inches="tight", pad_inches=0.02)

    print(f"[INFO] Saved: {out_png}")


if __name__ == "__main__":
    main()