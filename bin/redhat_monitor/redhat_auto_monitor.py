#!/usr/bin/env python3
"""
RedHAT automatic periodic-disruption detector / controller (state-machine hardened).

Goals
-----
1) Make no Starlink-specific assumption about a 15 s period.
2) Discover candidate probe targets from traceroute plus user-provided/public targets.
3) Probe several targets concurrently.
4) Detect repeated access-path disruption candidates from response-interval gaps.
5) Cross-check events across targets.
6) Learn the dominant period without knowing it in advance.
7) Program the existing RedHAT sysctls only after the pattern is stable.
8) While active, only resynchronize phase with events consistent with the learned lattice.
9) Log cross-target common events exactly once for later detector evaluation.
10) Optionally force all probes through a user-selected Linux interface.
11) Record the real monitor start time in the CSV for correct activation-delay evaluation.
12) On activation, anchor to the newest phase-consistent common event.
13) Do not disable immediately after activation: require an ACTIVE grace period.
14) Disable only when BOTH the recent periodic signature is missing AND the current fit is bad.
15) Apply a short reactivation cooldown after a disable to avoid state flapping.
16) Lock the learned period when ACTIVE using a median of recent good fits.
17) Never rewrite period_ms while ACTIVE.
18) Resynchronize only offset_ms when phase error exceeds a threshold and cooldown.
19) Return to LEARNING after sustained fundamental-period mismatch.

This program assumes the current kernel implementation uses:
  net.ipv4.tcp_leo_rwnd_enable
  net.ipv4.tcp_leo_rwnd_period_ms
  net.ipv4.tcp_leo_rwnd_offset_ms

The existing RedHAT phase function interprets offset_ms as the real-time epoch
modulo period at the reconfiguration boundary.
"""

from __future__ import annotations

import argparse
import collections
import csv
import ipaddress
import math
import os
import re
import selectors
import shutil
import signal
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple


PING_TS_RE = re.compile(r"^\[(\d+(?:\.\d+)?)\]")
IP_RE = re.compile(r"\b(\d{1,3}(?:\.\d{1,3}){3})\b")


def median(xs: Sequence[float]) -> float:
    return statistics.median(xs)


def mad(xs: Sequence[float], med: Optional[float] = None) -> float:
    if not xs:
        return 0.0
    if med is None:
        med = median(xs)
    return median([abs(x - med) for x in xs])


def lattice_residual(dt: float, period: float) -> Tuple[float, int]:
    if period <= 0:
        return float("inf"), 0
    k = max(1, int(round(dt / period)))
    return abs(dt - k * period), k


@dataclass
class TargetState:
    target: str
    last_reply_ts: Optional[float] = None
    intervals: Deque[float] = field(default_factory=lambda: collections.deque(maxlen=10000))
    events: Deque[float] = field(default_factory=lambda: collections.deque(maxlen=2048))
    replies: int = 0
    candidate_count: int = 0
    common_support_count: int = 0
    recent_common_support: Deque[int] = field(
        default_factory=lambda: collections.deque(maxlen=32)
    )

    def baseline(self, min_samples: int = 50) -> Optional[Tuple[float, float]]:
        if len(self.intervals) < min_samples:
            return None
        # Use the lower 95% so previously detected large gaps do not inflate the baseline.
        vals = sorted(self.intervals)
        cut = max(min_samples, int(len(vals) * 0.95))
        core = vals[:cut]
        med = median(core)
        sigma = 1.4826 * mad(core, med)
        return med, sigma

    def add_reply(
        self,
        ts: float,
        *,
        min_baseline_samples: int,
        min_gap_ms: float,
        gap_factor: float,
        mad_sigma: float,
        merge_gap_s: float,
    ) -> Optional[Tuple[float, float, float]]:
        self.replies += 1
        if self.last_reply_ts is None:
            self.last_reply_ts = ts
            return None

        prev = self.last_reply_ts
        dt = ts - prev
        self.last_reply_ts = ts
        if dt <= 0 or dt > 10.0:
            return None

        base_before = self.baseline(min_baseline_samples)
        # Add after computing baseline so the candidate itself cannot move its own threshold.
        self.intervals.append(dt)
        if base_before is None:
            return None

        med, sigma = base_before
        threshold = max(
            min_gap_ms / 1000.0,
            gap_factor * med,
            med + mad_sigma * sigma,
        )

        if dt <= threshold:
            return None

        # Approximate the beginning of the missing-response interval.
        event_ts = prev + med

        if self.events and event_ts - self.events[-1] <= merge_gap_s:
            return None

        self.events.append(event_ts)
        self.candidate_count += 1
        return event_ts, dt, threshold


@dataclass
class PeriodFit:
    period_s: float
    score: float
    fit_ratio: float
    one_cycle_ratio: float
    median_residual_s: float
    p95_residual_s: float
    inliers: int
    intervals: int


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return float("nan")
    vals = sorted(values)
    if len(vals) == 1:
        return vals[0]
    pos = (len(vals) - 1) * p / 100.0
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return vals[lo]
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def score_period(
    intervals: Sequence[float],
    period: float,
    tolerance_s: float,
    max_multiple: int,
    skip_penalty: float,
) -> Optional[PeriodFit]:
    residuals: List[float] = []
    ks: List[int] = []
    normalized: List[float] = []

    for dt in intervals:
        residual, k = lattice_residual(dt, period)
        if k <= max_multiple and residual <= tolerance_s:
            residuals.append(residual)
            ks.append(k)
            normalized.append(dt / k)

    if not residuals:
        return None

    fit_ratio = len(residuals) / len(intervals)
    one_cycle_ratio = sum(1 for k in ks if k == 1) / len(ks)
    mean_k = sum(ks) / len(ks)

    # Reject divisor periods (e.g., 5 s for a true 15 s periodicity) by penalizing
    # solutions that explain most adjacent intervals only as k=2,3,... multiples.
    complexity_penalty = 1.0 + skip_penalty * max(0.0, mean_k - 1.0)
    # score = fit_ratio * (0.70 + 0.30 * one_cycle_ratio) / complexity_penalty
    score = fit_ratio / complexity_penalty

    refined = median(normalized)
    # A single refinement pass usually removes grid quantization error.
    if abs(refined - period) > 1e-9:
        residuals2: List[float] = []
        ks2: List[int] = []
        for dt in intervals:
            residual, k = lattice_residual(dt, refined)
            if k <= max_multiple and residual <= tolerance_s:
                residuals2.append(residual)
                ks2.append(k)
        if residuals2:
            residuals = residuals2
            ks = ks2
            period = refined
            fit_ratio = len(residuals) / len(intervals)
            one_cycle_ratio = sum(1 for k in ks if k == 1) / len(ks)
            mean_k = sum(ks) / len(ks)
            complexity_penalty = 1.0 + skip_penalty * max(0.0, mean_k - 1.0)
            score = fit_ratio / complexity_penalty

    return PeriodFit(
        period_s=period,
        score=score,
        fit_ratio=fit_ratio,
        one_cycle_ratio=one_cycle_ratio,
        median_residual_s=median(residuals),
        p95_residual_s=percentile(residuals, 95),
        inliers=len(residuals),
        intervals=len(intervals),
    )


