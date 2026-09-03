#!/usr/bin/env python3
import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


def pct(x, p):
    x = np.asarray(x, dtype=float)
    x = x[np.isfinite(x)]
    return float(np.percentile(x, p)) if len(x) else float('nan')


def load_monitor(path: Path):
    df = pd.read_csv(path)
    req = {"wall_time", "record"}
    miss = req - set(df.columns)
    if miss:
        raise ValueError(f"missing columns: {sorted(miss)}")
    for c in ["wall_time", "event_epoch", "gap_ms", "threshold_ms", "support",
              "period_s", "score", "fit_ratio", "p95_residual_ms", "period_ms", "offset_ms"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def parse_pop_interval(path: Path, percentile=99.0, min_threshold_ms=40.0, merge_gap_ms=500.0):
    rows = []
    with path.open("r", errors="ignore") as f:
        for line in f:
            p = line.split()
            if len(p) < 2:
                continue
            try:
                rows.append((float(p[0]), float(p[1])))
            except ValueError:
                pass
    if not rows:
        return np.array([])
    ts = np.asarray([x[0] for x in rows])
    ints = np.asarray([x[1] for x in rows])
    th = max(np.percentile(ints, percentile), min_threshold_ms / 1000.0)
    normal = ints[ints < th]
    normal_med = np.median(normal) if len(normal) else np.median(ints)
    idx = np.where(ints >= th)[0]
    raw = ts[idx] - ints[idx] + normal_med
    out = []
    for t in raw:
        if not out or t - out[-1] > merge_gap_ms / 1000.0:
            out.append(float(t))
    return np.asarray(out)


def load_reference_events(path: Path):
    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)
        for col in ["event_epoch", "event_time", "timestamp", "time"]:
            if col in df.columns:
                vals = pd.to_numeric(df[col], errors="coerce").dropna().to_numpy(float)
                return np.sort(vals)
        raise ValueError("reference CSV must contain event_epoch/event_time/timestamp/time")
    return parse_pop_interval(path)


def nearest_errors(test_events, ref_events, tol_s):
    if len(test_events) == 0 or len(ref_events) == 0:
        return [], 0, 0, 0
    matched_ref = set()
    errors = []
    tp = 0
    for t in test_events:
        j = int(np.argmin(np.abs(ref_events - t)))
        e = float(t - ref_events[j])
        if abs(e) <= tol_s and j not in matched_ref:
            tp += 1
            matched_ref.add(j)
            errors.append(e)
    fp = len(test_events) - tp
    fn = len(ref_events) - tp
    return errors, tp, fp, fn


def summarize(df, reference_period=None, ref_events=None, match_tol_ms=200.0):
    rows = []
    if df.empty:
        raise ValueError("empty monitor log")
    start = float(df.wall_time.min())
    end = float(df.wall_time.max())
    duration = end - start

    candidates = df[df.record == "candidate"].copy()
    fits = df[df.record == "fit"].copy()
    activ = df[df.record == "activate"].copy()
    resync = df[df.record == "resync"].copy()
    disab = df[df.record == "disable"].copy()

    summary = {
        "duration_s": duration,
        "candidate_count": len(candidates),
        "fit_count": len(fits),
        "activate_count": len(activ),
        "resync_count": len(resync),
        "disable_count": len(disab),
        "final_state": str(df.iloc[-1].get("state", "")),
        "activation_delay_s": float(activ.wall_time.iloc[0] - start) if len(activ) else np.nan,
    }

    if len(fits):
        summary.update({
            "final_fit_period_s": float(fits.period_s.dropna().iloc[-1]) if fits.period_s.notna().any() else np.nan,
            "median_fit_period_s": float(fits.period_s.median()),
            "std_fit_period_ms": float(fits.period_s.std(ddof=0) * 1000.0),
            "final_score": float(fits.score.dropna().iloc[-1]) if fits.score.notna().any() else np.nan,
            "median_score": float(fits.score.median()),
            "final_fit_ratio": float(fits.fit_ratio.dropna().iloc[-1]) if fits.fit_ratio.notna().any() else np.nan,
            "median_fit_ratio": float(fits.fit_ratio.median()),
            "final_p95_residual_ms": float(fits.p95_residual_ms.dropna().iloc[-1]) if fits.p95_residual_ms.notna().any() else np.nan,
            "median_p95_residual_ms": float(fits.p95_residual_ms.median()),
        })

    if reference_period is not None and len(fits):
        err_ms = (fits.period_s.to_numpy(float) - reference_period) * 1000.0
        summary.update({
            "period_abs_error_median_ms": float(np.nanmedian(np.abs(err_ms))),
            "period_abs_error_p95_ms": pct(np.abs(err_ms), 95),
            "final_period_error_ms": float(err_ms[np.where(np.isfinite(err_ms))[0][-1]]) if np.isfinite(err_ms).any() else np.nan,
        })

    if len(candidates):
        per_target = candidates.groupby("target", dropna=False).agg(
            candidates=("record", "size"),
            median_gap_ms=("gap_ms", "median"),
            p95_gap_ms=("gap_ms", lambda x: np.nanpercentile(x,95)),
            median_threshold_ms=("threshold_ms", "median"),
        ).reset_index()
    else:
        per_target = pd.DataFrame(columns=["target","candidates","median_gap_ms","p95_gap_ms","median_threshold_ms"])

    event_eval = None
    if ref_events is not None and len(ref_events):
        det_events = pd.concat([activ, resync], ignore_index=True)
        det_events = pd.to_numeric(det_events.get("event_epoch"), errors="coerce").dropna().to_numpy(float)
        det_events = np.unique(det_events)
        errors, tp, fp, fn = nearest_errors(det_events, ref_events, match_tol_ms/1000.0)
        precision = tp / (tp+fp) if tp+fp else np.nan
        recall = tp / (tp+fn) if tp+fn else np.nan
        f1 = 2*precision*recall/(precision+recall) if precision+recall and np.isfinite(precision) and np.isfinite(recall) else np.nan
        event_eval = {
            "detected_control_events": len(det_events),
            "reference_events": len(ref_events),
            "tp": tp, "fp": fp, "fn": fn,
            "precision": precision, "recall": recall, "f1": f1,
            "phase_error_median_ms": float(np.median(errors)*1000) if errors else np.nan,
            "phase_abs_error_median_ms": float(np.median(np.abs(errors))*1000) if errors else np.nan,
            "phase_abs_error_p95_ms": pct(np.abs(errors)*1000,95) if errors else np.nan,
        }
        summary.update(event_eval)

    return summary, per_target


