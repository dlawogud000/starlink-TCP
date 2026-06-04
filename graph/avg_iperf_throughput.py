#!/usr/bin/env python3
import json
import sys
from pathlib import Path
from statistics import mean

def sample_covariance(xs, ys):
    """
    sample covariance: sum((x-x_mean)(y-y_mean)) / (n-1)
    """
    n = len(xs)
    if n < 2:
        return None

    x_mean = mean(xs)
    y_mean = mean(ys)

    return sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys)) / (n - 1)

if len(sys.argv) < 2:
    print(f"Usage: {sys.argv[0]} <exp_dir1> <exp_dir2> ...")
    sys.exit(1)

throughputs_mbps = []
valid_names = []

for exp_dir in sys.argv[1:]:
    exp_path = Path(exp_dir)
    iperf_path = exp_path / "iperf.json"

    if not iperf_path.exists():
        print(f"[WARN] Missing iperf.json: {iperf_path}")
        continue

    try:
        with open(iperf_path, "r") as f:
            data = json.load(f)

        # iperf3 전체 평균 throughput
        bps = data["end"]["sum_received"]["bits_per_second"]
        mbps = bps / 1_000_000

        throughputs_mbps.append(mbps)
        valid_names.append(exp_path.name)

        print(f"{exp_path.name}: {mbps:.3f} Mbps")

    except KeyError:
        print(f"[WARN] sum_received not found in {iperf_path}")
    except Exception as e:
        print(f"[ERROR] Failed to parse {iperf_path}: {e}")

if throughputs_mbps:
    avg_mbps = mean(throughputs_mbps)

    # 실험 순서 index: 1, 2, 3, ...
    indices = list(range(1, len(throughputs_mbps) + 1))

    cov_index_thr = sample_covariance(indices, throughputs_mbps)
    cov_thr_thr = sample_covariance(throughputs_mbps, throughputs_mbps)

    print()
    print("================================")
    print(f"Number of valid experiments: {len(throughputs_mbps)}")
    print(f"Average throughput: {avg_mbps:.3f} Mbps")

    if cov_index_thr is not None:
        print(f"Covariance(index, throughput): {cov_index_thr:.6f}")
        print(f"Covariance(throughput, throughput): {cov_thr_thr:.6f}")
    else:
        print("Covariance: not enough data")

    print("================================")
else:
    print("[ERROR] No valid throughput data found.")
