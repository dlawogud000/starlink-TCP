#!/usr/bin/env python3

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def main():

    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <experiment_dir>")
        sys.exit(1)

    exp_dir = Path(sys.argv[1])

    csv_file = exp_dir / "iperf3_flows.csv"

    if not csv_file.exists():
        print(f"ERROR: {csv_file} not found")
        sys.exit(1)

    df = pd.read_csv(csv_file)

    if "flow_id" not in df.columns:
        print("ERROR: flow_id column not found")
        sys.exit(1)

    if "throughput_mbps" not in df.columns:
        print("ERROR: throughput_mbps column not found")
        sys.exit(1)

    plt.figure(figsize=(10, 6))

    summary_rows = []

    for flow_id, group in df.groupby("flow_id"):

        values = (
            pd.to_numeric(
                group["throughput_mbps"],
                errors="coerce"
            )
            .dropna()
            .values
        )

        if len(values) == 0:
            continue

        x = np.sort(values)
        y = np.arange(1, len(x) + 1) / len(x)

        plt.plot(
            x,
            y,
            linewidth=2,
            label=str(flow_id)
        )

        summary_rows.append({
            "flow_id": flow_id,
            "samples": len(values),
            "mean_mbps": np.mean(values),
            "median_mbps": np.median(values),
            "p10_mbps": np.percentile(values, 10),
            "p90_mbps": np.percentile(values, 90),
            "p99_mbps": np.percentile(values, 99),
            "max_mbps": np.max(values),
        })

    plt.xlabel("Throughput (Mbps)")
    plt.ylabel("CDF")
    plt.title("Per-Flow Throughput CDF")
    plt.grid(True)
    plt.legend()
    plt.tight_layout()

    png_file = exp_dir / "per_flow_cdf.png"

    plt.savefig(
        png_file,
        dpi=150,
        bbox_inches="tight"
    )

    plt.close()

    summary_df = pd.DataFrame(summary_rows)

    summary_file = exp_dir / "per_flow_summary.csv"

    summary_df.to_csv(
        summary_file,
        index=False
    )

    print(f"[OK] Saved: {png_file}")
    print(f"[OK] Saved: {summary_file}")


if __name__ == "__main__":
    main()