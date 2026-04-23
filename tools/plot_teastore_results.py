#!/usr/bin/env python3
"""Generate publication-quality TeaStore plots from recorded measurements."""

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
class RunRecord:
    experiment: str
    manifest: Path
    run: int
    workload: str
    users: int
    ramp_up: int
    duration: int
    jtl_path: Path


@dataclass
class RunMetrics:
    samples: int
    failures: int
    duration_s: float
    throughput_rps: float
    success_throughput_rps: float
    error_rate_pct: float
    avg_ms: float
    p95_ms: int
    success_avg_ms: float
    success_p95_ms: int
    failed_avg_ms: float
    failed_p95_ms: int


@dataclass
class AggregatedPoint:
    experiment: str
    workload: str
    users: int
    ramp_up: int
    duration: int
    runs: int
    throughput_rps_mean: float
    throughput_rps_std: float
    success_throughput_rps_mean: float
    success_throughput_rps_std: float
    error_rate_pct_mean: float
    error_rate_pct_std: float
    avg_ms_mean: float
    avg_ms_std: float
    p95_ms_mean: float
    p95_ms_std: float
    success_avg_ms_mean: float
    success_avg_ms_std: float
    success_p95_ms_mean: float
    success_p95_ms_std: float


@dataclass
class TimeSeriesPoint:
    series: str
    start_s: float
    throughput_rps: float
    error_rate_pct: float
    success_avg_ms: float
    success_p95_ms: float
    failed_avg_ms: float
    failures: int


@dataclass
class ResourcePoint:
    experiment: str
    container_name: str
    timestamp_s: float
    cpu_percent: float
    mem_percent: float


def set_plot_style() -> None:
    import matplotlib as mpl

    mpl.rcParams.update(
        {
            "figure.figsize": (10.5, 5.5),
            "figure.dpi": 160,
            "savefig.dpi": 240,
            "font.size": 11,
            "axes.titlesize": 14,
            "axes.labelsize": 12,
            "legend.fontsize": 10,
            "xtick.labelsize": 10,
            "ytick.labelsize": 10,
            "axes.grid": True,
            "grid.alpha": 0.25,
            "grid.linestyle": "--",
            "axes.spines.top": False,
            "axes.spines.right": False,
            "lines.linewidth": 2.0,
            "lines.markersize": 6,
        }
    )


def expand_inputs(patterns: list[str], suffixes: tuple[str, ...]) -> list[Path]:
    paths: list[Path] = []
    for pattern in patterns:
        candidate = Path(pattern)
        if candidate.is_dir():
            for suffix in suffixes:
                paths.extend(sorted(candidate.rglob(f"*{suffix}")))
            continue

        matches = sorted(Path(match) for match in glob.glob(pattern))
        if matches:
            paths.extend(matches)
        elif candidate.exists():
            paths.append(candidate)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = str(path.resolve())
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


def infer_experiment_label(path: Path) -> str:
    parent = path.parent.name
    if parent in {"resources", "timeseries", "results"} and path.parent.parent.name:
        return path.parent.parent.name
    if parent:
        return parent
    return path.stem


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * q) - 1))
    return ordered[index]


def load_run_records(manifests: list[Path]) -> list[RunRecord]:
    records: list[RunRecord] = []
    for manifest in manifests:
        experiment = infer_experiment_label(manifest)
        with manifest.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    run = int(row["run"])
                    users = int(row["users"])
                    ramp_up = int(row["ramp_up"])
                    duration = int(row["duration"])
                    jtl_raw = row["jtl_file"].strip()
                    workload = row["workload"].strip()
                except (KeyError, TypeError, ValueError):
                    continue

                jtl_path = Path(jtl_raw)
                if not jtl_path.is_absolute():
                    jtl_path = (manifest.parent / jtl_path).resolve()

                records.append(
                    RunRecord(
                        experiment=experiment,
                        manifest=manifest,
                        run=run,
                        workload=workload,
                        users=users,
                        ramp_up=ramp_up,
                        duration=duration,
                        jtl_path=jtl_path,
                    )
                )
    return records


