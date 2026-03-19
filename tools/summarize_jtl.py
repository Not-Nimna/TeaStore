#!/usr/bin/env python3
"""Summarize JMeter .jtl result files and optionally write CSV output."""

from __future__ import annotations

import argparse
import csv
import glob
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Summary:
    file: str
    samples: int
    failures: int
    error_rate_pct: float
    duration_s: float
    throughput_rps: float
    avg_ms: float
    min_ms: int
    p50_ms: int
    p95_ms: int
    p99_ms: int
    max_ms: int


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
    elapsed_sum = 0
    min_elapsed: int | None = None
    max_elapsed = 0
    earliest_ts: int | None = None
    latest_ts: int | None = None
    histogram: dict[int, int] = {}

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                elapsed = int(float(row["elapsed"]))
                timestamp = int(float(row["timeStamp"]))
            except (KeyError, TypeError, ValueError):
                continue

            total += 1
            elapsed_sum += elapsed
            histogram[elapsed] = histogram.get(elapsed, 0) + 1

            if row.get("success", "").strip().lower() == "false":
                failures += 1

            if min_elapsed is None or elapsed < min_elapsed:
                min_elapsed = elapsed
            if elapsed > max_elapsed:
                max_elapsed = elapsed
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
            avg_ms=0.0,
            min_ms=0,
            p50_ms=0,
            p95_ms=0,
            p99_ms=0,
            max_ms=0,
        )

    duration_s = ((latest_ts or 0) - (earliest_ts or 0)) / 1000.0
    throughput_rps = (total / duration_s) if duration_s > 0 else 0.0

    return Summary(
        file=path.name,
        samples=total,
        failures=failures,
        error_rate_pct=(failures / total) * 100.0,
        duration_s=duration_s,
        throughput_rps=throughput_rps,
        avg_ms=elapsed_sum / total,
        min_ms=min_elapsed or 0,
        p50_ms=percentile_from_histogram(histogram, total, 0.50),
        p95_ms=percentile_from_histogram(histogram, total, 0.95),
        p99_ms=percentile_from_histogram(histogram, total, 0.99),
        max_ms=max_elapsed,
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
        "p50_ms",
        "p95_ms",
        "p99_ms",
        "max_ms",
    ]
    rows = [
        [
            s.file,
            str(s.samples),
            str(s.failures),
            f"{s.error_rate_pct:.2f}",
            f"{s.duration_s:.2f}",
            f"{s.throughput_rps:.2f}",
            f"{s.avg_ms:.2f}",
            str(s.p50_ms),
            str(s.p95_ms),
            str(s.p99_ms),
            str(s.max_ms),
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
                "avg_ms",
                "min_ms",
                "p50_ms",
                "p95_ms",
                "p99_ms",
                "max_ms",
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
                    f"{s.avg_ms:.2f}",
                    s.min_ms,
                    s.p50_ms,
                    s.p95_ms,
                    s.p99_ms,
                    s.max_ms,
                ]
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize JMeter .jtl files.")
    parser.add_argument(
        "patterns",
        nargs="*",
        default=["results_*.jtl"],
        help="File paths or glob patterns to summarize. Defaults to results_*.jtl.",
    )
    parser.add_argument(
        "--csv",
        dest="csv_output",
        help="Optional output CSV path for the summary table.",
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

    summaries = [summarize_file(path) for path in unique_paths]
    print_table(summaries)

    if args.csv_output:
        write_csv(summaries, Path(args.csv_output))


if __name__ == "__main__":
    main()