def estimate_period(
    events: Sequence[float],
    *,
    min_period_s: float,
    max_period_s: float,
    tolerance_s: float,
    max_multiple: int,
    skip_penalty: float,
) -> Optional[PeriodFit]:
    if len(events) < 3:
        return None

    intervals = [b - a for a, b in zip(events, events[1:]) if b > a]
    if len(intervals) < 2:
        return None

    candidates = set()
    # Generate possible fundamentals from adjacent event separations.  We do not
    # assume that every reconfiguration was detected, so divide by k=1..M.
    for dt in intervals:
        for k in range(1, max_multiple + 1):
            p = dt / k
            if min_period_s <= p <= max_period_s:
                candidates.add(round(p, 3))  # 1 ms grid is adequate for current sysctl.

    best: Optional[PeriodFit] = None
    for candidate in candidates:
        fit = score_period(intervals, candidate, tolerance_s, max_multiple, skip_penalty)
        if fit is None:
            continue
        if best is None or fit.score > best.score:
            best = fit
        elif best is not None and abs(fit.score - best.score) < 1e-9:
            # Prefer the larger fundamental if scores tie: this also avoids subharmonics.
            if fit.period_s > best.period_s:
                best = fit

    return best


def cluster_common_events(
    states: Dict[str, TargetState],
    targets: Sequence[str],
    *,
    now: float,
    window_s: float,
    tolerance_s: float,
    min_targets: int,
) -> List[Tuple[float, int, Tuple[str, ...]]]:

    points: List[Tuple[float, str]] = []
    cutoff = now - window_s

    for target in targets:
        st = states[target]

        for ts in st.events:
            if ts >= cutoff:
                points.append((ts, target))

    points.sort()

    if not points:
        return []

    clusters: List[List[Tuple[float, str]]] = []
    cur: List[Tuple[float, str]] = [points[0]]

    for point in points[1:]:
        center = median([x[0] for x in cur])

        if point[0] - center <= tolerance_s:
            cur.append(point)
        else:
            clusters.append(cur)
            cur = [point]

    clusters.append(cur)

    out: List[
        Tuple[float, int, Tuple[str, ...]]
    ] = []

    for cluster in clusters:
        supporters = tuple(
            sorted(set(t for _, t in cluster))
        )

        if len(supporters) < min_targets:
            continue

        per_target: Dict[str, float] = {}

        for ts, target in cluster:
            per_target.setdefault(target, ts)

        center = median(
            list(per_target.values())
        )

        out.append(
            (
                center,
                len(per_target),
                tuple(sorted(per_target)),
            )
        )

    return out


def phase_residual(event: float, anchor: float, period: float) -> float:
    if period <= 0:
        return float("inf")
    k = round((event - anchor) / period)
    return abs(event - (anchor + k * period))


def signed_phase_error(event: float, anchor: float, period: float) -> Tuple[float, float]:
    """Return (signed_error_s, predicted_event_s) on the locked periodic lattice."""
    if period <= 0:
        return float("inf"), float("nan")
    k = round((event - anchor) / period)
    predicted = anchor + k * period
    return event - predicted, predicted


def choose_initial_anchor(events: Sequence[float], period: float, tolerance_s: float) -> Optional[float]:
    if not events:
        return None
    best_anchor = None
    best_inliers: List[float] = []
    for anchor in events:
        inliers = [e for e in events if phase_residual(e, anchor, period) <= tolerance_s]
        if len(inliers) > len(best_inliers):
            best_anchor = anchor
            best_inliers = inliers
    if best_anchor is None:
        return None
    # Return the latest inlier so conversion to offset_ms loses less context.
    return max(best_inliers)


def newest_phase_consistent_event(
    events: Sequence[float],
    anchor: float,
    period: float,
    tolerance_s: float,
) -> Optional[float]:
    """Return the newest event that lies on the learned phase lattice."""
    valid = [
        e
        for e in events
        if phase_residual(e, anchor, period) <= tolerance_s
    ]
    return max(valid) if valid else None


def discover_route_hops(
    destination: str,
    max_hops: int,
    timeout_s: float,
    interface: Optional[str] = None,
) -> List[str]:
    traceroute = shutil.which("traceroute")
    if not traceroute:
        return []

    cmd = [
        traceroute,
        "-n",
        "-q", "1",
        "-w", str(timeout_s),
        "-m", str(max_hops),
    ]

    if interface:
        cmd += ["-i", interface]

    cmd.append(destination)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=max_hops * timeout_s + 3)
    except (OSError, subprocess.SubprocessError):
        return []

    hops: List[str] = []
    for line in proc.stdout.splitlines()[1:]:
        m = IP_RE.search(line)
        if not m:
            continue
        ip = m.group(1)
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip not in hops:
            hops.append(ip)
    return hops


