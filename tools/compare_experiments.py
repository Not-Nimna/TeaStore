#!/usr/bin/env python3
"""Compare two TeaStore experiment sets."""

from __future__ import annotations

import argparse
import csv
import glob
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass
class JtlSample:
    timestamp_ms: int
    elapsed_ms: int
    success: bool


@dataclass
class JtlSummary:
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
    success_samples: int
    success_avg_ms: float
    success_p95_ms: int
    failed_samples: int
    failed_avg_ms: float
    failed_p95_ms: int


@dataclass
class ResourceSummary:
    container_name: str
    samples: int
    avg_cpu_pct: float
    max_cpu_pct: float
    avg_mem_pct: float
    max_mem_pct: float
    max_mem_used_bytes: int | None
    max_pids: int | None


@dataclass
class DistributionStats:
    count: int
    mean: float
    stddev: float
    ci95_low: float
    ci95_high: float


@dataclass
class WelchTestResult:
    t_statistic: float
    degrees_of_freedom: float
    p_value: float
    p_value_method: str


def percentile(values: list[int], q: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    idx = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[idx]


def t_critical_95(df: int) -> float:
    table = {
        1: 12.706,
        2: 4.303,
        3: 3.182,
        4: 2.776,
        5: 2.571,
        6: 2.447,
        7: 2.365,
        8: 2.306,
        9: 2.262,
        10: 2.228,
        11: 2.201,
        12: 2.179,
        13: 2.160,
        14: 2.145,
        15: 2.131,
        16: 2.120,
        17: 2.110,
        18: 2.101,
        19: 2.093,
        20: 2.086,
        21: 2.080,
        22: 2.074,
        23: 2.069,
        24: 2.064,
        25: 2.060,
        26: 2.056,
        27: 2.052,
        28: 2.048,
        29: 2.045,
        30: 2.042,
    }
    return table.get(df, 1.96)


def summarize_distribution(values: list[float]) -> DistributionStats:
    if not values:
        return DistributionStats(0, 0.0, 0.0, 0.0, 0.0)

    mean = statistics.mean(values)
    if len(values) > 1:
        stddev = statistics.stdev(values)
        margin = t_critical_95(len(values) - 1) * (stddev / math.sqrt(len(values)))
    else:
        stddev = 0.0
        margin = 0.0
    return DistributionStats(count=len(values), mean=mean, stddev=stddev, ci95_low=mean - margin, ci95_high=mean + margin)


def welch_t_test(a: list[float], b: list[float]) -> WelchTestResult:
    if not a or not b:
        return WelchTestResult(0.0, 0.0, 1.0, "n/a")

    mean_a = statistics.mean(a)
    mean_b = statistics.mean(b)
    var_a = statistics.variance(a) if len(a) > 1 else 0.0
    var_b = statistics.variance(b) if len(b) > 1 else 0.0

    denom = math.sqrt((var_a / len(a)) + (var_b / len(b)))
    if denom == 0:
        return WelchTestResult(0.0, 0.0, 1.0, "degenerate")

    t_stat = (mean_a - mean_b) / denom
    term_a = (var_a / len(a)) if len(a) > 0 else 0.0
    term_b = (var_b / len(b)) if len(b) > 0 else 0.0
    numerator = (term_a + term_b) ** 2
    denominator = 0.0
    if len(a) > 1 and var_a > 0:
        denominator += (term_a**2) / (len(a) - 1)
    if len(b) > 1 and var_b > 0:
        denominator += (term_b**2) / (len(b) - 1)
    df = numerator / denominator if denominator > 0 else 0.0

    try:
        from scipy import stats  # type: ignore

        p_value = float(2.0 * stats.t.sf(abs(t_stat), df))
        return WelchTestResult(t_stat, df, p_value, "scipy")
    except Exception:
        normal = statistics.NormalDist()
        p_value = float(2.0 * (1.0 - normal.cdf(abs(t_stat))))
        return WelchTestResult(t_stat, df, p_value, "normal-approx")


def expand_paths(patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        matches = sorted(Path(p) for p in glob.glob(pattern))
        if matches:
            paths.extend(matches)
        else:
            candidate = Path(pattern)
            if candidate.exists():
                paths.append(candidate)

    unique_paths: list[Path] = []
    seen = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique_paths.append(path)
    return unique_paths


def load_jtl_samples(paths: list[Path]) -> list[JtlSample]:
    samples: list[JtlSample] = []
    for path in paths:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    timestamp_ms = int(float(row["timeStamp"]))
                    elapsed_ms = int(float(row["elapsed"]))
                except (KeyError, TypeError, ValueError):
                    continue
                samples.append(
                    JtlSample(
                        timestamp_ms=timestamp_ms,
                        elapsed_ms=elapsed_ms,
                        success=row.get("success", "").strip().lower() != "false",
                    )
                )
    samples.sort(key=lambda item: item.timestamp_ms)
    return samples


def summarize_jtl(paths: list[Path]) -> JtlSummary:
    samples = load_jtl_samples(paths)
    if not samples:
        return JtlSummary(0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0, 0, 0, 0.0, 0, 0, 0.0, 0)

    total = len(samples)
    failures = sum(1 for sample in samples if not sample.success)
    elapsed_values = [sample.elapsed_ms for sample in samples]
    success_values = [sample.elapsed_ms for sample in samples if sample.success]
    failed_values = [sample.elapsed_ms for sample in samples if not sample.success]
    duration_s = (samples[-1].timestamp_ms - samples[0].timestamp_ms) / 1000.0
    throughput_rps = total / duration_s if duration_s > 0 else 0.0

    return JtlSummary(
        samples=total,
        failures=failures,
        error_rate_pct=(failures / total) * 100.0,
        duration_s=duration_s,
        throughput_rps=throughput_rps,
        avg_ms=sum(elapsed_values) / total,
        min_ms=min(elapsed_values),
        p50_ms=percentile(elapsed_values, 0.50),
        p95_ms=percentile(elapsed_values, 0.95),
        p99_ms=percentile(elapsed_values, 0.99),
        max_ms=max(elapsed_values),
        success_samples=len(success_values),
        success_avg_ms=(sum(success_values) / len(success_values)) if success_values else 0.0,
        success_p95_ms=percentile(success_values, 0.95),
        failed_samples=len(failed_values),
        failed_avg_ms=(sum(failed_values) / len(failed_values)) if failed_values else 0.0,
        failed_p95_ms=percentile(failed_values, 0.95),
    )


def load_resource_rows(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                rows.append(row)
    return rows


def parse_percent(value: str) -> float | None:
    cleaned = value.strip().replace("%", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_int(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def summarize_resources(paths: list[Path]) -> dict[str, ResourceSummary]:
    rows = load_resource_rows(paths)
    by_container: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        name = row.get("container_name", "").strip() or row.get("container_id", "").strip()
        if name:
            by_container[name].append(row)

    summaries: dict[str, ResourceSummary] = {}
    for name, container_rows in by_container.items():
        cpu_values = [parse_percent(row.get("cpu_percent", "")) or 0.0 for row in container_rows]
        mem_values = [parse_percent(row.get("mem_percent", "")) or 0.0 for row in container_rows]
        used_values = [parse_int(row.get("mem_used_bytes", "")) for row in container_rows]
        pids_values = [parse_int(row.get("pids", "")) for row in container_rows]
        summaries[name] = ResourceSummary(
            container_name=name,
            samples=len(container_rows),
            avg_cpu_pct=sum(cpu_values) / len(cpu_values) if cpu_values else 0.0,
            max_cpu_pct=max(cpu_values) if cpu_values else 0.0,
            avg_mem_pct=sum(mem_values) / len(mem_values) if mem_values else 0.0,
            max_mem_pct=max(mem_values) if mem_values else 0.0,
            max_mem_used_bytes=max((value for value in used_values if value is not None), default=None),
            max_pids=max((value for value in pids_values if value is not None), default=None),
        )
    return summaries


def print_metric_table(baseline: JtlSummary, scaled: JtlSummary) -> None:
    headers = ["metric", "baseline", "scaled", "delta", "delta_pct"]
    rows = []

    def add_row(metric: str, base: float, new: float, fmt: str = "{:.2f}") -> None:
        delta = new - base
        delta_pct = (delta / base * 100.0) if base not in (0, 0.0) else 0.0
        rows.append([metric, fmt.format(base), fmt.format(new), fmt.format(delta), f"{delta_pct:.2f}"])

    add_row("throughput_rps", baseline.throughput_rps, scaled.throughput_rps)
    add_row("avg_ms", baseline.avg_ms, scaled.avg_ms)
    add_row("p95_ms", baseline.p95_ms, scaled.p95_ms, fmt="{:.0f}")
    add_row("error_rate_pct", baseline.error_rate_pct, scaled.error_rate_pct)
    add_row("success_avg_ms", baseline.success_avg_ms, scaled.success_avg_ms)
    add_row("failed_avg_ms", baseline.failed_avg_ms, scaled.failed_avg_ms)

    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def print_distribution_table(
    baseline_name: str,
    scaled_name: str,
    metric_name: str,
    baseline_values: list[float],
    scaled_values: list[float],
) -> None:
    baseline_stats = summarize_distribution(baseline_values)
    scaled_stats = summarize_distribution(scaled_values)

    headers = ["metric", "group", "runs", "mean", "stddev", "ci95_low", "ci95_high"]
    rows = [
        [
            metric_name,
            baseline_name,
            str(baseline_stats.count),
            f"{baseline_stats.mean:.3f}",
            f"{baseline_stats.stddev:.3f}",
            f"{baseline_stats.ci95_low:.3f}",
            f"{baseline_stats.ci95_high:.3f}",
        ],
        [
            metric_name,
            scaled_name,
            str(scaled_stats.count),
            f"{scaled_stats.mean:.3f}",
            f"{scaled_stats.stddev:.3f}",
            f"{scaled_stats.ci95_low:.3f}",
            f"{scaled_stats.ci95_high:.3f}",
        ],
    ]

    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def print_statistical_comparisons(
    baseline_name: str,
    scaled_name: str,
    baseline_summaries: list[JtlSummary],
    scaled_summaries: list[JtlSummary],
) -> None:
    metric_extractors = [
        ("throughput_rps", lambda summary: summary.throughput_rps),
        ("avg_ms", lambda summary: summary.avg_ms),
        ("p95_ms", lambda summary: float(summary.p95_ms)),
        ("error_rate_pct", lambda summary: summary.error_rate_pct),
        ("success_avg_ms", lambda summary: summary.success_avg_ms),
        ("success_p95_ms", lambda summary: float(summary.success_p95_ms)),
    ]

    print("Repeated-run statistics")
    for index, (metric_name, extractor) in enumerate(metric_extractors):
        baseline_values = [extractor(summary) for summary in baseline_summaries]
        scaled_values = [extractor(summary) for summary in scaled_summaries]
        print_distribution_table(baseline_name, scaled_name, metric_name, baseline_values, scaled_values)

        test = welch_t_test(baseline_values, scaled_values)
        print(
            "Welch t-test {metric}: t={t:.4f} df={df:.2f} p={p:.6f} [{method}]".format(
                metric=metric_name,
                t=test.t_statistic,
                df=test.degrees_of_freedom,
                p=test.p_value,
                method=test.p_value_method,
            )
        )
        if index < len(metric_extractors) - 1:
            print()


def print_resource_table(baseline: dict[str, ResourceSummary], scaled: dict[str, ResourceSummary]) -> None:
    names = sorted(set(baseline) | set(scaled))
    if not names:
        return

    headers = ["container", "base_cpu%", "scaled_cpu%", "base_mem%", "scaled_mem%", "base_max_mem", "scaled_max_mem"]
    rows = []
    for name in names:
        base = baseline.get(name)
        new = scaled.get(name)
        rows.append(
            [
                name,
                f"{base.avg_cpu_pct:.2f}" if base else "n/a",
                f"{new.avg_cpu_pct:.2f}" if new else "n/a",
                f"{base.avg_mem_pct:.2f}" if base else "n/a",
                f"{new.avg_mem_pct:.2f}" if new else "n/a",
                "" if base is None or base.max_mem_used_bytes is None else str(base.max_mem_used_bytes),
                "" if new is None or new.max_mem_used_bytes is None else str(new.max_mem_used_bytes),
            ]
        )

    widths = [len(h) for h in headers]
    for row in rows:
        for i, value in enumerate(row):
            widths[i] = max(widths[i], len(value))

    fmt = "  ".join(f"{{:{w}}}" for w in widths)
    print(fmt.format(*headers))
    for row in rows:
        print(fmt.format(*row))


def summarize_single_files(paths: list[Path]) -> list[JtlSummary]:
    summaries: list[JtlSummary] = []
    for path in paths:
        summaries.append(summarize_jtl([path]))
    return summaries


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare two TeaStore experiments.")
    parser.add_argument("--baseline-jtl", nargs="+", required=True, help="Baseline JTL files or glob patterns.")
    parser.add_argument("--scaled-jtl", nargs="+", required=True, help="Scaled JTL files or glob patterns.")
    parser.add_argument(
        "--baseline-resources",
        nargs="*",
        default=[],
        help="Optional baseline Docker stats CSV files or glob patterns.",
    )
    parser.add_argument(
        "--scaled-resources",
        nargs="*",
        default=[],
        help="Optional scaled Docker stats CSV files or glob patterns.",
    )
    args = parser.parse_args()

    baseline_jtl = expand_paths(args.baseline_jtl)
    scaled_jtl = expand_paths(args.scaled_jtl)
    if not baseline_jtl:
        raise SystemExit("No baseline JTL files matched.")
    if not scaled_jtl:
        raise SystemExit("No scaled JTL files matched.")

    baseline_summary = summarize_jtl(baseline_jtl)
    scaled_summary = summarize_jtl(scaled_jtl)
    baseline_run_summaries = summarize_single_files(baseline_jtl)
    scaled_run_summaries = summarize_single_files(scaled_jtl)

    print("JTL comparison")
    print_metric_table(baseline_summary, scaled_summary)
    print()
    print_statistical_comparisons("baseline", "scaled", baseline_run_summaries, scaled_run_summaries)

    if args.baseline_resources or args.scaled_resources:
        baseline_resources = summarize_resources(expand_paths(args.baseline_resources)) if args.baseline_resources else {}
        scaled_resources = summarize_resources(expand_paths(args.scaled_resources)) if args.scaled_resources else {}
        print()
        print("Resource comparison")
        print_resource_table(baseline_resources, scaled_resources)


if __name__ == "__main__":
    main()
