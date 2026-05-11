import re
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker


def load_pop_interval(path: Path):
    ts = []
    intervals = []

    if not path.exists():
        print(f"[WARN] Missing {path}")
        return ts, intervals

    with path.open("r", errors="ignore") as f:
        for line in f:
            parts = line.strip().split()
            if len(parts) != 2:
                continue

            try:
                ts.append(float(parts[0]))
                intervals.append(float(parts[1]))
            except ValueError:
                continue

    return ts, intervals


def load_ss_bytes(path: Path, direction: str):
    ts = []
    byte_values = []

    if not path.exists():
        print(f"[WARN] Missing {path}")
        return ts, byte_values

    if direction == "uplink":
        byte_key = "bytes_sent"
    elif direction == "downlink":
        byte_key = "bytes_received"
    else:
        raise ValueError("direction must be uplink or downlink")

    pattern_ts = re.compile(r"^([0-9]+\.[0-9]+)")
    pattern_bytes = re.compile(rf"{byte_key}=([0-9]+)")

    with path.open("r", errors="ignore") as f:
        for line in f:
            line = line.strip()

            if not line or "no_data_connection" in line or "no_connection" in line:
                continue

            m_ts = pattern_ts.search(line)
            m_bytes = pattern_bytes.search(line)

            if not m_ts or not m_bytes:
                continue

            ts.append(float(m_ts.group(1)))
            byte_values.append(int(m_bytes.group(1)))

    return ts, byte_values


def calc_throughput_mbps(ts, byte_values):
    out_ts = []
    mbps = []

    for i in range(1, len(ts)):
        dt = ts[i] - ts[i - 1]
        db = byte_values[i] - byte_values[i - 1]

        if dt <= 0:
            continue

        # TCP 연결이 바뀌거나 값이 리셋된 경우 제외
        if db < 0:
            continue

        rate_mbps = db * 8 / dt / 1_000_000

        out_ts.append(ts[i])
        mbps.append(rate_mbps)

    return out_ts, mbps


def normalize(ts, t0):
    return [x - t0 for x in ts]

def bin_average(times, values, bin_size=0.1):
    """
    times: 실험 시작 후 시간 리스트
    values: 측정값 리스트
    bin_size: 시간 bin 크기, 기본 0.1초

    return:
    binned_times, binned_values
    """
    bins = {}

    for t, v in zip(times, values):
        if v is None:
            continue

        b = int(t / bin_size)
        if b not in bins:
            bins[b] = []
        bins[b].append(v)

    out_times = []
    out_values = []

    for b in sorted(bins.keys()):
        vals = bins[b]
        if not vals:
            continue

        out_times.append(b * bin_size)
        out_values.append(sum(vals) / len(vals))

    return out_times, out_values


def align_by_common_bins(pop_times, pop_values, thr_times, thr_values, bin_size=0.1):
    """
    ping interval과 throughput을 같은 시간 bin 기준으로 맞춤
    """
    pop_bins = {}
    thr_bins = {}

    for t, v in zip(pop_times, pop_values):
        b = int(t / bin_size)
        pop_bins.setdefault(b, []).append(v)

    for t, v in zip(thr_times, thr_values):
        b = int(t / bin_size)
        thr_bins.setdefault(b, []).append(v)

    common_bins = sorted(set(pop_bins.keys()) & set(thr_bins.keys()))

    aligned_pop = []
    aligned_thr = []
    aligned_times = []

    for b in common_bins:
        pop_avg = sum(pop_bins[b]) / len(pop_bins[b])
        thr_avg = sum(thr_bins[b]) / len(thr_bins[b])

        aligned_times.append(b * bin_size)
        aligned_pop.append(pop_avg)
        aligned_thr.append(thr_avg)

    return aligned_times, aligned_pop, aligned_thr


def pearson_corr(x, y):
    """
    Pearson correlation coefficient 계산
    numpy 없이 계산
    """
    if len(x) != len(y) or len(x) < 2:
        return None

    mean_x = sum(x) / len(x)
    mean_y = sum(y) / len(y)

    num = 0.0
    den_x = 0.0
    den_y = 0.0

    for xi, yi in zip(x, y):
        dx = xi - mean_x
        dy = yi - mean_y

        num += dx * dy
        den_x += dx * dx
        den_y += dy * dy

    if den_x == 0 or den_y == 0:
        return None

    return num / ((den_x ** 0.5) * (den_y ** 0.5))


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <OUT_DIR> <direction:uplink|downlink>")
        sys.exit(1)

    out_dir = Path(sys.argv[1])
    direction = sys.argv[2]

    pop_log = out_dir / "pop_interval.log"
    ss_log = out_dir / "ss_rtt.log"

    pop_ts, pop_intervals = load_pop_interval(pop_log)
    ss_ts, ss_bytes = load_ss_bytes(ss_log, direction)

    if not pop_ts:
        print("[ERROR] No pop interval data")
        sys.exit(1)

    if not ss_ts:
        print("[ERROR] No ss byte data")
        print("[HINT] For downlink, check whether bytes_received exists in ss_rtt.log")
        sys.exit(1)

    thr_ts, throughput = calc_throughput_mbps(ss_ts, ss_bytes)

    if not thr_ts:
        print("[ERROR] No throughput data calculated")
        sys.exit(1)

    t0 = min(pop_ts[0], thr_ts[0])

    pop_x = normalize(pop_ts, t0)
    thr_x = normalize(thr_ts, t0)

    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)

    axes[0].plot(pop_x, pop_intervals, linewidth=1)
    axes[0].set_ylabel("Ping interval (s)")
    axes[0].set_title("POP Ping Response Interval")
    axes[0].grid(True, which="major", alpha=0.3)
    axes[0].grid(True, which="minor", alpha=0.1)

    axes[1].plot(thr_x, throughput, linewidth=1)
    axes[1].set_ylabel("Throughput (Mbps)")
    axes[1].set_title(f"TCP Throughput from ss log ({direction})")
    axes[1].set_xlabel("Time since experiment start (s)")
    axes[1].grid(True, which="major", alpha=0.3)
    axes[1].grid(True, which="minor", alpha=0.1)

    for ax in axes:
        ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))

    plt.tight_layout()

    out_png = out_dir / f"combined_ping_throughput_{direction}.png"
    plt.savefig(out_png, dpi=200)
    plt.close()

    print(f"[INFO] Saved: {out_png}")

    # Correlation analysis
    # pop_x: ping interval의 시간축
    # pop_intervals: ping interval 값
    # thr_x: throughput 시간축
    # throughput: Mbps 값

    bin_size = 0.1

    aligned_times, aligned_pop, aligned_thr = align_by_common_bins(
        pop_x,
        pop_intervals,
        thr_x,
        throughput,
        bin_size=bin_size
    )

    corr_interval_throughput = pearson_corr(aligned_pop, aligned_thr)

    result_txt = out_dir / f"correlation_ping_throughput_{direction}.txt"

    with result_txt.open("w") as f:
        f.write("Correlation analysis between POP ping interval and TCP throughput\n")
        f.write("================================================================\n")
        f.write(f"Direction: {direction}\n")
        f.write(f"Bin size: {bin_size} s\n")
        f.write(f"Number of aligned samples: {len(aligned_times)}\n\n")

        if corr_interval_throughput is not None:
            f.write(f"Pearson correlation: ping interval vs throughput = {corr_interval_throughput:.4f}\n")
        else:
            f.write("Pearson correlation: ping interval vs throughput = N/A\n")

    print(f"[INFO] Saved: {result_txt}")


if __name__ == "__main__":
    main()