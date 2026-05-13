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


def load_iperf_throughput(path: Path):
    """
    iperf.json에서 interval별 throughput을 읽음.
    throughput 단위는 Mbps로 변환.

    반환:
    times: iperf 시작 후 시간, 초 단위
    mbps: throughput, Mbps 단위
    """
    times = []
    mbps = []

    if not path.exists():
        print(f"[WARN] Missing {path}")
        return times, mbps

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

        # iperf interval의 중앙 시간을 사용
        t = (float(start) + float(end)) / 2.0
        rate_mbps = float(bps) / 1_000_000

        times.append(t)
        mbps.append(rate_mbps)

    return times, mbps


def normalize(ts, t0):
    return [x - t0 for x in ts]


def align_pop_max_time_with_next_iperf(
    pop_times,
    pop_values,
    thr_times,
    thr_values,
    bin_size=1.0
):
    """
    POP interval을 1초 bin 단위로 묶는다.

    각 bin 안에서 POP interval max 값을 찾고,
    그 max 값이 발생한 실제 시간 pop_max_time을 구한다.

    throughput은 pop_max_time 이후에 존재하는 iperf 샘플 중
    가장 가까운 throughput 값 1개를 사용한다.
    """
    pop_bins = {}

    for t, v in zip(pop_times, pop_values):
        b = int(t / bin_size)
        pop_bins.setdefault(b, []).append((t, v))

    aligned_times = []
    aligned_pop_max = []
    aligned_thr = []

    if not thr_times:
        return aligned_times, aligned_pop_max, aligned_thr

    for b, vals in sorted(pop_bins.items()):
        if not vals:
            continue

        # 해당 bin 안에서 POP interval max가 발생한 실제 시간과 값 찾기
        pop_max_time, pop_max_value = max(vals, key=lambda x: x[1])

        # pop_max_time 이후의 iperf 샘플만 후보로 사용
        candidate_indices = [
            i for i, t in enumerate(thr_times)
            if t >= pop_max_time
        ]

        # 이후 iperf 샘플이 없으면 해당 bin은 제외
        if not candidate_indices:
            continue

        # 이후 샘플 중 가장 가까운 iperf throughput 선택
        next_idx = min(
            candidate_indices,
            key=lambda i: thr_times[i] - pop_max_time
        )

        aligned_times.append(pop_max_time)
        aligned_pop_max.append(pop_max_value)
        aligned_thr.append(thr_values[next_idx])

    return aligned_times, aligned_pop_max, aligned_thr


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
    iperf_times, throughput_mbps = load_iperf_throughput(iperf_json)

    if not pop_ts:
        print("[ERROR] No pop interval data")
        sys.exit(1)

    if not iperf_times:
        print("[ERROR] No iperf throughput data")
        sys.exit(1)

    # pop_interval은 epoch timestamp이고,
    # iperf time은 실험 시작 후 상대시간이므로
    # pop_interval은 첫 timestamp 기준으로 0초부터 맞춤
    pop_x = normalize(pop_ts, pop_ts[0])
    thr_x = iperf_times

    # 그래프 저장
    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)

    axes[0].plot(pop_x, pop_intervals, linewidth=1)
    axes[0].set_ylabel("POP interval (s)")
    axes[0].set_title("POP Ping Response Interval")
    axes[0].grid(True, which="major", alpha=0.3)
    axes[0].grid(True, which="minor", alpha=0.1)

    axes[1].plot(thr_x, throughput_mbps, linewidth=1)
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

    aligned_times, aligned_pop_max, aligned_thr = align_pop_max_time_with_next_iperf(
        pop_x,
        pop_intervals,
        thr_x,
        throughput_mbps,
        bin_size=bin_size
    )

    corr_max = pearson_corr(aligned_pop_max, aligned_thr)
    if corr_max is not None:
        print(f"[RESULT] {exp_name} corr_max = {corr_max:.4f}")
    else:
        print(f"[RESULT] {exp_name} corr_max = N/A")
        
    result_txt = result_dir / f"correlation_{exp_name}.txt"

    with result_txt.open("w") as f:
        f.write("Correlation analysis between POP ping interval max and next iperf throughput\n")
        f.write("===========================================================================\n")
        f.write(f"Experiment: {exp_name}\n")
        f.write(f"Throughput source: {iperf_json}\n")
        f.write("Throughput unit: Mbps\n")
        f.write(f"POP interval source: {pop_log}\n")
        f.write("POP interval unit: seconds\n")
        f.write(f"Bin size: {bin_size} s\n")
        f.write(f"Number of aligned samples: {len(aligned_times)}\n\n")

        if corr_max is not None:
            f.write(
                "Pearson correlation: "
                f"POP interval max vs next iperf throughput = {corr_max:.4f}\n"
            )
        else:
            f.write(
                "Pearson correlation: "
                "POP interval max vs next iperf throughput = N/A\n"
            )

    print(f"[INFO] Saved: {result_txt}")


if __name__ == "__main__":
    main()