def summarize_jtl(path: Path) -> RunMetrics:
    total = 0
    failures = 0
    earliest_ts: int | None = None
    latest_ts: int | None = None

    all_elapsed: list[int] = []
    success_elapsed: list[int] = []
    failed_elapsed: list[int] = []

    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            try:
                elapsed = int(float(row["elapsed"]))
                timestamp = int(float(row["timeStamp"]))
            except (KeyError, TypeError, ValueError):
                continue

            total += 1
            all_elapsed.append(elapsed)

            is_failed = row.get("success", "").strip().lower() == "false"
            if is_failed:
                failures += 1
                failed_elapsed.append(elapsed)
            else:
                success_elapsed.append(elapsed)

            if earliest_ts is None or timestamp < earliest_ts:
                earliest_ts = timestamp
            if latest_ts is None or timestamp > latest_ts:
                latest_ts = timestamp

    duration_s = ((latest_ts or 0) - (earliest_ts or 0)) / 1000.0
    throughput_rps = total / duration_s if duration_s > 0 else 0.0
    success_throughput_rps = len(success_elapsed) / duration_s if duration_s > 0 else 0.0

    return RunMetrics(
        samples=total,
        failures=failures,
        duration_s=duration_s,
        throughput_rps=throughput_rps,
        success_throughput_rps=success_throughput_rps,
        error_rate_pct=(failures / total) * 100.0 if total else 0.0,
        avg_ms=(sum(all_elapsed) / total) if total else 0.0,
        p95_ms=int(percentile(all_elapsed, 0.95)) if all_elapsed else 0,
        success_avg_ms=(sum(success_elapsed) / len(success_elapsed)) if success_elapsed else 0.0,
        success_p95_ms=int(percentile(success_elapsed, 0.95)) if success_elapsed else 0,
        failed_avg_ms=(sum(failed_elapsed) / len(failed_elapsed)) if failed_elapsed else 0.0,
        failed_p95_ms=int(percentile(failed_elapsed, 0.95)) if failed_elapsed else 0,
    )


def aggregate_runs(records: list[RunRecord]) -> tuple[list[tuple[RunRecord, RunMetrics]], list[AggregatedPoint]]:
    per_run: list[tuple[RunRecord, RunMetrics]] = []
    grouped: dict[tuple[str, str, int, int, int], list[RunMetrics]] = defaultdict(list)

    for record in records:
        metrics = summarize_jtl(record.jtl_path)
        per_run.append((record, metrics))
        grouped[(record.experiment, record.workload, record.users, record.ramp_up, record.duration)].append(metrics)

    aggregated: list[AggregatedPoint] = []
    for (experiment, workload, users, ramp_up, duration), items in grouped.items():
        def mean_std(values: list[float]) -> tuple[float, float]:
            if not values:
                return 0.0, 0.0
            if len(values) == 1:
                return values[0], 0.0
            return statistics.mean(values), statistics.pstdev(values)

        throughput_values = [item.throughput_rps for item in items]
        success_throughput_values = [item.success_throughput_rps for item in items]
        error_rate_values = [item.error_rate_pct for item in items]
        avg_values = [item.avg_ms for item in items]
        p95_values = [float(item.p95_ms) for item in items]
        success_avg_values = [item.success_avg_ms for item in items]
        success_p95_values = [float(item.success_p95_ms) for item in items]

        aggregated.append(
            AggregatedPoint(
                experiment=experiment,
                workload=workload,
                users=users,
                ramp_up=ramp_up,
                duration=duration,
                runs=len(items),
                throughput_rps_mean=mean_std(throughput_values)[0],
                throughput_rps_std=mean_std(throughput_values)[1],
                success_throughput_rps_mean=mean_std(success_throughput_values)[0],
                success_throughput_rps_std=mean_std(success_throughput_values)[1],
                error_rate_pct_mean=mean_std(error_rate_values)[0],
                error_rate_pct_std=mean_std(error_rate_values)[1],
                avg_ms_mean=mean_std(avg_values)[0],
                avg_ms_std=mean_std(avg_values)[1],
                p95_ms_mean=mean_std(p95_values)[0],
                p95_ms_std=mean_std(p95_values)[1],
                success_avg_ms_mean=mean_std(success_avg_values)[0],
                success_avg_ms_std=mean_std(success_avg_values)[1],
                success_p95_ms_mean=mean_std(success_p95_values)[0],
                success_p95_ms_std=mean_std(success_p95_values)[1],
            )
        )

    aggregated.sort(key=lambda item: (item.experiment, item.workload, item.users, item.ramp_up, item.duration))
    return per_run, aggregated


