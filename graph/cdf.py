#!/usr/bin/env python3
"""
plot_starlink_cdf_overlay.py

Starlink experiment CDF plotting utility.

Features
--------
1. Full-run CDF
   - Throughput from iperf3_flows*.csv
   - RTT from pop_ping*.log
   - Response interval from pop_interval*.log

2. Overlay multiple datasets in one graph
   - Example: BBR normal vs BBR rwnd
   - Example: CUBIC normal vs CUBIC rwnd

3. Handover-aligned CDF
   - Detect handover proxy points from pop_interval peak points
   - Extract samples around each peak, e.g. ±3 seconds
   - Plot CDF only for those windows

Assumed file naming
-------------------
For one dataset directory:
  Normal:
    iperf3_flowsn*.csv
    pop_pingn*.log
    pop_intervaln*.log

  rwnd/control:
    iperf3_flows*.csv
    pop_ping*.log
    pop_interval*.log

The script can handle any suffix/pattern you define.

Examples
--------
# 1) Single dataset, full CDF
python3 plot_starlink_cdf_overlay.py \
  --dataset "BBR normal:./logs:n" \
  --out-prefix bbr_normal

# 2) Overlay normal vs rwnd, full CDF
python3 plot_starlink_cdf_overlay.py \
  --dataset "BBR normal:./logs:n" \
  --dataset "BBR rwnd:./logs:" \
  --out-prefix bbr_compare

# 3) Overlay normal vs rwnd, handover-aligned ±3s
python3 plot_starlink_cdf_overlay.py \
  --dataset "BBR normal:./logs:n" \
  --dataset "BBR rwnd:./logs:" \
  --handover-aligned \
  --window-sec 3 \
  --peak-percentile 99 \
  --out-prefix bbr_handover

# Dataset format
--dataset "LABEL:DIR:SUFFIX"

SUFFIX rules:
  n      -> uses files with n suffix, e.g. pop_pingn*.log, iperf3_flowsn*.csv
  empty  -> uses files without n suffix, e.g. pop_ping[0-9]*.log, iperf3_flows[0-9]*.csv
"""

from __future__ import annotations

from pathlib import Path
import argparse
import re
from dataclasses import dataclass
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


PING_RE = re.compile(r"time=([0-9.]+)\s*ms")
NUM_RE = re.compile(r"([0-9]+(?:\.[0-9]+)?)")


@dataclass
class DatasetSpec:
    label: str
    directory: Path
    suffix: str


@dataclass
class DatasetFiles:
    flows: List[Path]
    pings: List[Path]
    intervals: List[Path]


def parse_dataset_arg(raw: str) -> DatasetSpec:
    """
    Parse --dataset "LABEL:DIR:SUFFIX".

    The final suffix field may be empty:
      "BBR rwnd:/path/to/logs:"
    """
    parts = raw.split(":")
    if len(parts) < 3:
        raise ValueError(
            f"Invalid dataset spec: {raw}\n"
            'Expected format: "LABEL:DIR:SUFFIX", e.g. "BBR normal:./logs:n"'
        )

    label = parts[0]
    suffix = parts[-1]
    directory = ":".join(parts[1:-1])

    if not label:
        raise ValueError(f"Dataset label is empty: {raw}")
    if not directory:
        raise ValueError(f"Dataset directory is empty: {raw}")

    return DatasetSpec(label=label, directory=Path(directory), suffix=suffix)


def discover_files(spec: DatasetSpec) -> DatasetFiles:
    base = spec.directory

    if spec.suffix:
        flows = sorted(base.glob(f"iperf3_flows{spec.suffix}*.csv"))
        pings = sorted(base.glob(f"pop_ping{spec.suffix}*.log"))
        intervals = sorted(base.glob(f"pop_interval{spec.suffix}*.log"))
    else:
        # Avoid matching n-files when suffix is empty.
        flows = sorted(base.glob("iperf3_flows[0-9]*.csv"))
        pings = sorted(base.glob("pop_ping[0-9]*.log"))
        intervals = sorted(base.glob("pop_interval[0-9]*.log"))

    return DatasetFiles(flows=flows, pings=pings, intervals=intervals)


