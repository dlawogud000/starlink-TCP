import json
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


def load_iperf_intervals(path: Path):
    """
    iperf.json에서 interval별 throughput을 읽음.

    반환:
    rows = [
        (start, end, throughput_mbps),
        ...
    ]
    """
    rows = []

    if not path.exists():
        print(f"[WARN] Missing {path}")
        return rows

    with path.open("r", errors="ignore") as f:
        data = json.load(f)

    intervals = data.get("intervals", [])

    for item in intervals:
        sum_data = item.get("sum", {})

        start = sum_data.get("start")
        end = sum_data.get("end")
        bps = sum_data.get("bits_per_second")

        if start is None or end is None or bps is None:
            continue

        start = float(start)
        end = float(end)
        mbps = float(bps) / 1_000_000

        rows.append((start, end, mbps))

    return rows


def normalize(ts, t0):
    return [x - t0 for x in ts]


def pearson_corr(x, y):
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


def find_containing_iperf_throughput(t, iperf_rows):
    """
    t를 포함하는 iperf interval의 throughput을 찾는다.

    예:
    t = 5.8
    iperf interval = 5.0~6.0
    선택됨.
    """
    for start, end, mbps in iperf_rows:
        if start <= t < end:
            return mbps, start, end

    return None, None, None


def align_pop_max_with_containing_iperf(
    pop_times,
    pop_values,
    iperf_rows,
    bin_size=1.0
):
    """
    POP interval을 1초 bin 단위로 묶는다.

    각 bin에서:
    1. POP interval max 값
    2. 그 max가 발생한 시간
    을 찾는다.

    throughput은 POP max 발생 시각을 포함하는 iperf interval의 값을 사용한다.
    """
    pop_bins = {}

    for t, v in zip(pop_times, pop_values):
        b = int(t / bin_size)
        pop_bins.setdefault(b, []).append((t, v))

    aligned_times = []
    aligned_pop_max = []
    aligned_thr = []
    aligned_iperf_ranges = []

    for b, vals in sorted(pop_bins.items()):
        if not vals:
            continue

        # 해당 1초 bin 안에서 interval이 가장 큰 순간
        pop_max_time, pop_max_value = max(vals, key=lambda x: x[1])

        # POP max 시각을 포함하는 iperf interval
        thr, start, end = find_containing_iperf_throughput(
            pop_max_time,
            iperf_rows
        )

        if thr is None:
            continue

        aligned_times.append(pop_max_time)
        aligned_pop_max.append(pop_max_value)
        aligned_thr.append(thr)
        aligned_iperf_ranges.append((start, end))

    return aligned_times, aligned_pop_max, aligned_thr, aligned_iperf_ranges


def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <EXP_DIR> <RESULT_DIR>")
        sys.exit(1)

    exp_dir = Path(sys.argv[1])
    result_dir = Path(sys.argv[2])
    result_dir.mkdir(parents=True, exist_ok=True)

    exp_name = exp_dir.name

    pop_log = exp_dir / "pop_interval.log"
    iperf_json = exp_dir / "iperf.json"

    pop_ts, pop_intervals = load_pop_interval(pop_log)
    iperf_rows = load_iperf_intervals(iperf_json)

    if not pop_ts:
        print("[ERROR] No pop interval data")
        sys.exit(1)

    if not iperf_rows:
        print("[ERROR] No iperf throughput data")
        sys.exit(1)

    # pop_interval은 epoch timestamp이고,
    # iperf time은 실험 시작 후 상대시간이므로
    # pop_interval은 첫 timestamp 기준으로 0초부터 맞춤
    pop_x = normalize(pop_ts, pop_ts[0])

    iperf_mid_times = [
        (start + end) / 2.0
        for start, end, _ in iperf_rows
    ]
    throughput_mbps = [
        mbps
        for _, _, mbps in iperf_rows
    ]

    # 그래프 저장
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)

    axes[0].plot(pop_x, pop_intervals, linewidth=1)
    axes[0].set_ylabel("POP interval (s)")
    axes[0].set_title("POP Ping Response Interval")
    axes[0].grid(True, which="major", alpha=0.3)
    axes[0].grid(True, which="minor", alpha=0.1)

    axes[1].plot(iperf_mid_times, throughput_mbps, linewidth=1)
    axes[1].set_ylabel("Throughput (Mbps)")
    axes[1].set_title("iperf Throughput")
    axes[1].set_xlabel("Time since experiment start (s)")
    axes[1].grid(True, which="major", alpha=0.3)
    axes[1].grid(True, which="minor", alpha=0.1)

    for ax in axes:
        ax.xaxis.set_major_locator(ticker.MultipleLocator(10))
        ax.xaxis.set_minor_locator(ticker.MultipleLocator(1))

    plt.tight_layout()

    out_png = result_dir / f"combined_{exp_name}.png"
    plt.savefig(out_png, dpi=200)
    plt.close()

    print(f"[INFO] Saved: {out_png}")

    # 상관관계 분석
    bin_size = 1.0

    aligned_times, aligned_pop_max, aligned_thr, aligned_iperf_ranges = (
        align_pop_max_with_containing_iperf(
            pop_x,
            pop_intervals,
            iperf_rows,
            bin_size=bin_size
        )
    )

    corr_containing = pearson_corr(aligned_pop_max, aligned_thr)

    if corr_containing is not None:
        print(f"[RESULT] {exp_name} corr_containing = {corr_containing:.4f}")
    else:
        print(f"[RESULT] {exp_name} corr_containing = N/A")

    result_txt = result_dir / f"correlation_{exp_name}.txt"

    with result_txt.open("w") as f:
        f.write("Correlation analysis between POP ping interval max and containing iperf throughput\n")
        f.write("=================================================================================\n")
        f.write(f"Experiment: {exp_name}\n")
        f.write(f"Throughput source: {iperf_json}\n")
        f.write("Throughput unit: Mbps\n")
        f.write(f"POP interval source: {pop_log}\n")
        f.write("POP interval unit: seconds\n")
        f.write(f"Bin size: {bin_size} s\n")
        f.write(f"Number of aligned samples: {len(aligned_pop_max)}\n\n")

        if corr_containing is not None:
            f.write(
                "Pearson correlation: "
                f"POP interval max vs containing iperf throughput = {corr_containing:.4f}\n"
            )
        else:
            f.write(
                "Pearson correlation: "
                "POP interval max vs containing iperf throughput = N/A\n"
            )

    print(f"[INFO] Saved: {result_txt}")


if __name__ == "__main__":
    main()