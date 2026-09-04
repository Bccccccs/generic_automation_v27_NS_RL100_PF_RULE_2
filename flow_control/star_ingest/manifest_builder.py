"""Build and finalize STAR case manifests around a CCM run."""

from __future__ import annotations

import csv
import hashlib
import re
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from flow_control.data_schema import INITIAL_TRANSIENT_CROP

MASSFLOW_SIGN_CONVENTION = {
    "raw_columns": "star_actual_massflow_01..star_actual_massflow_24",
    "algorithm_columns": "actual_massflow_01..actual_massflow_24",
    "algorithm_positive_direction": "into_flow_domain",
    "conversion": "actual_massflow_NN = sign_to_domain * star_actual_massflow_NN",
    "sign_to_domain": -1.0,
    "source": "STAR boundary mass-flow report on J01..J24",
}


def prepare_preflight_manifest(*, template_path: Path, sim_path: Path, schedule_path: Path, output_dir: Path, time_step: float | None) -> Path:
    """Fill reproducible host-side fields before STAR is launched."""
    template = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    if not isinstance(template, dict):
        raise ValueError(f"manifest template must be a mapping: {template_path}")
    data = deepcopy(template)
    data["initial_transient_crop"] = deepcopy(INITIAL_TRANSIENT_CROP)
    data["massflow_sign_convention"] = deepcopy(MASSFLOW_SIGN_CONVENTION)
    data["case_id"] = output_dir.name
    data["created_time"] = datetime.now(timezone.utc).isoformat()
    data["source_product_dir"] = "raw_star/out_put"
    data["star"] = {**dict(data.get("star") or {}), "sim_file": sim_path.name, "sim_file_hash_sha256": _sha256(sim_path)}
    solver = dict(data.get("solver_time") or {})
    if time_step is not None:
        solver["time_step"] = float(time_step)
    solver["schedule_file"] = str(schedule_path)
    solver["schedule_hash_sha256"] = _sha256(schedule_path)
    solver["schedule_row_count"] = _csv_row_count(schedule_path)
    data["solver_time"] = solver
    data["manifest_status"] = "preflight_pending_star_template_snapshot"
    target = output_dir / "case_manifest.preflight.yaml"
    target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target


def start_runtime_manifest(
    *,
    preflight_path: Path,
    output_path: Path,
    star: dict[str, Any],
    runtime: dict[str, Any],
) -> Path:
    """Create the visible manifest before launching STAR and retain preflight data."""

    data = _read_mapping(preflight_path)
    data["star"] = {**dict(data.get("star") or {}), **star}
    runtime_data = {**dict(data.get("runtime") or {}), **runtime}
    runtime_data["status"] = "running"
    runtime_data["started_at"] = datetime.now(timezone.utc).isoformat()
    runtime_data.setdefault("finished_at", None)
    runtime_data.setdefault("elapsed_seconds", None)
    runtime_data.setdefault("return_code", None)
    data["runtime"] = runtime_data
    data["manifest_status"] = "runtime_running_pending_star_template_snapshot"
    _write_yaml(preflight_path, data)
    _write_yaml(output_path, data)
    return output_path


def finish_runtime_manifest(
    *,
    manifest_path: Path,
    status: str,
    return_code: int,
    runtime_log_path: Path | None = None,
    completed_steps: int | None = None,
    outputs: dict[str, Any] | None = None,
    failure_summary: str = "",
) -> Path:
    """Finalize runtime fields after either a successful or failed STAR launch."""

    if status not in {"completed", "failed"}:
        raise ValueError(f"unsupported runtime status: {status!r}")
    data = _read_mapping(manifest_path)
    runtime = dict(data.get("runtime") or {})
    finished_at = datetime.now(timezone.utc)
    runtime["status"] = status
    runtime["finished_at"] = finished_at.isoformat()
    runtime["return_code"] = int(return_code)
    started_at = _parse_datetime(runtime.get("started_at"))
    if started_at is not None:
        runtime["elapsed_seconds"] = round((finished_at - started_at).total_seconds(), 3)
    if completed_steps is not None:
        runtime["completed_steps"] = int(completed_steps)
        total_steps = runtime.get("total_steps")
        if isinstance(total_steps, int) and total_steps > 0:
            runtime["progress_percent"] = round(min(completed_steps / total_steps * 100.0, 100.0), 3)
    if failure_summary:
        runtime["failure_summary"] = failure_summary[-4000:]
        runtime["failure_type"] = classify_star_failure(failure_summary)
    data["runtime"] = runtime
    if outputs:
        data["outputs"] = {**dict(data.get("outputs") or {}), **outputs}
    if runtime_log_path is not None and runtime_log_path.is_file():
        metadata = read_star_runtime_metadata(runtime_log_path)
        if metadata:
            _attach_runtime_metadata(data, metadata)
    if status == "failed":
        data["manifest_status"] = "runtime_failed"
    _write_yaml(manifest_path, data)
    return manifest_path


