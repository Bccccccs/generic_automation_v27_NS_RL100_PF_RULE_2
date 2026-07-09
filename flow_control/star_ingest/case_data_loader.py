"""Load a case from a standard ``case_id/`` directory and return structured data.

Standard case directory layout::

    case_id/
        case_manifest.yaml      —  case metadata (geometry, mesh, flow params)
        actuation_schedule.csv   —  jet actuation commands per window
        timeseries.csv           —  force/moment sensor readings + jet states
        quality_report.json      —  pre-computed quality metrics
        figures/                 —  auto-generated diagnostic plots
        notes.md                 —  human-readable notes (optional)

This module extends ``flow_control.data_schema.CaseSchema`` with additional
validation logic for the STAR-export ingest path.
"""

from __future__ import annotations

import json
import csv
import logging
from pathlib import Path
from typing import Any

import yaml

from .star_export_reader import (
    read_star_export_csv,
    read_star_export_bundle,
    compute_fz_total,
    FZ_SENSOR_COLUMNS,
    GLOBAL_COLUMNS,
    JET_COLUMNS,
    CMD_MASSFLOW_COLUMNS,
    ACTUAL_MASSFLOW_COLUMNS,
)
from .quality_checker import QualityChecker

logger = logging.getLogger("case_data_loader")

# The 6 Fz sensor columns + 5 global columns that every case must have
REQUIRED_TIMESERIES_COLUMNS = (
    "physical_time",
    "Fz_S1L",
    "Fz_S1R",
    "Fz_S2L",
    "Fz_S2R",
    "Fz_S3L",
    "Fz_S3R",
    "Fz_Total",
    "Drag_Total",
    "Pitch_Moment",
    "Roll_Moment",
    "Jet_Reaction_Z",
)

JET_REQUIRED_EXTRA_COLUMNS = (
    *JET_COLUMNS,
    *CMD_MASSFLOW_COLUMNS,
    *ACTUAL_MASSFLOW_COLUMNS,
)

CASE_REQUIRED_FILES = (
    "case_manifest.yaml",
    "actuation_schedule.csv",
    "timeseries.csv",
    "quality_report.json",
)

CASE_OPTIONAL_FILES = ("notes.md",)
CASE_REQUIRED_DIRS = ("figures",)


