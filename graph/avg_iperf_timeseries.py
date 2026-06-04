#!/usr/bin/env python3
import json
import sys
import math
from pathlib import Path
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


BIN_SIZE = 1.0  # seconds


def load_iperf_samples_by_file(exp_dir: Path):
    """
    One experiment folder -> per-1-second-bin average throughput.

    Step 1:
    Map each iperf interval to a 1-second bin using midpoint time.
    Then average samples inside the same bin within this experiment.
    """
    iperf_path = exp_dir / "iperf.json"

    if not iperf_path.exists():
        print(f"[WARN] Missing iperf.json: {iperf_path}")
        return {}

    try:
        with iperf_path.open("r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"[WARN] Failed to read {iperf_path}: {e}")
        return {}

    file_bins = defaultdict(list)

    for interval in data.get("intervals", []):
        sum_data = interval.get("sum", {})

        start = sum_data.get("start")
        end = sum_data.get("end")
        bps = sum_data.get("bits_per_second")

        if start is None or end is None or bps is None:
            continue

        start = float(start)
        end = float(end)

        if end <= start:
            continue

        # 중앙 시각 기준으로 1초 구간에 매핑
        mid = (start + end) / 2.0
        bin_time = math.floor(mid / BIN_SIZE) * BIN_SIZE

        mbps = float(bps) / 1_000_000
        file_bins[bin_time].append(mbps)

    # 각 파일 내부에서 1초 bin별 평균
    file_bin_avg = {
        t: sum(values) / len(values)
        for t, values in file_bins.items()
        if values
    }

    return file_bin_avg


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <exp_folder1> <exp_folder2> ...")
        sys.exit(1)

    base_logs = Path("~/git/starlink-TCP/logs").expanduser()

    # Step 2에서 사용할 전체 bin 저장소
    # key: bin time, value: [experiment1_avg, experiment2_avg, ...]
    across_files = defaultdict(list)

    valid_count = 0

    for name in sys.argv[1:]:
        exp_dir = Path(name)

        # 폴더명만 넣은 경우 logs 아래에서 찾기
        if not exp_dir.exists():
            exp_dir = base_logs / name

        if not exp_dir.exists():
            print(f"[WARN] Missing experiment folder: {exp_dir}")
            continue

        file_bin_avg = load_iperf_samples_by_file(exp_dir)

        if not file_bin_avg:
            print(f"[WARN] No throughput data: {exp_dir.name}")
            continue

        valid_count += 1
        print(f"[INFO] Loaded {exp_dir.name}: {len(file_bin_avg)} one-second bins")

        # Step 2:
        # 같은 1초 bin에 대해 파일별 평균값을 모음
        for t, avg_mbps in file_bin_avg.items():
            across_files[t].append(avg_mbps)

    if not across_files:
        print("[ERROR] No valid throughput data found.")
        sys.exit(1)

    times = sorted(across_files.keys())

    # 모든 파일에서 같은 초당 bin끼리 평균
    avg_throughputs = [
        sum(across_files[t]) / len(across_files[t])
        for t in times
    ]

    plt.figure(figsize=(12, 4))
    plt.plot(times, avg_throughputs, linewidth=1.5)

    plt.xlabel("Time (s)")
    plt.ylabel("Average Throughput (Mbps)")
    plt.title(f"Average iperf Throughput Across Experiments, n={valid_count}")
    plt.ylim(100,350)

    plt.grid(True, alpha=0.3)
    plt.tight_layout()

    out_png = Path.cwd() / "avg_iperf_throughput_timeseries.png"
    plt.savefig(out_png, dpi=200)

    out_txt = Path.cwd() / "avg_iperf_throughput_timeseries.txt"
    with out_txt.open("w") as f:
        f.write("time_s avg_throughput_mbps experiment_count\n")
        for t, avg in zip(times, avg_throughputs):
            f.write(f"{t:.3f} {avg:.6f} {len(across_files[t])}\n")

    overall_avg = sum(avg_throughputs) / len(avg_throughputs)

    print()
    print("================================")
    print(f"Valid experiments: {valid_count}")
    print(f"Bin size: {BIN_SIZE:.1f} s")
    print("Method: per-file 1-second average, then average across files")
    print(f"Average throughput over averaged 1-second bins: {overall_avg:.3f} Mbps")
    print(f"Saved graph: {out_png}")
    print(f"Saved data : {out_txt}")
    print("================================")


if __name__ == "__main__":
    main()