def finalize_manifest(
    *,
    preflight_path: Path,
    snapshot_path: Path,
    output_path: Path,
    runtime_log_path: Path | None = None,
) -> Path:
    """Merge the Macro's pre-solve snapshot into the final case manifest."""
    data = yaml.safe_load(preflight_path.read_text(encoding="utf-8")) or {}
    snapshot = yaml.safe_load(snapshot_path.read_text(encoding="utf-8")) or {}
    if not isinstance(snapshot, dict) or snapshot.get("snapshot_status") != "ok":
        raise ValueError(f"invalid STAR template snapshot: {snapshot_path}")
    data["star_template_snapshot"] = snapshot
    data["surface_properties"] = snapshot.get("surface_properties", {})
    if runtime_log_path is not None and runtime_log_path.is_file():
        runtime_metadata = read_star_runtime_metadata(runtime_log_path)
        if runtime_metadata:
            _attach_runtime_metadata(data, runtime_metadata)
    data["manifest_status"] = "finalized_from_star_template_snapshot"
    output_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output_path


def read_star_runtime_metadata(log_path: Path) -> dict[str, Any]:
    """Read the actual STAR release, input-SIM version, and mesh size from its log."""

    text = log_path.read_text(encoding="utf-8", errors="replace")
    banners = list(
        re.finditer(
            r"Simcenter STAR-CCM\+\s+(?P<product>\S+)\s+Build\s+"
            r"(?P<build>\S+)\s+\((?P<platform>[^)]+)\)",
            text,
        )
    )
    metadata: dict[str, Any] = {}
    if banners:
        runtime = _version_record(banners[0])
        runtime["source"] = "starccm_runtime_log"
        metadata["runtime"] = runtime
    if len(banners) > 1 and "Saved by:" in text[: banners[1].start()]:
        saved_by = _version_record(banners[1])
        saved_by["source"] = "starccm_runtime_log_saved_by"
        metadata["input_sim_saved_by"] = saved_by

    regions: list[dict[str, Any]] = []
    mesh_pattern = re.compile(
        r"^\s*(?P<name>.*?)\s+\(index\s+(?P<index>\d+)\):\s+"
        r"(?P<cells>\d+)\s+cells,\s+(?P<faces>\d+)\s+faces,\s+"
        r"(?P<vertices>\d+)\s+verts\.\s*$",
        re.MULTILINE,
    )
    for match in mesh_pattern.finditer(text):
        regions.append(
            {
                "index": int(match.group("index")),
                "name": match.group("name").strip(),
                "cells": int(match.group("cells")),
                "faces": int(match.group("faces")),
                "vertices": int(match.group("vertices")),
            }
        )
    if regions:
        metadata["mesh"] = {
            "source": "starccm_runtime_log_connectivity",
            "regions": regions,
            "region_count": len(regions),
            "total_cells": sum(region["cells"] for region in regions),
            "total_faces": sum(region["faces"] for region in regions),
            "total_vertices": sum(region["vertices"] for region in regions),
        }
    parallel = _read_parallel_metadata(text)
    if parallel:
        metadata["parallel"] = parallel
    license_match = re.search(r"\bcopy of\s+(?P<feature>\S+)\s+checked out\b", text, re.IGNORECASE)
    if license_match:
        metadata["license"] = {
            "feature": license_match.group("feature"),
            "source": "starccm_runtime_log",
        }
    server_match = re.search(r"Server::start\s+-host\s+(?P<host>[^:\s]+):(?P<port>\d+)", text)
    if server_match:
        metadata["server"] = {
            "host": server_match.group("host"),
            "port": int(server_match.group("port")),
            "source": "starccm_runtime_log",
        }
    return metadata


def classify_star_failure(text: str) -> str:
    lowered = text.lower()
    patterns = (
        ("interrupted", "interrupted by user"),
        ("mpi_pml_mismatch", "selected pml"),
        ("ucx_initialization", "failed to create ucp"),
        ("slurm_access_denied", "pam_slurm_adopt"),
        ("license_failure", "license"),
        ("mpi_failure", "mpi_errors_are_fatal"),
        ("missing_input", "no such file or directory"),
    )
    for failure_type, marker in patterns:
        if marker in lowered:
            return failure_type
    return "starccm_nonzero_exit"