def load_case(
    case_dir: str | Path,
    *,
    require_complete_schema: bool | None = None,
) -> dict[str, Any]:
    """Load a complete case from a standard ``case_id/`` directory.

    Parameters
    ----------
    case_dir
        Path to the case directory (named after the ``case_id``).

    Returns
    -------
    dict with keys:
        - ``case_id``: directory basename
        - ``case_dir``: resolved ``Path``
        - ``manifest``: parsed YAML dict
        - ``timeseries``: list of row dicts
        - ``actuation_schedule``: list of row dicts
        - ``quality_report``: parsed JSON dict
        - ``notes``: notes.md text, or ``""``
        - ``figures_dir``: ``Path`` to figures/
        - ``has_jet_data``: bool — whether the case has jet columns
        - ``require_complete_schema``: bool — whether full required-column
          validation was applied
        - ``errors``: list of validation errors (empty = valid)
        - ``warnings``: list of validation warnings
    """
    case_dir = Path(case_dir).resolve()
    case_id = case_dir.name

    result: dict[str, Any] = {
        "case_id": case_id,
        "case_dir": case_dir,
        "manifest": {},
        "timeseries": [],
        "actuation_schedule": [],
        "quality_report": {},
        "notes": "",
        "figures_dir": case_dir / "figures",
        "has_jet_data": False,
        "require_complete_schema": True,
        "errors": [],
        "warnings": [],
    }

    # ── 1. File completeness ──────────────────────────────────────────────
    missing_files = _check_files(case_dir)
    if missing_files:
        result["errors"].append(
            f"Missing required files: {', '.join(missing_files)}"
        )
        return result  # cannot continue without core files

    # ── 2. Load manifest ──────────────────────────────────────────────────
    with (case_dir / "case_manifest.yaml").open("r", encoding="utf-8") as f:
        manifest = yaml.safe_load(f) or {}
    result["manifest"] = manifest
    if require_complete_schema is None:
        validation_mode = str(manifest.get("validation_mode", "full_case")).lower()
        require_complete_schema = validation_mode != "partial_timeseries"
    result["require_complete_schema"] = bool(require_complete_schema)

    # ── 3. Load timeseries ────────────────────────────────────────────────
    timeseries_path = case_dir / "timeseries.csv"
    result["timeseries"] = _read_csv_rows(timeseries_path)
    if not result["timeseries"]:
        result["errors"].append("timeseries.csv is empty")
        return result

    # Detect if this is a jet case.  Manifest case_type is authoritative when
    # present; column names are only a fallback for legacy cases.
    ts_columns = list(result["timeseries"][0].keys()) if result["timeseries"] else []
    result["has_jet_data"] = _is_jet_case(manifest, ts_columns)
    jet_case = result["has_jet_data"]

    # ── 4. Quality checks ─────────────────────────────────────────────────
    checker = QualityChecker()

    # 4a. Column completeness.  Partial STAR timeseries imports are allowed to
    # omit columns that will arrive from later exports; full cases are strict.
    if require_complete_schema:
        col_errors = checker.check_required_columns(result["timeseries"], REQUIRED_TIMESERIES_COLUMNS)
        result["errors"].extend(col_errors)
    else:
        result["errors"].extend(
            checker.check_required_columns(result["timeseries"], ("physical_time",))
        )

    if jet_case and require_complete_schema:
        jet_col_errors = checker.check_required_columns(result["timeseries"], JET_REQUIRED_EXTRA_COLUMNS)
        result["errors"].extend(jet_col_errors)
    elif not jet_case and (require_complete_schema or "Jet_Reaction_Z" in ts_columns):
        # No-jet case: Jet_Reaction_Z should be 0 or N/A when present.
        jrz_check = checker.check_no_jet_jrz(result["timeseries"])
        result["warnings"].extend(jrz_check)

    # 4b. Monotonic time
    time_errs = checker.check_monotonic_time(result["timeseries"])
    result["errors"].extend(time_errs)

    # 4c. NaN detection
    nan_errs = checker.check_nan_values(result["timeseries"])
    result["errors"].extend(nan_errs)

    # 4d. Units / direction warning
    unit_warns = checker.check_units_and_direction(manifest)
    result["warnings"].extend(unit_warns)

    if jet_case:
        # 4e. Jet on/off vs massflow consistency
        jet_mf_errs = checker.check_jet_massflow_consistency(result["timeseries"])
        result["errors"].extend(jet_mf_errs)

        # 4f. cmd vs actual massflow separation
        mf_sep_errs = checker.check_massflow_separation(result["timeseries"])
        result["errors"].extend(mf_sep_errs)

        # 4g. Jet_Reaction_Z in jet case should be present
        if "Jet_Reaction_Z" not in ts_columns:
            result["errors"].append(
                "Jet case with active jets must include Jet_Reaction_Z column"
            )

    # ── 5. Load actuation schedule ────────────────────────────────────────
    result["actuation_schedule"] = _read_csv_rows(case_dir / "actuation_schedule.csv")

    # ── 6. Load quality report ────────────────────────────────────────────
    with (case_dir / "quality_report.json").open("r", encoding="utf-8") as f:
        result["quality_report"] = json.load(f)

    # ── 7. Load notes ─────────────────────────────────────────────────────
    notes_path = case_dir / "notes.md"
    if notes_path.exists():
        result["notes"] = notes_path.read_text(encoding="utf-8")

    logger.info(
        "Loaded case %s: %d timeseries rows, %d errors, %d warnings",
        case_id,
        len(result["timeseries"]),
        len(result["errors"]),
        len(result["warnings"]),
    )
    return result


# ── Public helper: ingest STAR export into a case directory ──────────────────