def group_key(record: RunRecord) -> str:
    return f"{record.experiment}/{record.workload}"


def plot_metric_vs_users(
    aggregated: list[AggregatedPoint],
    metric: str,
    ylabel: str,
    output: Path,
    title: str,
) -> None:
    import matplotlib.pyplot as plt

    if not aggregated:
        return

    by_series: dict[str, list[AggregatedPoint]] = defaultdict(list)
    for point in aggregated:
        by_series[f"{point.experiment}/{point.workload}"].append(point)

    fig, ax = plt.subplots(figsize=(10.5, 5.5))
    palette = plt.get_cmap("tab10")

    for index, (series, points) in enumerate(sorted(by_series.items())):
        points = sorted(points, key=lambda item: item.users)
        x = [item.users for item in points]
        y = [getattr(item, metric) for item in points]
        yerr = [
            getattr(item, f"{metric.split('_mean')[0]}_std", 0.0) if metric.endswith("_mean") else 0.0
            for item in points
        ]
        ax.errorbar(
            x,
            y,
            yerr=yerr if any(value > 0 for value in yerr) else None,
            marker="o",
            capsize=3,
            color=palette(index % 10),
            label=series,
        )

    ax.set_xlabel("Concurrent users")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.legend(loc="best", frameon=True)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def plot_workload_boxplot(
    per_run: list[tuple[RunRecord, RunMetrics]],
    metric: str,
    ylabel: str,
    output: Path,
    title: str,
) -> None:
    import matplotlib.pyplot as plt

    if not per_run:
        return

    grouped: dict[str, list[float]] = defaultdict(list)
    for record, metrics in per_run:
        grouped[group_key(record)].append(float(getattr(metrics, metric)))

    labels = sorted(grouped)
    data = [grouped[label] for label in labels]

    fig, ax = plt.subplots(figsize=(max(10.0, len(labels) * 1.5), 5.6))
    box = ax.boxplot(data, labels=labels, patch_artist=True, showmeans=True)
    palette = plt.get_cmap("Set3")
    for index, patch in enumerate(box["boxes"]):
        patch.set_facecolor(palette(index % 12))
        patch.set_alpha(0.9)
    for median in box["medians"]:
        median.set_color("#333333")
        median.set_linewidth(2.0)

    ax.set_xlabel("Experiment / workload")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.tick_params(axis="x", rotation=20)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def detect_overload(windows: list[dict[str, float]]) -> int | None:
    if not windows:
        return None

    baseline = max((window["throughput_rps"] for window in windows[: min(3, len(windows))]), default=0.0)
    if baseline <= 0:
        baseline = max(window["throughput_rps"] for window in windows)

    for window in windows:
        throughput_drop = window["throughput_rps"] < baseline * 0.9
        error_spike = window["error_rate_pct"] >= 10.0
        if throughput_drop and error_spike:
            return int(window["window_index"])
    return None


def load_time_series(paths: list[Path]) -> dict[str, list[TimeSeriesPoint]]:
    series: dict[str, list[TimeSeriesPoint]] = defaultdict(list)
    for path in paths:
        label = f"{infer_experiment_label(path)}/{path.stem}"
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    start_s = float(row["start_ms"]) / 1000.0
                    throughput_rps = float(row["throughput_rps"])
                    error_rate_pct = float(row["error_rate_pct"])
                    success_avg_ms = float(row["success_avg_ms"])
                    success_p95_ms = float(row["success_p95_ms"])
                    failed_avg_ms = float(row["failed_avg_ms"])
                    failures = int(float(row["failures"]))
                except (KeyError, TypeError, ValueError):
                    continue
                series[label].append(
                    TimeSeriesPoint(
                        series=label,
                        start_s=start_s,
                        throughput_rps=throughput_rps,
                        error_rate_pct=error_rate_pct,
                        success_avg_ms=success_avg_ms,
                        success_p95_ms=success_p95_ms,
                        failed_avg_ms=failed_avg_ms,
                        failures=failures,
                    )
                )
        series[label].sort(key=lambda item: item.start_s)
    return series


