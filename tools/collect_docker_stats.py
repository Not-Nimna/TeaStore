#!/usr/bin/env python3
"""Collect Docker CPU and memory stats at a fixed interval."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


SIZE_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([A-Za-z]+)?\s*$")


@dataclass
class ContainerStat:
    sample_index: int
    timestamp_ms: int
    timestamp_utc: str
    container_id: str
    container_name: str
    cpu_percent: float
    mem_percent: float
    mem_usage_raw: str
    mem_used_bytes: int | None
    mem_limit_bytes: int | None
    net_io_raw: str
    block_io_raw: str
    pids: int | None


def parse_percent(value: str) -> float | None:
    cleaned = value.strip().replace("%", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def parse_size(value: str) -> int | None:
    cleaned = value.strip()
    if not cleaned or cleaned == "0":
        return 0

    match = SIZE_RE.match(cleaned)
    if not match:
        return None

    amount = float(match.group(1))
    unit = (match.group(2) or "B").lower()
    multipliers = {
        "b": 1,
        "kb": 1000,
        "mb": 1000**2,
        "gb": 1000**3,
        "tb": 1000**4,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }
    multiplier = multipliers.get(unit)
    if multiplier is None:
        return None
    return int(amount * multiplier)


def parse_memory_usage(value: str) -> tuple[int | None, int | None]:
    parts = [part.strip() for part in value.split("/")]
    if len(parts) != 2:
        return None, None
    return parse_size(parts[0]), parse_size(parts[1])


def collect_targets(container_filters: list[str] | None) -> list[tuple[str, str]]:
    if container_filters:
        return [(container, container) for container in container_filters]

    result = subprocess.run(
        ["docker", "ps", "--format", "{{.ID}}|{{.Names}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    targets: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        if "|" not in line:
            continue
        container_id, container_name = line.split("|", 1)
        if container_id and container_name:
            targets.append((container_id.strip(), container_name.strip()))
    return targets


def sample_containers(targets: list[tuple[str, str]], sample_index: int) -> list[ContainerStat]:
    if not targets:
        return []

    ids = [container_id for container_id, _ in targets]
    result = subprocess.run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
        check=True,
        capture_output=True,
        text=True,
    )

    timestamp_ms = int(time.time() * 1000)
    timestamp_utc = dt.datetime.now(dt.timezone.utc).isoformat()
    name_by_id = {container_id: container_name for container_id, container_name in targets}
    rows: list[ContainerStat] = []

    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue

        container_id = str(payload.get("Container", "")).strip()
        container_name = name_by_id.get(container_id, str(payload.get("Name", "")).strip())
        cpu_percent = parse_percent(str(payload.get("CPUPerc", ""))) or 0.0
        mem_percent = parse_percent(str(payload.get("MemPerc", ""))) or 0.0
        mem_used_bytes, mem_limit_bytes = parse_memory_usage(str(payload.get("MemUsage", "")))
        pids_raw = str(payload.get("PIDs", "")).strip()
        pids = int(pids_raw) if pids_raw.isdigit() else None

        rows.append(
            ContainerStat(
                sample_index=sample_index,
                timestamp_ms=timestamp_ms,
                timestamp_utc=timestamp_utc,
                container_id=container_id,
                container_name=container_name,
                cpu_percent=cpu_percent,
                mem_percent=mem_percent,
                mem_usage_raw=str(payload.get("MemUsage", "")).strip(),
                mem_used_bytes=mem_used_bytes,
                mem_limit_bytes=mem_limit_bytes,
                net_io_raw=str(payload.get("NetIO", "")).strip(),
                block_io_raw=str(payload.get("BlockIO", "")).strip(),
                pids=pids,
            )
        )

    return rows


def write_header(writer: csv.writer) -> None:
    writer.writerow(
        [
            "sample_index",
            "timestamp_ms",
            "timestamp_utc",
            "container_id",
            "container_name",
            "cpu_percent",
            "mem_percent",
            "mem_usage_raw",
            "mem_used_bytes",
            "mem_limit_bytes",
            "net_io_raw",
            "block_io_raw",
            "pids",
        ]
    )


def write_rows(writer: csv.writer, rows: list[ContainerStat]) -> None:
    for row in rows:
        writer.writerow(
            [
                row.sample_index,
                row.timestamp_ms,
                row.timestamp_utc,
                row.container_id,
                row.container_name,
                f"{row.cpu_percent:.2f}",
                f"{row.mem_percent:.2f}",
                row.mem_usage_raw,
                "" if row.mem_used_bytes is None else row.mem_used_bytes,
                "" if row.mem_limit_bytes is None else row.mem_limit_bytes,
                row.net_io_raw,
                row.block_io_raw,
                "" if row.pids is None else row.pids,
            ]
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect Docker stats to CSV.")
    parser.add_argument("--output-dir", default="docker-stats", help="Directory for output CSV files.")
    parser.add_argument("--prefix", default="collect", help="Prefix for output filenames.")
    parser.add_argument("--interval-seconds", type=float, default=5.0, help="Sampling interval in seconds.")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        help="Optional total duration. If omitted, runs until interrupted.",
    )
    parser.add_argument(
        "--container",
        action="append",
        dest="containers",
        help="Container name or ID to sample. Repeat to sample specific containers only.",
    )
    parser.add_argument(
        "--include-manifest",
        action="store_true",
        help="Write a manifest CSV with the containers observed in each sample.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_started = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    combined_path = output_dir / f"{args.prefix}_{run_started}_combined.csv"
    manifest_path = output_dir / f"{args.prefix}_{run_started}_manifest.csv"

    targets = collect_targets(args.containers)
    if not targets:
        raise SystemExit("No Docker containers found to sample.")

    per_container_files: dict[str, Path] = {}
    per_container_handles = {}
    per_container_writers = {}

    try:
        with combined_path.open("w", newline="") as combined_handle, manifest_path.open("w", newline="") as manifest_handle:
            combined_writer = csv.writer(combined_handle)
            write_header(combined_writer)

            manifest_writer = csv.writer(manifest_handle)
            if args.include_manifest:
                manifest_writer.writerow(
                    ["sample_index", "timestamp_ms", "timestamp_utc", "container_id", "container_name"]
                )

            started = time.time()
            sample_index = 0
            while True:
                now = time.time()
                if args.duration_seconds is not None and sample_index > 0:
                    elapsed = now - started
                    if elapsed >= args.duration_seconds:
                        break

                rows = sample_containers(targets, sample_index)
                if rows:
                    write_rows(combined_writer, rows)
                    combined_handle.flush()

                    if args.include_manifest:
                        for row in rows:
                            manifest_writer.writerow(
                                [row.sample_index, row.timestamp_ms, row.timestamp_utc, row.container_id, row.container_name]
                            )
                        manifest_handle.flush()

                    for row in rows:
                        if row.container_name not in per_container_files:
                            per_container_path = output_dir / f"{args.prefix}_{run_started}_{row.container_name}.csv"
                            per_container_files[row.container_name] = per_container_path
                            handle = per_container_path.open("w", newline="")
                            per_container_handles[row.container_name] = handle
                            writer = csv.writer(handle)
                            per_container_writers[row.container_name] = writer
                            write_header(writer)

                        write_rows(per_container_writers[row.container_name], [row])
                        per_container_handles[row.container_name].flush()

                sample_index += 1

                if args.duration_seconds is not None and (time.time() - started) >= args.duration_seconds:
                    break

                time.sleep(max(0.1, args.interval_seconds))

    except KeyboardInterrupt:
        pass
    finally:
        for handle in per_container_handles.values():
            handle.close()

    print(f"Combined CSV: {combined_path}")
    print(f"Manifest CSV: {manifest_path}")
    print(f"Per-container CSVs: {output_dir}")


if __name__ == "__main__":
    main()
