#!/usr/bin/env python3
"""Summarize JMeter .jtl result files and optionally write CSV output."""

from __future__ import annotations

import argparse
import csv
import glob
from dataclasses import dataclass
from pathlib import Path


@dataclass
class LatencyStats:
    samples: int
    avg_ms: float
    min_ms: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    max_ms: int


@dataclass
class Summary:
    file: str
    samples: int
    failures: int
    error_rate_pct: float
    duration_s: float
    throughput_rps: float
    all_latency: LatencyStats
    success_latency: LatencyStats
    failed_latency: LatencyStats


def percentile_from_histogram(histogram: dict[int, int], total: int, q: float) -> int:
    if total == 0:
        return 0
    threshold = max(1, int(total * q + 0.999999999))
    running = 0
    for value in sorted(histogram):
        running += histogram[value]
        if running >= threshold:
            return value
    return max(histogram, default=0)


def summarize_file(path: Path) -> Summary:
    total = 0
    failures = 0
    earliest_ts: int | None = None
    latest_ts: int | None = None

    all_histogram: dict[int, int] = {}
    all_elapsed_sum = 0
    all_min_elapsed: int | None = None
    all_max_elapsed = 0

    success_histogram: dict[int, int] = {}
    success_elapsed_sum = 0
    success_min_elapsed: int | None = None
    success_max_elapsed = 0
    success_count = 0

    failed_histogram: dict[int, int] = {}
    failed_elapsed_sum = 0
    failed_min_elapsed: int | None = None
    failed_max_elapsed = 0
    failed_count = 0

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                elapsed = int(float(row["elapsed"]))
                timestamp = int(float(row["timeStamp"]))
            except (KeyError, TypeError, ValueError):
                continue

            total += 1
            all_elapsed_sum += elapsed
            all_histogram[elapsed] = all_histogram.get(elapsed, 0) + 1

            is_failed = row.get("success", "").strip().lower() == "false"
            if is_failed:
                failures += 1
                failed_count += 1
                failed_elapsed_sum += elapsed
                failed_histogram[elapsed] = failed_histogram.get(elapsed, 0) + 1
                if failed_min_elapsed is None or elapsed < failed_min_elapsed:
                    failed_min_elapsed = elapsed
                if elapsed > failed_max_elapsed:
                    failed_max_elapsed = elapsed
            else:
                success_count += 1
                success_elapsed_sum += elapsed
                success_histogram[elapsed] = success_histogram.get(elapsed, 0) + 1
                if success_min_elapsed is None or elapsed < success_min_elapsed:
                    success_min_elapsed = elapsed
                if elapsed > success_max_elapsed:
                    success_max_elapsed = elapsed

            if all_min_elapsed is None or elapsed < all_min_elapsed:
                all_min_elapsed = elapsed
            if elapsed > all_max_elapsed:
                all_max_elapsed = elapsed
            if earliest_ts is None or timestamp < earliest_ts:
                earliest_ts = timestamp
            if latest_ts is None or timestamp > latest_ts:
                latest_ts = timestamp

    if total == 0:
        return Summary(
            file=path.name,
            samples=0,
            failures=0,
            error_rate_pct=0.0,
            duration_s=0.0,
            throughput_rps=0.0,
            all_latency=LatencyStats(0, 0.0, 0, 0, 0, 0, 0),
            success_latency=LatencyStats(0, 0.0, 0, 0, 0, 0, 0),
            failed_latency=LatencyStats(0, 0.0, 0, 0, 0, 0, 0),
        )

    duration_s = ((latest_ts or 0) - (earliest_ts or 0)) / 1000.0
    throughput_rps = (total / duration_s) if duration_s > 0 else 0.0

    all_latency = LatencyStats(
        samples=total,
        avg_ms=all_elapsed_sum / total,
        min_ms=all_min_elapsed or 0,
        p50_ms=percentile_from_histogram(all_histogram, total, 0.50),
        p95_ms=percentile_from_histogram(all_histogram, total, 0.95),
        p99_ms=percentile_from_histogram(all_histogram, total, 0.99),
        max_ms=all_max_elapsed,
    )

    success_latency = LatencyStats(
        samples=success_count,
        avg_ms=(success_elapsed_sum / success_count) if success_count else 0.0,
        min_ms=success_min_elapsed or 0,
        p50_ms=percentile_from_histogram(success_histogram, success_count, 0.50) if success_count else 0,
        p95_ms=percentile_from_histogram(success_histogram, success_count, 0.95) if success_count else 0,
        p99_ms=percentile_from_histogram(success_histogram, success_count, 0.99) if success_count else 0,
        max_ms=success_max_elapsed,
    )

    failed_latency = LatencyStats(
        samples=failed_count,
        avg_ms=(failed_elapsed_sum / failed_count) if failed_count else 0.0,
        min_ms=failed_min_elapsed or 0,
        p50_ms=percentile_from_histogram(failed_histogram, failed_count, 0.50) if failed_count else 0,
        p95_ms=percentile_from_histogram(failed_histogram, failed_count, 0.95) if failed_count else 0,
        p99_ms=percentile_from_histogram(failed_histogram, failed_count, 0.99) if failed_count else 0,
        max_ms=failed_max_elapsed,
    )

    return Summary(
        file=path.name,
        samples=total,
        failures=failures,
        error_rate_pct=(failures / total) * 100.0,
        duration_s=duration_s,
        throughput_rps=throughput_rps,
        all_latency=all_latency,
        success_latency=success_latency,
        failed_latency=failed_latency,
    )


