"""Show and persist the current status of a STAR-CCM+ flow-control run."""

from __future__ import annotations

import argparse
import re
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from flow_control.star_ingest.manifest_builder import read_star_runtime_metadata


ERROR_MARKERS = (
    "UCX ERROR",
    "Failed to create UCP",
    "selected pml",
    "mpi_errors_are_fatal",
    "pam_slurm_adopt",
    "Authentication failed",
    "Fatal",
    "Exception",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Show STAR-CCM+ Slurm, MPI, step, and error status.")
    parser.add_argument("--out", required=True, help="raw_star directory or its parent case directory.")
    parser.add_argument("--tail", type=int, default=5, help="Number of recent log lines to print.")
    args = parser.parse_args(argv)

    raw_dir, manifest_path = _resolve_run_paths(Path(args.out))
    manifest = _read_or_bootstrap_manifest(manifest_path, raw_dir)
    runtime = dict(manifest.get("runtime") or {})
    star = dict(manifest.get("star") or {})
    log_path = _runtime_path(raw_dir, runtime.get("runtime_log"), "starccm_flow_control.log")
    timeseries_path = raw_dir / "timeseries.csv"

    completed_steps = _csv_data_row_count(timeseries_path)
    total_steps = _as_positive_int(runtime.get("total_steps"))
    progress = round(completed_steps / total_steps * 100.0, 3) if total_steps else None
    slurm_state = _slurm_job_state(str(runtime.get("slurm_job_id") or ""))
    recent_errors = _recent_error_lines(log_path)
    runtime_metadata = read_star_runtime_metadata(log_path) if log_path.is_file() else {}
    actual_processes = (
        dict(runtime_metadata.get("parallel") or {}).get("total_processes")
        or runtime.get("actual_processes")
    )

    runtime.update(
        {
            "last_status_check": datetime.now(timezone.utc).isoformat(),
            "completed_steps": completed_steps,
            "slurm_state": slurm_state,
            "recent_error_count": len(recent_errors),
        }
    )
    if progress is not None:
        runtime["progress_percent"] = progress
    if actual_processes is not None:
        runtime["actual_processes"] = int(actual_processes)
    manifest["runtime"] = runtime
    _write_manifest(manifest_path, manifest)

    print(f"状态: {runtime.get('status', 'unknown')}")
    print(f"STAR-CCM+: {star.get('version', 'unknown')} ({star.get('version_source', 'unknown')})")
    print(f"Slurm Job: {runtime.get('slurm_job_id') or '-'} {slurm_state}")
    nodes = runtime.get("nodes") if isinstance(runtime.get("nodes"), list) else []
    print(f"节点: {len(nodes)}" + (f" ({', '.join(str(node) for node in nodes)})" if nodes else ""))
    requested = runtime.get("requested_processes") or runtime.get("num_cores")
    print(f"MPI进程: actual={actual_processes or '待日志确认'} requested={requested or 'unknown'}")
    if total_steps:
        print(f"Step: {completed_steps}/{total_steps} ({progress:.2f}%)")
    else:
        print(f"Step: {completed_steps}/unknown")
    print(f"日志: {log_path}")
    print(f"Manifest: {manifest_path}")
    if recent_errors:
        print(f"近期错误: {len(recent_errors)}")
        for line in recent_errors[-5:]:
            print(f"  {line}")
    else:
        print("近期错误: none")
    if args.tail > 0 and log_path.is_file():
        print(f"最近 {args.tail} 行日志:")
        for line in _tail_lines(log_path, args.tail):
            print(f"  {line}")
    return 1 if runtime.get("status") == "failed" else 0


def _resolve_run_paths(path: Path) -> tuple[Path, Path]:
    path = path.expanduser().resolve()
    candidates = (
        (path / "raw_star", path / "raw_star" / "case_manifest.yaml"),
        (path, path / "case_manifest.yaml"),
    )
    for raw_dir, manifest_path in candidates:
        if manifest_path.is_file() or (raw_dir / "starccm_flow_control.log").is_file():
            return raw_dir, manifest_path
    raise FileNotFoundError(
        f"no case_manifest.yaml or starccm_flow_control.log found under {path} or {path / 'raw_star'}"
    )


def _read_or_bootstrap_manifest(path: Path, raw_dir: Path) -> dict[str, Any]:
    if not path.is_file():
        return _bootstrap_manifest(raw_dir)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {path}")
    return data


def _bootstrap_manifest(raw_dir: Path) -> dict[str, Any]:
    """Build a minimal status manifest for runs started by an older runner."""

    log_path = raw_dir / "starccm_flow_control.log"
    metadata = read_star_runtime_metadata(log_path) if log_path.is_file() else {}
    runtime_version = dict(metadata.get("runtime") or {})
    parallel = dict(metadata.get("parallel") or {})
    machinefiles = sorted(raw_dir.glob("hosts_*.ma"), key=lambda item: item.stat().st_mtime)
    machinefile = machinefiles[-1] if machinefiles else None
    nodes: list[str] = []
    requested_processes = 0
    job_id = ""
    if machinefile is not None:
        job_match = re.fullmatch(r"hosts_(.+)\.ma", machinefile.name)
        job_id = job_match.group(1) if job_match else ""
        for raw_line in machinefile.read_text(encoding="utf-8", errors="replace").splitlines():
            host, separator, slots = raw_line.strip().partition(":")
            if host:
                nodes.append(host)
            if separator and slots.isdigit():
                requested_processes += int(slots)
    schedule_path = raw_dir / "actuation_schedule.csv"
    total_steps = _csv_data_row_count(schedule_path)
    return {
        "manifest_status": "runtime_status_bootstrapped_from_existing_log",
        "star": {
            "version": runtime_version.get("release_version", "unknown"),
            "version_source": runtime_version.get("source", "runtime_log_pending"),
        },
        "runtime": {
            "status": "running",
            "slurm_job_id": job_id,
            "nodes": nodes,
            "node_count": len(nodes),
            "requested_processes": requested_processes or parallel.get("total_processes"),
            "actual_processes": parallel.get("total_processes"),
            "total_steps": total_steps or None,
            "machinefile": str(machinefile) if machinefile is not None else "",
            "runtime_log": str(log_path),
        },
        "star_runtime_metadata": metadata,
    }


def _write_manifest(path: Path, data: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def _runtime_path(raw_dir: Path, configured: Any, fallback_name: str) -> Path:
    if isinstance(configured, str) and configured:
        path = Path(configured).expanduser()
        return path if path.is_absolute() else raw_dir / path
    return raw_dir / fallback_name


def _csv_data_row_count(path: Path) -> int:
    if not path.is_file():
        return 0
    with path.open(encoding="utf-8", errors="replace") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def _recent_error_lines(path: Path, *, max_lines: int = 400) -> list[str]:
    if not path.is_file():
        return []
    lines = _tail_lines(path, max_lines)
    return [line for line in lines if any(marker.lower() in line.lower() for marker in ERROR_MARKERS)]


def _tail_lines(path: Path, count: int) -> list[str]:
    if count <= 0:
        return []
    with path.open(encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\r\n") for line in deque(handle, maxlen=count)]


def _slurm_job_state(job_id: str) -> str:
    if not job_id:
        return "not-configured"
    try:
        completed = subprocess.run(
            ["squeue", "-j", job_id, "-h", "-o", "%T"],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return "unavailable"
    return completed.stdout.strip() or "not-in-queue"


def _as_positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


if __name__ == "__main__":
    raise SystemExit(main())