def ingest_star_export(
    star_files: list[str | Path],
    *,
    case_dir: str | Path,
    manifest: dict[str, Any] | None = None,
    actuation_schedule: list[dict[str, Any]] | None = None,
    notes: str | None = None,
    overwrite: bool = False,
    require_complete_schema: bool = True,
) -> dict[str, Any]:
    """Ingest raw STAR-CCM+ export file(s) into a standard case directory.

    This is the primary entry point for the STAR → standard case pipeline.

    Parameters
    ----------
    star_files
        One or more STAR-CCM+ export CSV paths (e.g. ``["FZ.csv"]``,
        or ``["FZ.csv", "jet_commands.csv"]``).
    case_dir
        Target case directory (will be created if needed).
    manifest
        Optional case manifest dict (auto-populated with defaults).
    actuation_schedule
        Optional pre-built actuation schedule rows.
    notes
        Optional notes text for ``notes.md``.
    overwrite
        If ``False`` (default), raises ``FileExistsError`` when case exists.
    require_complete_schema
        If ``True`` (default), missing required full-case columns are errors.
        Set to ``False`` for a single STAR timeseries export that will be merged
        with other exports later.

    Returns
    -------
    dict — the result of :func:`load_case` after ingestion.
    """
    case_path = Path(case_dir)
    if case_path.exists() and not overwrite:
        raise FileExistsError(
            f"Case directory already exists: {case_path}. "
            f"Set overwrite=True to replace."
        )
    case_path.mkdir(parents=True, exist_ok=True)

    # 1. Read STAR export data
    if len(star_files) == 1:
        data = read_star_export_csv(star_files[0])
    else:
        data = read_star_export_bundle(star_files)

    rows = data["rows"]

    # 2. Compute Fz_Total if all six bottom-force sensors are present.
    # Missing STAR exports stay missing so quality checks can report them.
    compute_fz_total(rows)

    # Re-compute column ordering after derived columns are added.
    present_cols = _ordered_columns_from_rows(rows) if rows else data["columns"]

    # 3. Write timeseries.csv
    _write_csv_rows(case_path / "timeseries.csv", present_cols, rows)

    # 4. Write actuation_schedule.csv
    if actuation_schedule is not None:
        sch_cols = list(actuation_schedule[0].keys()) if actuation_schedule else ["physical_time"]
        _write_csv_rows(case_path / "actuation_schedule.csv", sch_cols, actuation_schedule)
    else:
        # Write an empty schedule with just the header
        _write_csv_rows(case_path / "actuation_schedule.csv", ["physical_time"], [])

    # 5. Write case_manifest.yaml
    manifest_data = manifest or {}
    manifest_data.setdefault("geometry_version", "unknown")
    manifest_data.setdefault("mesh_version", "unknown")
    manifest_data.setdefault("flow_velocity", 0.0)
    manifest_data.setdefault("gap", 0.0)
    manifest_data.setdefault("time_step", 0.0)
    manifest_data.setdefault("jet_amplitude", 0.0)
    manifest_data.setdefault("window_duration", 0.0)
    manifest_data.setdefault("random_seed", 0)
    manifest_data.setdefault("case_type", "unknown")
    manifest_data["validation_mode"] = (
        "full_case" if require_complete_schema else "partial_timeseries"
    )
    with (case_path / "case_manifest.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(manifest_data, f, sort_keys=False, allow_unicode=True)

    # 6. Create figures directory
    (case_path / "figures").mkdir(exist_ok=True)

    # 7. Write notes.md
    if notes:
        (case_path / "notes.md").write_text(notes, encoding="utf-8")

    # 8. Seed, then write the final quality report. load_case validates the
    # standard directory layout, which includes quality_report.json.
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump({}, f)

    result = load_case(case_path, require_complete_schema=require_complete_schema)
    quality_report = _build_quality_report(result)
    quality_report["source_files"] = data["source_files"]
    quality_report["star_column_mapping"] = data["mapping"]
    quality_report["detected_units"] = data["units"]
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(quality_report, f, indent=2, ensure_ascii=False)

    # Reload with updated quality report
    result = load_case(case_path, require_complete_schema=require_complete_schema)
    return result


def _check_files(case_dir: Path) -> list[str]:
    missing: list[str] = []
    for file_name in CASE_REQUIRED_FILES:
        if not (case_dir / file_name).exists():
            missing.append(file_name)
    for dir_name in CASE_REQUIRED_DIRS:
        if not (case_dir / dir_name).is_dir():
            missing.append(f"{dir_name}/")
    return missing


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        rows: list[dict[str, Any]] = []
        for row in reader:
            parsed: dict[str, Any] = {}
            for key, val in row.items():
                if key is None:
                    continue
                stripped = val.strip().strip('"') if val else ""
                try:
                    parsed[key] = float(stripped)
                except (ValueError, TypeError):
                    parsed[key] = stripped
            rows.append(parsed)
        return rows


def _is_jet_case(manifest: dict[str, Any], ts_columns: list[str]) -> bool:
    case_type = str(manifest.get("case_type", "")).strip().lower()
    if case_type in {"jet", "jet_on", "with_jet", "active_jet"}:
        return True
    if case_type in {"no_jet", "passive", "reference"}:
        return False
    return any(
        col.startswith("JET_")
        or col.startswith("cmd_massflow_")
        or col.startswith("actual_massflow_")
        for col in ts_columns
    )


def _ordered_columns_from_rows(rows: list[dict[str, Any]]) -> list[str]:
    priority = (
        "physical_time",
        *FZ_SENSOR_COLUMNS,
        *GLOBAL_COLUMNS,
        *JET_COLUMNS,
        *CMD_MASSFLOW_COLUMNS,
        *ACTUAL_MASSFLOW_COLUMNS,
    )
    present: set[str] = set()
    first_seen: list[str] = []
    for row in rows:
        for col in row:
            if col not in present:
                present.add(col)
                first_seen.append(col)
    return [col for col in priority if col in present] + [
        col for col in first_seen if col not in priority
    ]


def _write_csv_rows(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _build_quality_report(result: dict[str, Any]) -> dict[str, Any]:
    """Build a quality report dict from the case data and check results."""
    return {
        "case_id": result["case_id"],
        "num_errors": len(result["errors"]),
        "num_warnings": len(result["warnings"]),
        "errors": result["errors"],
        "warnings": result["warnings"],
        "has_jet_data": result["has_jet_data"],
        "validation_mode": (
            "full_case" if result.get("require_complete_schema") else "partial_timeseries"
        ),
        "num_timeseries_rows": len(result["timeseries"]),
        "num_timeseries_columns": len(result["timeseries"][0]) if result["timeseries"] else 0,
        "run_success_flag": len(result["errors"]) == 0,
    }