def print_table(summaries: list[Summary]) -> None:
    headers = [
        "file",
        "samples",
        "failures",
        "error%",
        "duration_s",
        "throughput_rps",
        "avg_ms",
        "p95_ms",
        "success_avg_ms",
        "success_p95_ms",
        "failed_avg_ms",
    ]
    rows = [
        [
            s.file,
            str(s.samples),
            str(s.failures),
            f"{s.error_rate_pct:.2f}",
            f"{s.duration_s:.2f}",
            f"{s.throughput_rps:.2f}",
            f"{s.all_latency.avg_ms:.2f}",
            str(s.all_latency.p95_ms),
            f"{s.success_latency.avg_ms:.2f}",
            str(s.success_latency.p95_ms),
            f"{s.failed_latency.avg_ms:.2f}",
        ]
        for s in summaries
    ]
    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def write_csv(summaries: list[Summary], output: Path) -> None:
    with output.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "file",
                "samples",
                "failures",
                "error_rate_pct",
                "duration_s",
                "throughput_rps",
                "all_avg_ms",
                "all_min_ms",
                "all_p50_ms",
                "all_p95_ms",
                "all_p99_ms",
                "all_max_ms",
                "success_samples",
                "success_avg_ms",
                "success_min_ms",
                "success_p50_ms",
                "success_p95_ms",
                "success_p99_ms",
                "success_max_ms",
                "failed_samples",
                "failed_avg_ms",
                "failed_min_ms",
                "failed_p50_ms",
                "failed_p95_ms",
                "failed_p99_ms",
                "failed_max_ms",
            ]
        )
        for s in summaries:
            writer.writerow(
                [
                    s.file,
                    s.samples,
                    s.failures,
                    f"{s.error_rate_pct:.2f}",
                    f"{s.duration_s:.2f}",
                    f"{s.throughput_rps:.2f}",
                    f"{s.all_latency.avg_ms:.2f}",
                    s.all_latency.min_ms,
                    s.all_latency.p50_ms,
                    s.all_latency.p95_ms,
                    s.all_latency.p99_ms,
                    s.all_latency.max_ms,
                    s.success_latency.samples,
                    f"{s.success_latency.avg_ms:.2f}",
                    s.success_latency.min_ms,
                    s.success_latency.p50_ms,
                    s.success_latency.p95_ms,
                    s.success_latency.p99_ms,
                    s.success_latency.max_ms,
                    s.failed_latency.samples,
                    f"{s.failed_latency.avg_ms:.2f}",
                    s.failed_latency.min_ms,
                    s.failed_latency.p50_ms,
                    s.failed_latency.p95_ms,
                    s.failed_latency.p99_ms,
                    s.failed_latency.max_ms,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize JMeter .jtl result files.")
    parser.add_argument("patterns", nargs="*", default=["results_*.jtl"], help="File paths or glob patterns.")
    parser.add_argument("--csv", dest="csv_output", help="Optional output CSV path for the summary table.")
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

    summaries = [summarize_file(path) for path in unique_paths]
    print_table(summaries)

    if args.csv_output:
        write_csv(summaries, Path(args.csv_output))


if __name__ == "__main__":
    main()