def make_plots(df, outdir: Path, reference_period=None):
    outdir.mkdir(parents=True, exist_ok=True)
    fits = df[df.record == "fit"].copy()
    if len(fits):
        t0 = float(df.wall_time.min())
        x = (fits.wall_time.to_numpy(float) - t0) / 60.0

        fig, ax = plt.subplots(figsize=(8,4.5))
        ax.plot(x, fits.period_s.to_numpy(float)*1000.0, marker="o", markersize=2, linewidth=1)
        if reference_period is not None:
            ax.axhline(reference_period*1000.0, linestyle="--", linewidth=1)
        ax.set_xlabel("Elapsed time (min)")
        ax.set_ylabel("Estimated period (ms)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(outdir / "period_convergence.png", dpi=180)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8,4.5))
        ax.plot(x, fits.score.to_numpy(float), label="score", linewidth=1.2)
        ax.plot(x, fits.fit_ratio.to_numpy(float), label="fit ratio", linewidth=1.2)
        ax.set_xlabel("Elapsed time (min)")
        ax.set_ylabel("Value")
        ax.set_ylim(0, 1.05)
        ax.grid(True, alpha=0.3)
        ax.legend()
        fig.tight_layout()
        fig.savefig(outdir / "fit_quality.png", dpi=180)
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(8,4.5))
        ax.plot(x, fits.p95_residual_ms.to_numpy(float), linewidth=1.2)
        ax.set_xlabel("Elapsed time (min)")
        ax.set_ylabel("P95 lattice residual (ms)")
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(outdir / "residual_over_time.png", dpi=180)
        plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="Evaluate RedHAT automatic period/phase detector logs")
    ap.add_argument("monitor_csv")
    ap.add_argument("--reference-period", type=float, default=None, help="Known ground-truth period in seconds, e.g. 15")
    ap.add_argument("--reference-events", default=None, help="Optional pop_interval.log or event CSV for phase/event accuracy")
    ap.add_argument("--match-tolerance-ms", type=float, default=200.0)
    ap.add_argument("--outdir", default="redhat_auto_eval")
    args = ap.parse_args()

    df = load_monitor(Path(args.monitor_csv))
    ref = load_reference_events(Path(args.reference_events)) if args.reference_events else None
    summary, targets = summarize(df, args.reference_period, ref, args.match_tolerance_ms)

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([summary]).to_csv(outdir / "summary.csv", index=False)
    targets.to_csv(outdir / "per_target.csv", index=False)
    make_plots(df, outdir, args.reference_period)

    with (outdir / "summary.txt").open("w") as f:
        for k, v in summary.items():
            if isinstance(v, float):
                text = "nan" if not np.isfinite(v) else f"{v:.6f}"
            else:
                text = str(v)
            f.write(f"{k}: {text}\n")

    print("=== RedHAT Auto Detector Evaluation ===")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"{k:30s}: {v:.6f}" if np.isfinite(v) else f"{k:30s}: nan")
        else:
            print(f"{k:30s}: {v}")
    if len(targets):
        print("\n=== Per-target candidate statistics ===")
        print(targets.to_string(index=False))
    print(f"\nSaved results to: {outdir}")

if __name__ == "__main__":
    main()
