#!/usr/bin/env python3
"""Analyze JMeter .jtl files over time."""

from __future__ import annotations

import argparse
import csv
import glob
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Sample:
    timestamp_ms: int
    elapsed_ms: int
    success: bool


@dataclass
class WindowStats:
    window_index: int
    start_ms: int
    end_ms: int
    samples: int
    successes: int
    failures: int
    error_rate_pct: float
    throughput_rps: float
    success_avg_ms: float
    success_min_ms: int
    success_p95_ms: int
    success_max_ms: int
    failed_avg_ms: float
    failed_min_ms: int
    failed_p95_ms: int
    failed_max_ms: int
    all_avg_ms: float


def percentile_from_values(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[index]


def load_samples(paths: list[Path]) -> list[Sample]:
    samples: list[Sample] = []
    for path in paths:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    timestamp_ms = int(float(row["timeStamp"]))
                    elapsed_ms = int(float(row["elapsed"]))
                except (KeyError, TypeError, ValueError):
                    continue
                success = row.get("success", "").strip().lower() != "false"
                samples.append(Sample(timestamp_ms=timestamp_ms, elapsed_ms=elapsed_ms, success=success))
    samples.sort(key=lambda item: item.timestamp_ms)
    return samples


def group_into_windows(samples: list[Sample], window_size_s: float) -> list[WindowStats]:
    if not samples:
        return []

    window_size_ms = max(1, int(window_size_s * 1000))
    start_ms = samples[0].timestamp_ms
    buckets: dict[int, list[Sample]] = {}

    for sample in samples:
        window_index = (sample.timestamp_ms - start_ms) // window_size_ms
        buckets.setdefault(int(window_index), []).append(sample)

    windows: list[WindowStats] = []
    for window_index in sorted(buckets):
        bucket = buckets[window_index]
        bucket_successes = [sample for sample in bucket if sample.success]
        bucket_failures = [sample for sample in bucket if not sample.success]
        elapsed_values = [sample.elapsed_ms for sample in bucket]
        success_values = [sample.elapsed_ms for sample in bucket_successes]
        failure_values = [sample.elapsed_ms for sample in bucket_failures]

        window_start = start_ms + window_index * window_size_ms
        window_end = window_start + window_size_ms
        total = len(bucket)
        failures = len(bucket_failures)
        successes = len(bucket_successes)

        windows.append(
            WindowStats(
                window_index=window_index,
                start_ms=window_start,
                end_ms=window_end,
                samples=total,
                successes=successes,
                failures=failures,
                error_rate_pct=(failures / total) * 100.0 if total else 0.0,
                throughput_rps=total / window_size_s if window_size_s > 0 else 0.0,
                success_avg_ms=(sum(success_values) / successes) if successes else 0.0,
                success_min_ms=min(success_values) if successes else 0,
                success_p95_ms=percentile_from_values(success_values, 0.95),
                success_max_ms=max(success_values) if successes else 0,
                failed_avg_ms=(sum(failure_values) / failures) if failures else 0.0,
                failed_min_ms=min(failure_values) if failures else 0,
                failed_p95_ms=percentile_from_values(failure_values, 0.95),
                failed_max_ms=max(failure_values) if failures else 0,
                all_avg_ms=(sum(elapsed_values) / total) if total else 0.0,
            )
        )

    return windows


def detect_overload(
    windows: list[WindowStats],
    error_threshold_pct: float,
    consecutive_windows: int,
) -> WindowStats | None:
    if not windows:
        return None

    baseline_throughput = max(window.throughput_rps for window in windows[: min(3, len(windows))])
    if baseline_throughput <= 0:
        baseline_throughput = max(window.throughput_rps for window in windows)

    streak = 0
    for window in windows:
        throughput_drop = window.throughput_rps < (baseline_throughput * 0.9)
        error_spike = window.error_rate_pct >= error_threshold_pct
        if throughput_drop and error_spike:
            streak += 1
            if streak >= consecutive_windows:
                return window
        else:
            streak = 0
    return None


def print_table(windows: list[WindowStats]) -> None:
    headers = [
        "window",
        "start_s",
        "samples",
        "successes",
        "failures",
        "error%",
        "throughput_rps",
        "success_avg_ms",
        "success_p95_ms",
        "failed_avg_ms",
        "all_avg_ms",
    ]
    rows = []
    for window in windows:
        rows.append(
            [
                str(window.window_index),
                f"{window.start_ms / 1000.0:.2f}",
                str(window.samples),
                str(window.successes),
                str(window.failures),
                f"{window.error_rate_pct:.2f}",
                f"{window.throughput_rps:.2f}",
                f"{window.success_avg_ms:.2f}",
                str(window.success_p95_ms),
                f"{window.failed_avg_ms:.2f}",
                f"{window.all_avg_ms:.2f}",
            ]
        )

    widths = [len(header) for header in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    fmt = "  ".join(f"{{:{width}}}" for width in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def write_csv(windows: list[WindowStats], output: Path) -> None:
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "window_index",
                "start_ms",
                "end_ms",
                "samples",
                "successes",
                "failures",
                "error_rate_pct",
                "throughput_rps",
                "success_avg_ms",
                "success_min_ms",
                "success_p95_ms",
                "success_max_ms",
                "failed_avg_ms",
                "failed_min_ms",
                "failed_p95_ms",
                "failed_max_ms",
                "all_avg_ms",
            ]
        )
        for window in windows:
            writer.writerow(
                [
                    window.window_index,
                    window.start_ms,
                    window.end_ms,
                    window.samples,
                    window.successes,
                    window.failures,
                    f"{window.error_rate_pct:.2f}",
                    f"{window.throughput_rps:.2f}",
                    f"{window.success_avg_ms:.2f}",
                    window.success_min_ms,
                    window.success_p95_ms,
                    window.success_max_ms,
                    f"{window.failed_avg_ms:.2f}",
                    window.failed_min_ms,
                    window.failed_p95_ms,
                    window.failed_max_ms,
                    f"{window.all_avg_ms:.2f}",
                ]
            )


def maybe_plot(windows: list[WindowStats], plot_prefix: str, window_size_s: float) -> None:
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit("matplotlib is required for plotting. Install it or omit --plot-prefix.") from exc

    if not windows:
        return

    x = [window.start_ms / 1000.0 for window in windows]
    throughput = [window.throughput_rps for window in windows]
    error_rate = [window.error_rate_pct for window in windows]
    failures = [window.failures for window in windows]
    success_avg = [window.success_avg_ms for window in windows]
    bar_width = max(0.1, window_size_s * 0.8)

    fig, ax1 = plt.subplots(figsize=(10, 4.5))
    ax1.plot(x, throughput, marker="o", color="#1f77b4", label="Throughput (samples/s)")
    ax1.set_xlabel("Time (s)")
    ax1.set_ylabel("Throughput (samples/s)", color="#1f77b4")
    ax1.tick_params(axis="y", labelcolor="#1f77b4")
    ax1.bar(x, failures, width=bar_width, alpha=0.2, color="#d62728", label="Failures")
    ax2 = ax1.twinx()
    ax2.plot(x, error_rate, marker="s", color="#d62728", label="Error rate (%)")
    ax2.set_ylabel("Error rate (%)", color="#d62728")
    ax2.tick_params(axis="y", labelcolor="#d62728")
    fig.tight_layout()
    fig.savefig(f"{plot_prefix}_throughput_error_rate.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(10, 4.5))
    ax.plot(x, success_avg, marker="o", color="#2ca02c", label="Successful latency avg (ms)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Latency (ms)")
    ax2 = ax.twinx()
    ax2.bar(x, failures, width=bar_width, alpha=0.2, color="#d62728", label="Failures")
    ax2.set_ylabel("Failures")
    fig.tight_layout()
    fig.savefig(f"{plot_prefix}_success_latency_failures.png", dpi=160)
    plt.close(fig)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze JMeter .jtl files over time.")
    parser.add_argument("patterns", nargs="*", default=["results_*.jtl"], help="File paths or glob patterns.")
    parser.add_argument("--window-seconds", type=float, default=10.0, help="Time window size in seconds.")
    parser.add_argument("--csv", dest="csv_output", help="Optional CSV output path for the windowed metrics.")
    parser.add_argument(
        "--plot-prefix",
        help="Optional prefix for PNG plots. Generates throughput/error and latency/failure plots.",
    )
    parser.add_argument("--overload-error-threshold", type=float, default=10.0, help="Error threshold in percent.")
    parser.add_argument(
        "--overload-consecutive-windows",
        type=int,
        default=2,
        help="Consecutive windows above the overload threshold required to flag overload.",
    )
    args = parser.parse_args()

    paths: list[Path] = []
    for pattern in args.patterns:
        matches = sorted(Path(p) for p in glob.glob(pattern))
        if matches:
            paths.extend(matches)
        else:
            candidate = Path(pattern)
            if candidate.exists():
                paths.append(candidate)

    unique_paths = []
    seen = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique_paths.append(path)

    if not unique_paths:
        raise SystemExit("No .jtl files matched.")

    samples = load_samples(unique_paths)
    windows = group_into_windows(samples, args.window_seconds)
    print_table(windows)

    overload = detect_overload(
        windows,
        error_threshold_pct=args.overload_error_threshold,
        consecutive_windows=args.overload_consecutive_windows,
    )
    if overload is None:
        print("Overload transition: not detected")
    else:
        print("Overload transition: window %d starting at %.2fs" % (overload.window_index, overload.start_ms / 1000.0))

    if args.csv_output:
        write_csv(windows, Path(args.csv_output))

    if args.plot_prefix:
        maybe_plot(windows, args.plot_prefix, args.window_seconds)


if __name__ == "__main__":
    main()