def unique(items: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for x in items:
        if not x or x in seen:
            continue
        seen.add(x)
        out.append(x)
    return out


def build_probe_targets(args: argparse.Namespace) -> List[str]:
    explicit = list(args.target or [])
    bootstraps = list(args.bootstrap or [])

    all_hops: List[str] = []

    if args.discover_hops:
        for bootstrap in bootstraps:
            hops = discover_route_hops(
                bootstrap,
                args.traceroute_hops,
                args.traceroute_timeout,
                args.interface,
            )

            if hops:
                print(
                    f"[DISCOVERY] traceroute {bootstrap}: "
                    + ", ".join(hops)
                )

                usable = hops[
                    args.skip_route_hops:
                ]

                all_hops.extend(
                    usable[: args.max_route_targets]
                )

    selected_hops = unique(all_hops)

    targets = unique(
        explicit
        + bootstraps
        + selected_hops
    )

    return targets[: args.max_targets]


def start_ping(
    target: str,
    interval_s: float,
    interface: Optional[str] = None,
) -> subprocess.Popen:
    ping = shutil.which("ping")
    if not ping:
        raise RuntimeError("ping command not found")

    cmd = [ping, "-D", "-n", "-i", str(interval_s), "-W", "1"]

    if interface:
        cmd += ["-I", interface]

    cmd.append(target)
    if shutil.which("stdbuf"):
        cmd = ["stdbuf", "-oL"] + cmd
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def sysctl_write(name: str, value: int, dry_run: bool) -> None:
    print(f"[SYSCTL] {name}={value}")
    if dry_run:
        return
    proc = subprocess.run(
        ["sysctl", "-w", f"{name}={value}"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


def program_redhat(period_s: float, anchor_epoch_s: float, dry_run: bool) -> Tuple[int, int]:
    period_ms = max(1, int(round(period_s * 1000.0)))
    anchor_ms = int(round(anchor_epoch_s * 1000.0))
    offset_ms = anchor_ms % period_ms
    # Set timing first; enable last so there is no interval with mixed parameters.
    sysctl_write("net.ipv4.tcp_leo_rwnd_period_ms", period_ms, dry_run)
    sysctl_write("net.ipv4.tcp_leo_rwnd_offset_ms", offset_ms, dry_run)
    sysctl_write("net.ipv4.tcp_leo_rwnd_enable", 1, dry_run)
    sysctl_write("net.ipv4.tcp_leo_dynamic_enable", 1, dry_run)
    return period_ms, offset_ms


def program_redhat_phase_only(
    locked_period_s: float,
    anchor_epoch_s: float,
    dry_run: bool,
) -> int:
    """Update only the kernel phase/offset while ACTIVE."""
    period_ms = max(1, int(round(locked_period_s * 1000.0)))
    anchor_ms = int(round(anchor_epoch_s * 1000.0))
    offset_ms = anchor_ms % period_ms
    sysctl_write("net.ipv4.tcp_leo_rwnd_offset_ms", offset_ms, dry_run)
    return offset_ms


def disable_redhat(dry_run: bool) -> None:
    sysctl_write("net.ipv4.tcp_leo_rwnd_enable", 0, dry_run)
    sysctl_write("net.ipv4.tcp_leo_dynamic_enable", 0, dry_run)

def compute_prediction_guard_ms(
    errors_ms: Sequence[float],
    safety_ms: float,
    min_ms: int,
    max_ms: int,
) -> int:
    if not errors_ms:
        return min_ms

    q95 = percentile(errors_ms, 95)

    guard = int(math.ceil(
        q95 + safety_ms
    ))

    return max(
        min_ms,
        min(max_ms, guard),
    )

def target_participation_ratio(
    st: TargetState,
) -> float:
    if not st.recent_common_support:
        return 0.0

    return (
        sum(st.recent_common_support)
        / len(st.recent_common_support)
    )

def choose_active_targets(
    states: Dict[str, TargetState],
    all_targets: Sequence[str],
    bootstraps: Sequence[str],
    max_targets: int,
    min_ratio: float,
) -> List[str]:

    scored = []

    for target in all_targets:
        st = states[target]

        ratio = target_participation_ratio(st)

        if ratio < min_ratio:
            continue

        scored.append(
            (
                ratio,
                st.common_support_count,
                target,
            )
        )

    scored.sort(reverse=True)

    selected: List[str] = []

    # Keep at least one stable public endpoint.
    endpoint_candidates = [
        item
        for item in scored
        if item[2] in bootstraps
    ]

    if endpoint_candidates:
        selected.append(
            endpoint_candidates[0][2]
        )

    for _, _, target in scored:
        if target in selected:
            continue

        selected.append(target)

        if len(selected) >= max_targets:
            break

    return selected



def reset_probe_session(states: Dict[str, TargetState], targets: Sequence[str]) -> None:
    """Reset only reply-edge state before (re)starting probes.

    Keep the long-term interval baseline and historical events; the former lets a
    short validation become useful immediately, while the analysis window filters
    old events naturally.  Resetting last_reply_ts prevents the first reply after
    a long sleep from being interpreted as one huge gap.
    """
    for target in targets:
        if target in states:
            states[target].last_reply_ts = None


def start_probe_processes(
    targets: Sequence[str],
    interval_s: float,
    interface: Optional[str],
    selector: selectors.BaseSelector,
    procs: Dict[int, Tuple[str, subprocess.Popen]],
) -> int:
    """Start ping processes for targets not already running."""
    running = {target for target, _proc in procs.values()}
    started = 0
    for target in targets:
        if target in running:
            continue
        try:
            proc = start_ping(target, interval_s, interface)
        except Exception as e:
            print(f"[WARN] failed to start ping for {target}: {e}")
            continue
        assert proc.stdout is not None
        selector.register(proc.stdout, selectors.EVENT_READ, target)
        procs[proc.pid] = (target, proc)
        started += 1
    return started


def stop_probe_processes(
    procs: Dict[int, Tuple[str, subprocess.Popen]],
    selector: selectors.BaseSelector,
) -> None:
    """Stop all currently running ping processes and unregister their pipes."""
    for _pid, (_target, proc) in list(procs.items()):
        if proc.stdout is not None:
            try:
                selector.unregister(proc.stdout)
            except Exception:
                pass
        try:
            proc.terminate()
        except OSError:
            pass

    for _pid, (_target, proc) in list(procs.items()):
        try:
            proc.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception:
                pass
    procs.clear()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--interface",
        default=None,
        help=(
            "Network interface used for ping/traceroute probes, e.g. eno1 or enp0s20f0u2. "
            "If omitted, Linux routing selects the interface."
        ),
    )
    p.add_argument("--target", action="append", help="Explicit probe target; repeatable")
    p.add_argument(
        "--bootstrap",
        action="append",
        default=None,
        help="Stable Internet endpoint used for route discovery/probing; repeatable",
    )
    p.add_argument("--discover-hops", action="store_true", default=True)
    p.add_argument("--no-discover-hops", dest="discover_hops", action="store_false")
    p.add_argument("--traceroute-hops", type=int, default=8)
    p.add_argument("--traceroute-timeout", type=float, default=0.4)
    p.add_argument("--skip-route-hops", type=int, default=1,
                   help="Skip initial traceroute hops; default 1 excludes the local Starlink router.")
    p.add_argument("--max-route-targets", type=int, default=3)
    p.add_argument("--max-targets", type=int, default=5)

    p.add_argument("--probe-interval-ms", type=float, default=20.0)
    p.add_argument("--min-baseline-samples", type=int, default=100)
    p.add_argument("--min-gap-ms", type=float, default=50.0)
    p.add_argument("--gap-factor", type=float, default=2.3)
    p.add_argument("--mad-sigma", type=float, default=7.0)
    p.add_argument("--event-merge-ms", type=float, default=500.0)
    p.add_argument("--cross-target-ms", type=float, default=100.0)
    p.add_argument("--min-targets", type=int, default=2)

    p.add_argument("--period-min", type=float, default=5.0)
    p.add_argument("--period-max", type=float, default=30.0)
    p.add_argument("--period-tolerance-ms", type=float, default=180.0)
    p.add_argument("--max-multiple", type=int, default=6)
    p.add_argument("--skip-penalty", type=float, default=0.30)
    p.add_argument("--min-events", type=int, default=6)
    p.add_argument("--min-score", type=float, default=0.70)
    p.add_argument("--min-fit-ratio", type=float, default=0.70)
    p.add_argument("--learn-seconds", type=float, default=120.0,
                   help="Minimum observation time before activation")
    p.add_argument("--analysis-window", type=float, default=300.0)
    p.add_argument("--analysis-every", type=float, default=5.0)

    p.add_argument("--period-lock-window", type=int, default=8,
                   help="Number of recent good period fits used for median period locking.")
    p.add_argument("--period-lock-min-fits", type=int, default=3,
                   help="Minimum number of recent good fits required before activation.")
    p.add_argument("--phase-resync-threshold-ms", type=float, default=40.0,
                   help="Rewrite offset only when consensus phase error reaches this threshold.")
    p.add_argument("--phase-resync-min-cycles", type=float, default=3.0,
                   help="Minimum locked periods between kernel phase resynchronizations.")
    p.add_argument("--phase-resync-alpha", type=float, default=1.0,
                   help="Fraction of consensus phase error applied during resynchronization.")
    p.add_argument("--period-relearn-threshold-ms", type=float, default=100.0,
                   help="Observed period deviation that counts as a fundamental-period mismatch.")
    p.add_argument("--period-relearn-count", type=int, default=5,
                   help="Consecutive structurally-good but mismatching fits before relearning.")
    p.add_argument("--reactivation-cooldown-cycles", type=float, default=2.0,
                   help="Cooldown after disabling before activation is allowed again.")
    # Retained for CLI compatibility with older runs.  In the self-suspending
    # ACTIVE design, confirmation/validation session outcomes replace the old
    # continuous lost-signature timer.
    p.add_argument("--lost-cycles", type=float, default=8.0,
                   help="Compatibility option; not used while ACTIVE probes are suspended.")
    p.add_argument("--disable-grace-cycles", type=float, default=8.0,
                   help="Compatibility option; not used while ACTIVE probes are suspended.")

    p.add_argument("--phase-consensus-window", type=int, default=5,
                   help="Recent signed phase errors retained for consensus resynchronization.")
    p.add_argument("--phase-consensus-min-events", type=int, default=3,
                   help="Minimum recent phase events required before phase resync.")
    p.add_argument("--phase-consensus-sign-ratio", type=float, default=0.80,
                   help="Required fraction of phase errors having the median's sign.")

    p.add_argument("--auto-prediction-guard", action="store_true",
                   help="Automatically derive kernel prediction guard from phase uncertainty.")
    p.add_argument("--prediction-guard-window", type=int, default=12)
    p.add_argument("--prediction-guard-safety-ms", type=float, default=15.0)
    p.add_argument("--prediction-guard-min-ms", type=int, default=20)
    p.add_argument("--prediction-guard-max-ms", type=int, default=120)
    p.add_argument("--prediction-guard-update-step-ms", type=int, default=10,
                   help="Do not rewrite the guard for changes smaller than this.")

    p.add_argument("--active-targets", type=int, default=3,
                   help="Number of best targets used during ACTIVE confirmation/validation.")
    p.add_argument("--active-min-participation", type=float, default=0.70,
                   help="Minimum learning-stage common-event participation ratio for an ACTIVE target.")

    # Self-suspending probing policy.
    p.add_argument("--active-confirm-cycles", type=float, default=5.0,
                   help="Keep probing for this many locked periods immediately after activation.")
    p.add_argument("--active-confirm-min-events", type=int, default=2,
                   help="Minimum phase-consistent events required during ACTIVE confirmation.")
    p.add_argument("--validation-interval-min", type=float, default=30.0,
                   help="While ACTIVE/SLEEP, start a short active validation this many minutes later.")
    p.add_argument("--validation-cycles", type=float, default=5.0,
                   help="Length of each periodic validation in locked periods.")
    p.add_argument("--validation-min-events", type=int, default=3,
                   help="Minimum phase-consistent events required for validation success.")

    p.add_argument("--manage-enable", action="store_true",
                   help="Actually enable/disable RedHAT based on detector state")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--log", default="redhat_auto_monitor.csv")
    args = p.parse_args()

    if args.bootstrap is None:
        args.bootstrap = ["1.1.1.1", "8.8.8.8"]

    # Defensive argument normalization.
    args.skip_route_hops = max(0, args.skip_route_hops)
    args.max_targets = max(1, args.max_targets)
    args.max_route_targets = max(0, args.max_route_targets)
    args.min_targets = max(1, args.min_targets)
    args.active_targets = max(args.min_targets, args.active_targets)
    args.period_lock_min_fits = max(1, args.period_lock_min_fits)
    args.period_lock_window = max(args.period_lock_min_fits, args.period_lock_window)
    args.phase_consensus_min_events = max(1, args.phase_consensus_min_events)
    args.phase_consensus_window = max(args.phase_consensus_min_events, args.phase_consensus_window)
    args.active_confirm_cycles = max(1.0, args.active_confirm_cycles)
    args.validation_cycles = max(1.0, args.validation_cycles)
    args.active_confirm_min_events = max(1, args.active_confirm_min_events)
    args.validation_min_events = max(1, args.validation_min_events)
    args.validation_interval_min = max(0.1, args.validation_interval_min)
    return args


def main() -> int:
    args = parse_args()
    targets = build_probe_targets(args)
    if not targets:
        print("ERROR: no probe targets", file=sys.stderr)
        return 2

    if args.min_targets > len(targets):
        print(f"[WARN] min-targets={args.min_targets} > targets={len(targets)}; lowering it")
        args.min_targets = len(targets)
    if args.active_targets < args.min_targets:
        args.active_targets = args.min_targets

    current_targets = list(targets)
    states = {t: TargetState(t) for t in targets}
    procs: Dict[int, Tuple[str, subprocess.Popen]] = {}
    selector = selectors.DefaultSelector()

    print("[TARGETS]", ", ".join(targets))
    print(f"[INTERFACE] forcing probes through {args.interface}" if args.interface
          else "[INTERFACE] using Linux routing/default route")
    print("[STATE] LEARNING")

    if args.manage_enable:
        try:
            disable_redhat(args.dry_run)
        except RuntimeError as e:
            print(f"ERROR: cannot disable RedHAT: {e}", file=sys.stderr)
            return 2

    reset_probe_session(states, targets)
    start_probe_processes(
        targets,
        args.probe_interval_ms / 1000.0,
        args.interface,
        selector,
        procs,
    )
    if not procs:
        print("ERROR: no ping process started", file=sys.stderr)
        selector.close()
        return 2

    stop = False

    def handle_signal(_signum, _frame):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    log_path = Path(args.log)
    log_file = log_path.open("w", newline="", buffering=1)
    writer = csv.writer(log_file)
    writer.writerow([
        "wall_time", "record", "target", "event_epoch", "gap_ms", "threshold_ms",
        "support", "period_s", "score", "fit_ratio", "p95_residual_ms",
        "state", "period_ms", "offset_ms", "observed_period_s", "locked_period_s",
        "predicted_event_epoch", "phase_error_ms", "action", "probe_mode",
    ])

    def state_label(active: bool) -> str:
        return "ACTIVE" if active else "LEARNING"

    def log_simple(now_wall: float, record: str, action: str, probe_mode: str,
                   locked_period: Optional[float] = None) -> None:
        writer.writerow([
            now_wall, record, "", "", "", "", "", "", "", "", "",
            state_label(active), "", "", "",
            f"{locked_period:.9f}" if locked_period is not None else "",
            "", "", action, probe_mode,
        ])

    start_wall = time.time()
    learning_started_wall = start_wall
    writer.writerow([
        start_wall, "start", "", "", "", "", "", "", "", "", "",
        "LEARNING", "", "", "", "", "", "", "", "LEARNING",
    ])

    # Controller state.
    active = False
    probe_mode = "LEARNING"  # LEARNING, ACTIVE_CONFIRM, ACTIVE_SLEEP, ACTIVE_VALIDATE
    locked_period: Optional[float] = None
    phase_anchor: Optional[float] = None
    last_valid_event: Optional[float] = None
    last_programmed: Optional[Tuple[int, int]] = None
    last_kernel_resync_time: Optional[float] = None
    active_since: Optional[float] = None
    last_disable_time: Optional[float] = None
    last_disabled_period: Optional[float] = None

    recent_good_period_fits: Deque[float] = collections.deque(
        maxlen=max(1, args.period_lock_window)
    )
    phase_error_history: Deque[float] = collections.deque(
        maxlen=max(1, args.phase_consensus_window)
    )
    phase_abs_error_history: Deque[float] = collections.deque(
        maxlen=max(1, args.prediction_guard_window)
    )
    last_prediction_guard_ms: Optional[int] = None
    period_mismatch_count = 0

    # Probe-session state for confirmation/periodic validation.
    session_started_wall: Optional[float] = None
    session_deadline_mono: Optional[float] = None
    session_valid_events = 0
    next_validation_mono: Optional[float] = None

    logged_common_events = set()
    next_analysis = time.monotonic() + args.analysis_every

    def reset_learning_target_stats() -> None:
        for st in states.values():
            st.common_support_count = 0
            st.recent_common_support.clear()

    def enter_learning(now_wall: float, now_mono: float, reason: str) -> None:
        nonlocal active, probe_mode, locked_period, phase_anchor, last_valid_event
        nonlocal last_programmed, last_kernel_resync_time, active_since
        nonlocal last_disable_time, last_disabled_period, period_mismatch_count
        nonlocal current_targets, session_started_wall, session_deadline_mono
        nonlocal session_valid_events, next_validation_mono, next_analysis
        nonlocal last_prediction_guard_ms, learning_started_wall

        previous_period = locked_period
        if args.manage_enable:
            disable_redhat(args.dry_run)

        active = False
        probe_mode = "LEARNING"
        locked_period = None
        phase_anchor = None
        last_valid_event = None
        last_programmed = None
        last_kernel_resync_time = None
        active_since = None
        period_mismatch_count = 0
        phase_error_history.clear()
        phase_abs_error_history.clear()
        recent_good_period_fits.clear()
        last_prediction_guard_ms = None
        current_targets = list(targets)
        session_started_wall = None
        session_deadline_mono = None
        session_valid_events = 0
        next_validation_mono = None
        reset_learning_target_stats()
        learning_started_wall = now_wall

        if previous_period is not None:
            last_disable_time = now_wall
            last_disabled_period = previous_period

        # Full LEARNING always restores active probing on all discovery targets.
        stop_probe_processes(procs, selector)
        reset_probe_session(states, targets)
        start_probe_processes(
            targets,
            args.probe_interval_ms / 1000.0,
            args.interface,
            selector,
            procs,
        )
        next_analysis = now_mono + args.analysis_every
        print(f"[STATE] LEARNING reason={reason}")
        print("[TARGETS] restored learning targets: " + ", ".join(current_targets))
        log_simple(now_wall, "relearn", reason, probe_mode, previous_period)

    def enter_sleep(now_wall: float, now_mono: float, source: str) -> None:
        nonlocal probe_mode, next_validation_mono, session_started_wall
        nonlocal session_deadline_mono, session_valid_events

        stop_probe_processes(procs, selector)
        probe_mode = "ACTIVE_SLEEP"
        session_started_wall = None
        session_deadline_mono = None
        session_valid_events = 0
        next_validation_mono = now_mono + args.validation_interval_min * 60.0
        print(
            f"[PROBE] suspended after {source}; next validation in "
            f"{args.validation_interval_min:.1f} min"
        )
        log_simple(now_wall, "probe_sleep", source, probe_mode, locked_period)

    def start_validation(now_wall: float, now_mono: float) -> None:
        nonlocal probe_mode, session_started_wall, session_deadline_mono
        nonlocal session_valid_events, next_analysis

        if locked_period is None:
            enter_learning(now_wall, now_mono, "validation-without-period")
            return
        reset_probe_session(states, current_targets)
        stop_probe_processes(procs, selector)
        start_probe_processes(
            current_targets,
            args.probe_interval_ms / 1000.0,
            args.interface,
            selector,
            procs,
        )
        if len(procs) < args.min_targets:
            enter_learning(now_wall, now_mono, "validation-probe-start-failure")
            return
        probe_mode = "ACTIVE_VALIDATE"
        session_started_wall = now_wall
        session_deadline_mono = now_mono + args.validation_cycles * locked_period
        session_valid_events = 0
        phase_error_history.clear()
        phase_abs_error_history.clear()
        next_analysis = now_mono + min(args.analysis_every, 1.0)
        print(
            f"[PROBE] periodic validation started targets={','.join(current_targets)} "
            f"duration={args.validation_cycles * locked_period:.1f}s"
        )
        log_simple(now_wall, "validation_start", "timer", probe_mode, locked_period)

    try:
        while not stop:
            now_mono_pre = time.monotonic()
            now_wall_pre = time.time()

            # ACTIVE_SLEEP does no continuous probing.  Wake only for scheduled validation.
            if (
                active
                and probe_mode == "ACTIVE_SLEEP"
                and next_validation_mono is not None
                and now_mono_pre >= next_validation_mono
            ):
                start_validation(now_wall_pre, now_mono_pre)

            events_ready = selector.select(timeout=0.25)
            for key, _mask in events_ready:
                target = key.data
                line = key.fileobj.readline()
                if not line:
                    # A ping process died or its pipe closed.  Unregister it; later
                    # session checks decide whether enough targets remain.
                    try:
                        selector.unregister(key.fileobj)
                    except Exception:
                        pass
                    dead_pid = None
                    for pid, (t, proc) in procs.items():
                        if t == target and proc.stdout is key.fileobj:
                            dead_pid = pid
                            break
                    if dead_pid is not None:
                        _t, proc = procs.pop(dead_pid)
                        try:
                            proc.wait(timeout=0.1)
                        except Exception:
                            pass
                    print(f"[WARN] ping process ended for {target}")
                    continue

                m = PING_TS_RE.match(line)
                if not m:
                    continue
                ts = float(m.group(1))
                candidate = states[target].add_reply(
                    ts,
                    min_baseline_samples=args.min_baseline_samples,
                    min_gap_ms=args.min_gap_ms,
                    gap_factor=args.gap_factor,
                    mad_sigma=args.mad_sigma,
                    merge_gap_s=args.event_merge_ms / 1000.0,
                )
                if candidate:
                    ev, dt, threshold = candidate
                    print(
                        f"[CANDIDATE] target={target} event={ev:.6f} "
                        f"gap={dt*1000:.1f}ms threshold={threshold*1000:.1f}ms"
                    )
                    writer.writerow([
                        time.time(), "candidate", target, f"{ev:.9f}", f"{dt*1000:.3f}",
                        f"{threshold*1000:.3f}", "", "", "", "", "",
                        state_label(active), "", "", "",
                        f"{locked_period:.9f}" if locked_period is not None else "",
                        "", "", "", probe_mode,
                    ])

            now_mono = time.monotonic()
            now_wall = time.time()

            # Sleeping ACTIVE state intentionally skips all stale-fit/disable logic.
            if active and probe_mode == "ACTIVE_SLEEP":
                continue

            if now_mono < next_analysis:
                # Session deadlines must still be enforced even between analyses.
                if (
                    active
                    and probe_mode in ("ACTIVE_CONFIRM", "ACTIVE_VALIDATE")
                    and session_deadline_mono is not None
                    and now_mono >= session_deadline_mono
                ):
                    needed = (args.active_confirm_min_events
                              if probe_mode == "ACTIVE_CONFIRM"
                              else args.validation_min_events)
                    source = "confirm" if probe_mode == "ACTIVE_CONFIRM" else "validation"
                    if session_valid_events >= needed:
                        print(
                            f"[PROBE] {source} success valid_events={session_valid_events}/{needed}"
                        )
                        log_simple(now_wall, f"{source}_success", "pass", probe_mode, locked_period)
                        enter_sleep(now_wall, now_mono, source)
                    else:
                        print(
                            f"[PROBE] {source} failed valid_events={session_valid_events}/{needed}; "
                            "returning to full LEARNING"
                        )
                        enter_learning(now_wall, now_mono, f"{source}-failed")
                continue

            next_analysis = now_mono + args.analysis_every

            common = cluster_common_events(
                states,
                current_targets,
                now=now_wall,
                window_s=args.analysis_window,
                tolerance_s=args.cross_target_ms / 1000.0,
                min_targets=args.min_targets,
            )
            common_times = [x[0] for x in common]

            # Log each cross-target common event exactly once.  Participation
            # statistics are learned only in LEARNING mode.
            for event_time, support, supporters in common:
                event_key = int(round(event_time * 1000.0))
                if event_key in logged_common_events:
                    continue
                logged_common_events.add(event_key)
                supporter_text = ",".join(supporters)

                if not active:
                    for target in targets:
                        participated = 1 if target in supporters else 0
                        states[target].recent_common_support.append(participated)
                        if participated:
                            states[target].common_support_count += 1

                print(
                    f"[COMMON] event={event_time:.6f} support={support} "
                    f"targets={supporter_text}"
                )
                writer.writerow([
                    now_wall, "common", supporter_text, f"{event_time:.9f}", "", "", support,
                    "", "", "", "", state_label(active), "", "", "",
                    f"{locked_period:.9f}" if locked_period is not None else "",
                    "", "", "", probe_mode,
                ])

            fit: Optional[PeriodFit] = None
            if len(common_times) >= args.min_events:
                fit = estimate_period(
                    common_times,
                    min_period_s=args.period_min,
                    max_period_s=args.period_max,
                    tolerance_s=args.period_tolerance_ms / 1000.0,
                    max_multiple=args.max_multiple,
                    skip_penalty=args.skip_penalty,
                )

            if fit:
                print(
                    f"[FIT] events={len(common_times)} period={fit.period_s:.6f}s "
                    f"score={fit.score:.3f} fit={fit.fit_ratio:.3f} "
                    f"one-cycle={fit.one_cycle_ratio:.3f} "
                    f"p95-residual={fit.p95_residual_s*1000:.1f}ms"
                )
                writer.writerow([
                    now_wall, "fit", "", "", "", "", "",
                    f"{fit.period_s:.9f}", f"{fit.score:.6f}", f"{fit.fit_ratio:.6f}",
                    f"{fit.p95_residual_s*1000:.3f}", state_label(active), "", "",
                    f"{fit.period_s:.9f}",
                    f"{locked_period:.9f}" if locked_period is not None else "",
                    "", "", "fit", probe_mode,
                ])

            # Initial discovery criteria and ACTIVE maintenance criteria are intentionally separate.
            learning_fit_good = bool(
                fit
                and fit.score >= args.min_score
                and fit.fit_ratio >= args.min_fit_ratio
                and fit.p95_residual_s <= args.period_tolerance_ms / 1000.0
            )
            active_structure_good = bool(
                fit
                and fit.fit_ratio >= args.min_fit_ratio
                and fit.p95_residual_s <= args.period_tolerance_ms / 1000.0
            )
            if not active and learning_fit_good and fit is not None:
                recent_good_period_fits.append(fit.period_s)

            learned_long_enough = (now_wall - learning_started_wall) >= args.learn_seconds
            reactivation_allowed = True
            if (
                last_disable_time is not None
                and last_disabled_period is not None
                and args.reactivation_cooldown_cycles > 0
            ):
                cooldown_s = args.reactivation_cooldown_cycles * last_disabled_period
                reactivation_allowed = (now_wall - last_disable_time) >= cooldown_s

            enough_lock_fits = len(recent_good_period_fits) >= args.period_lock_min_fits
            good_fit = bool(
                not active
                and learning_fit_good
                and learned_long_enough
                and reactivation_allowed
                and enough_lock_fits
            )

            if good_fit and fit is not None:
                tolerance_s = args.period_tolerance_ms / 1000.0
                candidate_locked_period = statistics.median(list(recent_good_period_fits))
                lattice_anchor = choose_initial_anchor(
                    common_times, candidate_locked_period, tolerance_s
                )
                anchor = None
                if lattice_anchor is not None:
                    anchor = newest_phase_consistent_event(
                        common_times, lattice_anchor, candidate_locked_period, tolerance_s
                    )

                if anchor is not None:
                    active = True
                    probe_mode = "ACTIVE_CONFIRM"
                    locked_period = candidate_locked_period
                    phase_anchor = anchor
                    last_valid_event = anchor
                    active_since = now_wall
                    last_kernel_resync_time = now_wall
                    period_mismatch_count = 0
                    phase_error_history.clear()
                    phase_abs_error_history.clear()

                    selected_targets = choose_active_targets(
                        states,
                        targets,
                        args.bootstrap,
                        args.active_targets,
                        args.active_min_participation,
                    )
                    if len(selected_targets) >= args.min_targets:
                        current_targets = selected_targets
                    else:
                        current_targets = list(targets)
                    print(
                        f"[TARGET-PRUNE] {len(targets)} -> {len(current_targets)} targets: "
                        + ", ".join(current_targets)
                    )

                    if args.manage_enable:
                        last_programmed = program_redhat(
                            locked_period, phase_anchor, args.dry_run
                        )

                    session_started_wall = now_wall
                    session_deadline_mono = now_mono + args.active_confirm_cycles * locked_period
                    session_valid_events = 0
                    next_validation_mono = None

                    print(
                        f"[STATE] ACTIVE locked_period={locked_period:.6f}s "
                        f"observed_period={fit.period_s:.6f}s anchor={phase_anchor:.6f} "
                        f"confirm_for={args.active_confirm_cycles:.1f} cycles"
                    )
                    writer.writerow([
                        now_wall, "activate", "", f"{anchor:.9f}", "", "", "",
                        f"{locked_period:.9f}", f"{fit.score:.6f}", f"{fit.fit_ratio:.6f}",
                        f"{fit.p95_residual_s*1000:.3f}", "ACTIVE",
                        last_programmed[0] if last_programmed else "",
                        last_programmed[1] if last_programmed else "",
                        f"{fit.period_s:.9f}", f"{locked_period:.9f}", "", "", "lock",
                        probe_mode,
                    ])
                continue

            if active and locked_period is not None and phase_anchor is not None:
                # Detect a true period change only from structurally good fits.  Do not
                # gate this test on already being close to the locked period.
                if active_structure_good and fit is not None:
                    period_error_ms = (fit.period_s - locked_period) * 1000.0
                    if abs(period_error_ms) >= args.period_relearn_threshold_ms:
                        period_mismatch_count += 1
                        print(
                            f"[PERIOD] locked={locked_period:.6f}s observed={fit.period_s:.6f}s "
                            f"error={period_error_ms:+.1f}ms "
                            f"mismatch={period_mismatch_count}/{args.period_relearn_count}"
                        )
                    else:
                        period_mismatch_count = 0

                if period_mismatch_count >= args.period_relearn_count:
                    enter_learning(now_wall, now_mono, "period-mismatch")
                    continue

                valid = [
                    e for e in common_times
                    if phase_residual(e, phase_anchor, locked_period)
                    <= args.period_tolerance_ms / 1000.0
                    and (last_valid_event is None or e > last_valid_event + 1e-6)
                ]

                if valid:
                    newest = max(valid)
                    phase_error_s, predicted_event = signed_phase_error(
                        newest, phase_anchor, locked_period
                    )
                    phase_error_ms = phase_error_s * 1000.0
                    phase_error_history.append(phase_error_s)
                    phase_abs_error_history.append(abs(phase_error_ms))
                    last_valid_event = newest
                    session_valid_events += 1

                    min_resync_interval_s = args.phase_resync_min_cycles * locked_period
                    cooldown_elapsed = (
                        last_kernel_resync_time is None
                        or now_wall - last_kernel_resync_time >= min_resync_interval_s
                    )
                    consensus_ready = len(phase_error_history) >= args.phase_consensus_min_events
                    consensus_error_s = 0.0
                    sign_ratio = 0.0
                    consensus_crossed = False

                    if consensus_ready:
                        recent_errors = list(phase_error_history)
                        consensus_error_s = statistics.median(recent_errors)
                        if consensus_error_s > 0:
                            same_sign = sum(e > 0 for e in recent_errors)
                        elif consensus_error_s < 0:
                            same_sign = sum(e < 0 for e in recent_errors)
                        else:
                            same_sign = 0
                        sign_ratio = same_sign / len(recent_errors)
                        consensus_crossed = bool(
                            abs(consensus_error_s) * 1000.0 >= args.phase_resync_threshold_ms
                            and sign_ratio >= args.phase_consensus_sign_ratio
                        )

                    action = "none"
                    if consensus_crossed and cooldown_elapsed:
                        alpha = min(1.0, max(0.0, args.phase_resync_alpha))
                        corrected_anchor = phase_anchor + consensus_error_s * alpha
                        if args.manage_enable:
                            new_offset = program_redhat_phase_only(
                                locked_period, corrected_anchor, args.dry_run
                            )
                            if last_programmed is not None:
                                last_programmed = (last_programmed[0], new_offset)
                        phase_anchor = corrected_anchor
                        last_kernel_resync_time = now_wall
                        phase_error_history.clear()
                        action = "resync"

                    print(
                        f"[PHASE] event={newest:.6f} predicted={predicted_event:.6f} "
                        f"error={phase_error_ms:+.1f}ms action={action} "
                        f"session_events={session_valid_events}"
                    )

                    if args.auto_prediction_guard and len(phase_abs_error_history) >= 5:
                        desired_guard_ms = compute_prediction_guard_ms(
                            list(phase_abs_error_history),
                            args.prediction_guard_safety_ms,
                            args.prediction_guard_min_ms,
                            args.prediction_guard_max_ms,
                        )
                        guard_changed_enough = (
                            last_prediction_guard_ms is None
                            or abs(desired_guard_ms - last_prediction_guard_ms)
                                >= args.prediction_guard_update_step_ms
                        )
                        if guard_changed_enough:
                            print(
                                f"[GUARD] p95={percentile(list(phase_abs_error_history), 95):.1f}ms "
                                f"new_guard={desired_guard_ms}ms"
                            )
                            if args.manage_enable:
                                sysctl_write(
                                    "net.ipv4.tcp_leo_dynamic_prediction_guard_ms",
                                    desired_guard_ms,
                                    args.dry_run,
                                )
                            last_prediction_guard_ms = desired_guard_ms

                    writer.writerow([
                        now_wall, "phase_check", "", f"{newest:.9f}", "", "", "",
                        f"{locked_period:.9f}",
                        f"{fit.score:.6f}" if fit else "",
                        f"{fit.fit_ratio:.6f}" if fit else "",
                        f"{fit.p95_residual_s*1000:.3f}" if fit else "",
                        "ACTIVE",
                        last_programmed[0] if last_programmed else "",
                        last_programmed[1] if last_programmed else "",
                        f"{fit.period_s:.9f}" if fit else "",
                        f"{locked_period:.9f}", f"{predicted_event:.9f}",
                        f"{phase_error_ms:.3f}", action, probe_mode,
                    ])

                # Initial confirmation / periodic validation is time-bounded.  On
                # success the monitor self-suspends; on failure it performs a full relearn.
                if (
                    probe_mode in ("ACTIVE_CONFIRM", "ACTIVE_VALIDATE")
                    and session_deadline_mono is not None
                    and now_mono >= session_deadline_mono
                ):
                    needed = (args.active_confirm_min_events
                              if probe_mode == "ACTIVE_CONFIRM"
                              else args.validation_min_events)
                    source = "confirm" if probe_mode == "ACTIVE_CONFIRM" else "validation"
                    if session_valid_events >= needed:
                        print(
                            f"[PROBE] {source} success valid_events={session_valid_events}/{needed}"
                        )
                        log_simple(now_wall, f"{source}_success", "pass", probe_mode, locked_period)
                        enter_sleep(now_wall, now_mono, source)
                    else:
                        print(
                            f"[PROBE] {source} failed valid_events={session_valid_events}/{needed}; "
                            "returning to full LEARNING"
                        )
                        enter_learning(now_wall, now_mono, f"{source}-failed")
                    continue

                # During active probing sessions, if the available target count falls
                # below cross-target requirements, fail safely into full relearning.
                running_targets = {target for target, _proc in procs.values()}
                running_current = sum(1 for target in current_targets if target in running_targets)
                if running_current < args.min_targets:
                    enter_learning(now_wall, now_mono, "insufficient-probe-processes")
                    continue

    finally:
        stop_probe_processes(procs, selector)
        selector.close()
        log_file.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