def cdf(values: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if len(values) == 0:
        return np.array([]), np.array([])
    x = np.sort(values)
    y = np.arange(1, len(x) + 1) / len(x)
    return x, y


def choose_throughput_column(df: pd.DataFrame) -> str:
    candidates = [
        c for c in df.columns
        if any(k in c.lower() for k in ["throughput", "bps", "bitrate", "bandwidth"])
    ]

    if candidates:
        return candidates[0]

    numeric_cols = df.select_dtypes(include=[np.number]).columns
    if len(numeric_cols) == 0:
        raise ValueError("No numeric column found in throughput CSV")

    # Many converted iperf CSVs put throughput as the final numeric column.
    return numeric_cols[-1]


def parse_flow_file(path: Path) -> np.ndarray:
    df = pd.read_csv(path)
    col = choose_throughput_column(df)

    values = pd.to_numeric(df[col], errors="coerce").dropna().to_numpy(dtype=float)

    # If values look like bps, convert to Mbps.
    if len(values) > 0 and np.nanmedian(values) > 1e5:
        values = values / 1e6

    return values


def parse_flow_files(files: List[Path]) -> np.ndarray:
    values: List[float] = []

    for path in files:
        try:
            arr = parse_flow_file(path)
            values.extend(arr.tolist())
        except Exception as e:
            print(f"[WARN] failed to parse flow CSV {path}: {e}")

    return np.asarray(values, dtype=float)


def parse_ping_file(path: Path) -> np.ndarray:
    values: List[float] = []

    with open(path, "r", errors="ignore") as f:
        for line in f:
            m = PING_RE.search(line)
            if m:
                values.append(float(m.group(1)))

    return np.asarray(values, dtype=float)


def parse_ping_files(files: List[Path]) -> np.ndarray:
    values: List[float] = []

    for path in files:
        try:
            arr = parse_ping_file(path)
            values.extend(arr.tolist())
        except Exception as e:
            print(f"[WARN] failed to parse ping log {path}: {e}")

    return np.asarray(values, dtype=float)


def parse_interval_file(path: Path) -> np.ndarray:
    values: List[float] = []

    with open(path, "r", errors="ignore") as f:
        for line in f:
            nums = NUM_RE.findall(line)
            if nums:
                # Use the last numeric token in each line.
                v = float(nums[-1])
                if 0 < v < 10000:
                    values.append(v)

    return np.asarray(values, dtype=float)


def parse_interval_files(files: List[Path]) -> np.ndarray:
    values: List[float] = []

    for path in files:
        try:
            arr = parse_interval_file(path)
            values.extend(arr.tolist())
        except Exception as e:
            print(f"[WARN] failed to parse interval log {path}: {e}")

    return np.asarray(values, dtype=float)


def local_peak_indices(values: np.ndarray, percentile: float, min_gap: int) -> np.ndarray:
    """
    Detect interval peaks above percentile threshold.

    Consecutive samples above threshold are collapsed into one peak index
    by picking the maximum point within each run. Then nearby peaks within
    min_gap samples are merged.
    """
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return np.array([], dtype=int)

    threshold = np.percentile(values, percentile)
    candidates = np.where(values >= threshold)[0]

    if len(candidates) == 0:
        return np.array([], dtype=int)

    # Collapse consecutive candidate runs.
    peaks = []
    run = [candidates[0]]

    for idx in candidates[1:]:
        if idx == run[-1] + 1:
            run.append(idx)
        else:
            run_arr = np.asarray(run)
            peak = run_arr[np.argmax(values[run_arr])]
            peaks.append(int(peak))
            run = [idx]

    run_arr = np.asarray(run)
    peak = run_arr[np.argmax(values[run_arr])]
    peaks.append(int(peak))

    # Merge nearby peaks.
    merged = []
    for p in peaks:
        if not merged:
            merged.append(p)
            continue

        if p - merged[-1] <= min_gap:
            # Keep the larger peak.
            if values[p] > values[merged[-1]]:
                merged[-1] = p
        else:
            merged.append(p)

    return np.asarray(merged, dtype=int)


def pair_by_index(files_a: List[Path], files_b: List[Path]) -> List[Tuple[Path, Path]]:
    """
    Pair files by sorted order.

    This works for file sets like:
      pop_intervaln1.log <-> iperf3_flowsn1.csv
      pop_intervaln2.log <-> iperf3_flowsn2.csv
    """
    n = min(len(files_a), len(files_b))
    return list(zip(files_a[:n], files_b[:n]))


def estimate_samples_per_second(intervals: np.ndarray, fallback: int = 100) -> int:
    """
    The pop_interval logs often come from 10ms probes, so about 100 samples/s.
    Since file format may not include timestamps, use fallback by default.
    """
    return fallback


def extract_handover_throughput(
    interval_files: List[Path],
    flow_files: List[Path],
    peak_percentile: float,
    window_sec: float,
    interval_hz: int,
    flow_hz: int,
    min_gap_sec: float,
) -> np.ndarray:
    """
    Detect interval peaks and collect throughput samples within ±window_sec.

    Assumption:
      interval log and throughput CSV are time-aligned within each run.
      If sampling rates differ, peak position is mapped by normalized file position.
    """
    collected: List[float] = []

    for interval_path, flow_path in pair_by_index(interval_files, flow_files):
        intervals = parse_interval_file(interval_path)
        flows = parse_flow_file(flow_path)

        if len(intervals) == 0 or len(flows) == 0:
            continue

        min_gap = max(1, int(min_gap_sec * interval_hz))
        peaks = local_peak_indices(intervals, peak_percentile, min_gap=min_gap)

        for peak_idx in peaks:
            ratio = peak_idx / max(len(intervals) - 1, 1)
            flow_idx = int(ratio * (len(flows) - 1))

            half_window = int(round(window_sec * flow_hz))
            start = max(0, flow_idx - half_window)
            end = min(len(flows), flow_idx + half_window + 1)

            collected.extend(flows[start:end].tolist())

    return np.asarray(collected, dtype=float)


def extract_handover_rtt(
    interval_files: List[Path],
    ping_files: List[Path],
    peak_percentile: float,
    window_sec: float,
    interval_hz: int,
    ping_hz: int,
    min_gap_sec: float,
) -> np.ndarray:
    """
    Detect interval peaks and collect RTT samples within ±window_sec.

    Since ping and interval logs may not have explicit timestamps, this maps
    peak position to ping position by normalized file position.
    """
    collected: List[float] = []

    for interval_path, ping_path in pair_by_index(interval_files, ping_files):
        intervals = parse_interval_file(interval_path)
        rtts = parse_ping_file(ping_path)

        if len(intervals) == 0 or len(rtts) == 0:
            continue

        min_gap = max(1, int(min_gap_sec * interval_hz))
        peaks = local_peak_indices(intervals, peak_percentile, min_gap=min_gap)

        for peak_idx in peaks:
            ratio = peak_idx / max(len(intervals) - 1, 1)
            rtt_idx = int(ratio * (len(rtts) - 1))

            half_window = int(round(window_sec * ping_hz))
            start = max(0, rtt_idx - half_window)
            end = min(len(rtts), rtt_idx + half_window + 1)

            collected.extend(rtts[start:end].tolist())

    return np.asarray(collected, dtype=float)


def plot_overlay(
    series: Dict[str, np.ndarray],
    xlabel: str,
    title: str,
    out_path: Path,
    xlim: Tuple[float | None, float | None] | None = None,
) -> None:
    plt.figure(figsize=(9, 6))

    plotted = False
    for label, values in series.items():
        x, y = cdf(values)
        if len(x) == 0:
            print(f"[WARN] no samples for {label} in {title}")
            continue
        plt.plot(x, y, label=label)
        plotted = True

    plt.xlabel(xlabel)
    plt.ylabel("CDF")
    plt.title(title)
    plt.grid(True)

    if xlim is not None:
        plt.xlim(*xlim)

    if plotted:
        plt.legend()

    plt.savefig(out_path, bbox_inches="tight")
    plt.close()
    print(f"[OK] wrote {out_path}")


def summarize_values(label: str, metric: str, values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]

    row = {
        "dataset": label,
        "metric": metric,
        "samples": len(values),
    }

    if len(values) == 0:
        row.update({
            "mean": np.nan,
            "median": np.nan,
            "p10": np.nan,
            "p90": np.nan,
            "p99": np.nan,
            "max": np.nan,
        })
    else:
        row.update({
            "mean": float(np.mean(values)),
            "median": float(np.median(values)),
            "p10": float(np.percentile(values, 10)),
            "p90": float(np.percentile(values, 90)),
            "p99": float(np.percentile(values, 99)),
            "max": float(np.max(values)),
        })

    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        action="append",
        required=True,
        help='Dataset spec "LABEL:DIR:SUFFIX". Example: "BBR normal:./logs:n" or "BBR rwnd:./logs:"',
    )
    parser.add_argument("--out-prefix", default="starlink", help="output filename prefix")
    parser.add_argument("--out-dir", default=".", help="output directory")
    parser.add_argument("--handover-aligned", action="store_true", help="use pop_interval peak ±window samples")
    parser.add_argument("--window-sec", type=float, default=3.0, help="handover-aligned half-window in seconds")
    parser.add_argument("--peak-percentile", type=float, default=99.0, help="pop_interval peak threshold percentile")
    parser.add_argument("--interval-hz", type=int, default=100, help="pop_interval samples per second")
    parser.add_argument("--ping-hz", type=int, default=100, help="pop_ping samples per second")
    parser.add_argument("--flow-hz", type=int, default=1, help="throughput CSV samples per second")
    parser.add_argument("--min-gap-sec", type=float, default=5.0, help="minimum seconds between detected handover peaks")
    parser.add_argument("--xmax-throughput", type=float, default=None, help="optional throughput x-axis max")
    parser.add_argument("--xmax-rtt", type=float, default=None, help="optional RTT x-axis max")
    parser.add_argument("--xmax-interval", type=float, default=None, help="optional interval x-axis max")

    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    specs = [parse_dataset_arg(raw) for raw in args.dataset]

    throughput_series: Dict[str, np.ndarray] = {}
    rtt_series: Dict[str, np.ndarray] = {}
    interval_series: Dict[str, np.ndarray] = {}
    summary_rows = []

    for spec in specs:
        files = discover_files(spec)

        print(f"\n[INFO] Dataset: {spec.label}")
        print(f"  directory: {spec.directory}")
        print(f"  suffix: {spec.suffix!r}")
        print(f"  flow files: {len(files.flows)}")
        print(f"  ping files: {len(files.pings)}")
        print(f"  interval files: {len(files.intervals)}")

        if args.handover_aligned:
            throughput = extract_handover_throughput(
                files.intervals,
                files.flows,
                peak_percentile=args.peak_percentile,
                window_sec=args.window_sec,
                interval_hz=args.interval_hz,
                flow_hz=args.flow_hz,
                min_gap_sec=args.min_gap_sec,
            )
            rtt = extract_handover_rtt(
                files.intervals,
                files.pings,
                peak_percentile=args.peak_percentile,
                window_sec=args.window_sec,
                interval_hz=args.interval_hz,
                ping_hz=args.ping_hz,
                min_gap_sec=args.min_gap_sec,
            )
            # For interval itself, use all peak-neighborhood interval samples.
            # This is useful when you want to compare the handover proxy distribution.
            interval = parse_interval_files(files.intervals)
        else:
            throughput = parse_flow_files(files.flows)
            rtt = parse_ping_files(files.pings)
            interval = parse_interval_files(files.intervals)

        throughput_series[spec.label] = throughput
        rtt_series[spec.label] = rtt
        interval_series[spec.label] = interval

        summary_rows.append(summarize_values(spec.label, "throughput_Mbps", throughput))
        summary_rows.append(summarize_values(spec.label, "rtt_ms", rtt))
        summary_rows.append(summarize_values(spec.label, "interval", interval))

    mode = "handover_aligned" if args.handover_aligned else "full"

    throughput_title = "Throughput CDF"
    rtt_title = "RTT CDF"
    interval_title = "POP Interval CDF"

    if args.handover_aligned:
        throughput_title += f" around POP interval peaks (±{args.window_sec:g}s)"
        rtt_title += f" around POP interval peaks (±{args.window_sec:g}s)"
        interval_title += " full distribution"

    plot_overlay(
        throughput_series,
        xlabel="Throughput (Mbps)",
        title=throughput_title,
        out_path=out_dir / f"{args.out_prefix}_{mode}_throughput_cdf.png",
        xlim=(None, args.xmax_throughput),
    )
    plot_overlay(
        rtt_series,
        xlabel="RTT (ms)",
        title=rtt_title,
        out_path=out_dir / f"{args.out_prefix}_{mode}_rtt_cdf.png",
        xlim=(None, args.xmax_rtt),
    )
    plot_overlay(
        interval_series,
        xlabel="Response Interval",
        title=interval_title,
        out_path=out_dir / f"{args.out_prefix}_{mode}_interval_cdf.png",
        xlim=(None, args.xmax_interval),
    )

    summary = pd.DataFrame(summary_rows)
    summary_path = out_dir / f"{args.out_prefix}_{mode}_summary.csv"
    summary.to_csv(summary_path, index=False)
    print(f"[OK] wrote {summary_path}")
    print(summary)


if __name__ == "__main__":
    main()