def plot_time_series(series_by_label: dict[str, list[TimeSeriesPoint]], output: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    if not series_by_label:
        return

    fig, axes = plt.subplots(3, 1, figsize=(11.5, 10.0), sharex=True)
    palette = plt.get_cmap("tab10")

    for index, (label, points) in enumerate(sorted(series_by_label.items())):
        color = palette(index % 10)
        x = [point.start_s for point in points]
        throughput = [point.throughput_rps for point in points]
        error_rate = [point.error_rate_pct for point in points]
        success_avg = [point.success_avg_ms for point in points]
        failed_avg = [point.failed_avg_ms for point in points]
        failures = [point.failures for point in points]

        overload_index = detect_overload(
            [
                {"window_index": idx, "throughput_rps": point.throughput_rps, "error_rate_pct": point.error_rate_pct}
                for idx, point in enumerate(points)
            ]
        )
        overload_time = x[overload_index] if overload_index is not None and overload_index < len(x) else None

        axes[0].plot(x, throughput, marker="o", color=color, label=label)
        axes[1].plot(x, error_rate, marker="s", color=color, label=label)
        axes[2].plot(x, success_avg, marker="o", color=color, label=f"{label} success avg")
        if any(value > 0 for value in failures):
            axes[2].bar(
                x,
                failures,
                width=max(0.1, (x[1] - x[0]) * 0.8) if len(x) > 1 else 0.6,
                alpha=0.15,
                color=color,
                label=f"{label} failures",
            )
        if overload_time is not None:
            for axis in axes:
                axis.axvline(overload_time, color="#8c564b", linestyle="--", alpha=0.45)

        if failed_avg and any(value > 0 for value in failed_avg):
            axes[2].plot(x, failed_avg, marker="^", linestyle="--", color=color, alpha=0.75)

    axes[0].set_ylabel("Throughput (samples/s)")
    axes[0].set_title(title)
    axes[1].set_ylabel("Error rate (%)")
    axes[2].set_ylabel("Latency / failures")
    axes[2].set_xlabel("Time (s)")
    axes[0].legend(loc="best", frameon=True)
    axes[1].legend(loc="best", frameon=True)
    axes[2].legend(loc="best", frameon=True, ncols=2)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def load_resource_points(paths: list[Path]) -> dict[str, dict[str, list[ResourcePoint]]]:
    series: dict[str, dict[str, list[ResourcePoint]]] = defaultdict(lambda: defaultdict(list))
    for path in paths:
        experiment = infer_experiment_label(path)
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                try:
                    timestamp_s = float(row["timestamp_ms"]) / 1000.0
                    container = row["container_name"].strip() or row["container_id"].strip()
                    cpu_percent = float(row["cpu_percent"])
                    mem_percent = float(row["mem_percent"])
                except (KeyError, TypeError, ValueError):
                    continue
                series[experiment][container].append(
                    ResourcePoint(
                        experiment=experiment,
                        container_name=container,
                        timestamp_s=timestamp_s,
                        cpu_percent=cpu_percent,
                        mem_percent=mem_percent,
                    )
                )

    for experiment in series:
        for container in series[experiment]:
            series[experiment][container].sort(key=lambda item: item.timestamp_s)
    return series


def plot_resource_timeseries(series: dict[str, dict[str, list[ResourcePoint]]], output: Path, title: str) -> None:
    import matplotlib.pyplot as plt

    if not series:
        return

    containers = sorted({container for experiment in series.values() for container in experiment})
    if not containers:
        return

    cols = 2 if len(containers) > 1 else 1
    rows = math.ceil(len(containers) / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(12.5, 4.8 * rows), sharex=False)
    if rows == 1 and cols == 1:
        axes_list = [axes]
    elif rows == 1 or cols == 1:
        axes_list = list(axes)
    else:
        axes_list = [ax for row in axes for ax in row]

    palette = plt.get_cmap("tab10")
    for index, container in enumerate(containers):
        ax = axes_list[index]
        for series_index, (experiment, containers_by_name) in enumerate(sorted(series.items())):
            points = containers_by_name.get(container)
            if not points:
                continue
            x = [point.timestamp_s for point in points]
            cpu = [point.cpu_percent for point in points]
            mem = [point.mem_percent for point in points]
            color = palette(series_index % 10)
            ax.plot(x, cpu, marker="o", color=color, label=f"{experiment} CPU")
            ax.plot(x, mem, marker="s", color=color, linestyle="--", label=f"{experiment} MEM")

        ax.set_title(container)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Percent")
        ax.legend(loc="best", frameon=True, fontsize=8)

    for ax in axes_list[len(containers) :]:
        ax.set_visible(False)

    fig.suptitle(title, y=1.01, fontsize=15)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def ensure_output_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate publication-quality TeaStore plots.")
    parser.add_argument("--manifests", nargs="*", default=[], help="Manifest CSV files or directories.")
    parser.add_argument("--timeseries", nargs="*", default=[], help="Windowed CSV files or directories.")
    parser.add_argument("--resources", nargs="*", default=[], help="Docker stats CSV files or directories.")
    parser.add_argument("--output-dir", default="plots", help="Directory for generated PNG files.")
    parser.add_argument("--prefix", default="teastore", help="Filename prefix for generated figures.")
    args = parser.parse_args()

    output_dir = ensure_output_dir(Path(args.output_dir))
    manifests = expand_inputs(args.manifests, ("runs.csv", "manifest.csv"))
    timeseries = expand_inputs(args.timeseries, ("timeseries.csv",))
    resources = expand_inputs(args.resources, ("combined.csv",))

    if not manifests and not timeseries and not resources:
        raise SystemExit("No input files matched.")

    try:
        import matplotlib.pyplot as plt  # noqa: F401
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SystemExit("matplotlib is required for plotting. Install it first.") from exc

    set_plot_style()

    if manifests:
        per_run, aggregated = aggregate_runs(load_run_records(manifests))
        if aggregated:
            plot_metric_vs_users(
                aggregated,
                "success_avg_ms_mean",
                "Successful latency avg (ms)",
                output_dir / f"{args.prefix}_success_avg_vs_users.png",
                "Successful response time vs concurrent users",
            )
            plot_metric_vs_users(
                aggregated,
                "success_p95_ms_mean",
                "Successful latency p95 (ms)",
                output_dir / f"{args.prefix}_success_p95_vs_users.png",
                "p95 latency vs concurrent users",
            )
            plot_metric_vs_users(
                aggregated,
                "error_rate_pct_mean",
                "Error rate (%)",
                output_dir / f"{args.prefix}_error_rate_vs_users.png",
                "Error rate vs concurrent users",
            )
            plot_metric_vs_users(
                aggregated,
                "throughput_rps_mean",
                "Throughput (samples/s)",
                output_dir / f"{args.prefix}_throughput_vs_users.png",
                "Throughput vs concurrent users",
            )
            plot_metric_vs_users(
                aggregated,
                "success_throughput_rps_mean",
                "Successful throughput (samples/s)",
                output_dir / f"{args.prefix}_success_throughput_vs_users.png",
                "Successful throughput vs concurrent users",
            )

        if per_run:
            plot_workload_boxplot(
                per_run,
                "success_p95_ms",
                "Successful latency p95 (ms)",
                output_dir / f"{args.prefix}_workload_success_p95_boxplot.png",
                "p95 latency by experiment / workload",
            )
            plot_workload_boxplot(
                per_run,
                "throughput_rps",
                "Throughput (samples/s)",
                output_dir / f"{args.prefix}_workload_throughput_boxplot.png",
                "Throughput distribution by experiment / workload",
            )

    if timeseries:
        series = load_time_series(timeseries)
        if series:
            plot_time_series(series, output_dir / f"{args.prefix}_timeseries.png", "Latency, throughput, and error rate over time")

    if resources:
        resource_series = load_resource_points(resources)
        if resource_series:
            plot_resource_timeseries(
                resource_series,
                output_dir / f"{args.prefix}_resources.png",
                "CPU and memory usage over time by service",
            )

    print(f"Figures written to: {output_dir}")


if __name__ == "__main__":
    main()
