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


def prepare_preflight_manifest(*, template_path: Path, sim_path: Path, schedule_path: Path, output_dir: Path, time_step: float | None) -> Path:
    """Fill reproducible host-side fields before STAR is launched."""
    template = yaml.safe_load(template_path.read_text(encoding="utf-8")) or {}
    if not isinstance(template, dict):
        raise ValueError(f"manifest template must be a mapping: {template_path}")
    data = deepcopy(template)
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
    return metadata


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
    data["star_runtime_metadata"] = metadata


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv_row_count(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))
