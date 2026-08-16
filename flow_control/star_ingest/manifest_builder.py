"""Build and finalize STAR case manifests around a CCM run."""

from __future__ import annotations

import csv
import hashlib
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


def finalize_manifest(*, preflight_path: Path, snapshot_path: Path, output_path: Path) -> Path:
    """Merge the Macro's pre-solve snapshot into the final case manifest."""
    data = yaml.safe_load(preflight_path.read_text(encoding="utf-8")) or {}
    snapshot = yaml.safe_load(snapshot_path.read_text(encoding="utf-8")) or {}
    if not isinstance(snapshot, dict) or snapshot.get("snapshot_status") != "ok":
        raise ValueError(f"invalid STAR template snapshot: {snapshot_path}")
    data["star_template_snapshot"] = snapshot
    data["surface_properties"] = snapshot.get("surface_properties", {})
    data["manifest_status"] = "finalized_from_star_template_snapshot"
    output_path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return output_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _csv_row_count(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return sum(1 for _ in csv.DictReader(handle))