def _version_record(match: re.Match[str]) -> dict[str, str]:
    build = match.group("build")
    platform = match.group("platform")
    release_suffix = re.search(r"(?:^|[-/])r(\d+)(?:$|[-/])", platform, re.IGNORECASE)
    release = f"{build}-R{release_suffix.group(1)}" if release_suffix else build
    return {
        "product_version": match.group("product"),
        "build_version": build,
        "release_version": release,
        "platform": platform,
    }


def _attach_runtime_metadata(data: dict[str, Any], metadata: dict[str, Any]) -> None:
    star = dict(data.get("star") or {})
    runtime = dict(metadata.get("runtime") or {})
    saved_by = dict(metadata.get("input_sim_saved_by") or {})
    mesh = dict(metadata.get("mesh") or {})
    parallel = dict(metadata.get("parallel") or {})

    if runtime:
        star["version"] = runtime["release_version"]
        star["product_version"] = runtime["product_version"]
        star["build_version"] = runtime["build_version"]
        star["platform"] = runtime["platform"]
        star["version_source"] = runtime["source"]
        data["starccm_version"] = runtime["release_version"]
    if saved_by:
        star["input_sim_saved_by"] = saved_by
    if mesh:
        configured_names = star.get("region_names")
        if isinstance(configured_names, list):
            for region in mesh["regions"]:
                index = region["index"]
                if 0 <= index < len(configured_names):
                    region["name"] = str(configured_names[index])
        sim_hash = str(star.get("sim_file_hash_sha256") or "unknown")
        mesh_version = (
            f"sim-{sim_hash[:12]}-c{mesh['total_cells']}"
            f"-f{mesh['total_faces']}-v{mesh['total_vertices']}"
        )
        mesh["version"] = mesh_version
        mesh["version_source"] = "sim_sha256_and_star_runtime_topology"
        star["mesh_version"] = mesh_version
        star["mesh"] = mesh
        data["mesh_version"] = mesh_version
        data["mesh_metadata"] = mesh
    data["star"] = star
    if parallel:
        runtime = dict(data.get("runtime") or {})
        if "total_processes" in parallel:
            runtime["actual_processes"] = parallel["total_processes"]
        if "mpi_distribution" in parallel:
            runtime["mpi_distribution"] = parallel["mpi_distribution"]
        if "hosts" in parallel:
            runtime["actual_process_distribution"] = parallel["hosts"]
        runtime["parallel_source"] = parallel.get("source")
        data["runtime"] = runtime
    if metadata.get("license"):
        star["license"] = metadata["license"]
    if metadata.get("server"):
        runtime = dict(data.get("runtime") or {})
        runtime["server"] = metadata["server"]
        data["runtime"] = runtime
    data["star"] = star
    data["star_runtime_metadata"] = metadata


def _read_parallel_metadata(text: str) -> dict[str, Any]:
    patterns = (
        r"Total number of processes\s*[:=]\s*(\d+)",
        r"total processes\s*[:=]\s*(\d+)",
        r"MPI[^\n]*?\bprocesses\s*[:=]\s*(\d+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            metadata: dict[str, Any] = {
                "total_processes": int(match.group(1)),
                "source": "starccm_runtime_log",
            }
            mpi_match = re.search(r"MPI Distribution\s*:\s*(.+)", text, re.IGNORECASE)
            if mpi_match:
                metadata["mpi_distribution"] = mpi_match.group(1).strip()
            hosts: list[dict[str, Any]] = []
            host_pattern = re.compile(
                r"^Host\s+(?P<index>\d+)\s+--\s+(?P<host>\S+)\s+--\s+"
                r"Ranks\s+(?P<first>\d+)-(?P<last>\d+)\s*$",
                re.MULTILINE,
            )
            for host_match in host_pattern.finditer(text):
                first_rank = int(host_match.group("first"))
                last_rank = int(host_match.group("last"))
                hosts.append(
                    {
                        "index": int(host_match.group("index")),
                        "host": host_match.group("host"),
                        "first_rank": first_rank,
                        "last_rank": last_rank,
                        "rank_count": last_rank - first_rank + 1,
                    }
                )
            if hosts:
                metadata["hosts"] = hosts
            return metadata
    return {}


def _read_mapping(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"manifest must be a mapping: {path}")
    return data


def _write_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    temporary.replace(path)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv_row_count(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))
