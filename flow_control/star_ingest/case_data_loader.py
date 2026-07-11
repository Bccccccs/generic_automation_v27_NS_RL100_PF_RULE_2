"""Load a case from a standard ``case_id/`` directory and return structured data.

Standard case directory layout::

    case_id/
        case_manifest.yaml      —  case metadata (geometry, mesh, flow params)
        actuation_schedule.csv   —  jet actuation commands per window
        timeseries.csv           —  force/moment sensor readings + jet states
        quality_report.json      —  pre-computed quality metrics
        input/                   —  actuation inputs used by the backend
        figures/                 —  auto-generated diagnostic plots
        logs/                    —  solver/runtime logs, if available
        flow_snapshots/           —  flow-field snapshots, if available
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
    discover_star_export_csvs,
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
    "window_id",
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
    "solver_status",
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
CASE_REQUIRED_DIRS = ("input", "figures", "logs", "flow_snapshots")


def load_case(
    case_dir: str | Path,
    *,
    require_complete_schema: bool | None = None,
    check_mode: str | None = None,
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
        "check_mode": check_mode or "star_ingest",
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
    if check_mode is None:
        check_mode = str(manifest.get("check_mode", manifest.get("case_stage", "star_ingest")))
    result["check_mode"] = check_mode
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
        mf_sep_errs = checker.check_massflow_separation(
            result["timeseries"],
            allow_identical_actual=check_mode in {"mock", "arx_use", "ccm"},
        )
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
    check_mode: str = "star_ingest",
    write_final_quality_report: bool = True,
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
    _add_common_timeseries_fields(
        rows,
        case_type=str((manifest or {}).get("case_type", "unknown")),
    )

    # Re-compute column ordering after derived columns are added.
    present_cols = _ordered_columns_from_rows(rows) if rows else data["columns"]

    # 3. Write timeseries.csv
    _write_csv_rows(case_path / "timeseries.csv", present_cols, rows)

    # 4. Write actuation_schedule.csv.  The root copy is the standard case
    # schema; input/ keeps the backend command source for traceability.
    if actuation_schedule is not None:
        sch_cols = list(actuation_schedule[0].keys()) if actuation_schedule else ["physical_time"]
        _write_csv_rows(case_path / "actuation_schedule.csv", sch_cols, actuation_schedule)
        _write_csv_rows(case_path / "input" / "actuation_schedule.csv", sch_cols, actuation_schedule)
    else:
        # Write an empty schedule with just the header
        _write_csv_rows(case_path / "actuation_schedule.csv", ["physical_time"], [])
        _write_csv_rows(case_path / "input" / "actuation_schedule.csv", ["physical_time"], [])

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
    manifest_data["check_mode"] = check_mode
    manifest_data["validation_mode"] = (
        "full_case" if require_complete_schema else "partial_timeseries"
    )
    with (case_path / "case_manifest.yaml").open("w", encoding="utf-8") as f:
        yaml.safe_dump(manifest_data, f, sort_keys=False, allow_unicode=True)

    # 6. Create standard case directories
    for directory_name in CASE_REQUIRED_DIRS:
        (case_path / directory_name).mkdir(exist_ok=True)

    # 7. Write notes.md
    if notes:
        (case_path / "notes.md").write_text(notes, encoding="utf-8")

    report_seed = {
        "status": "generated_timeseries_only",
        "check_mode": check_mode,
        "source_files": data["source_files"],
        "star_column_mapping": data["mapping"],
        "detected_units": data["units"],
        "num_timeseries_rows": len(rows),
        "num_timeseries_columns": len(present_cols),
    }
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(report_seed, f, indent=2, ensure_ascii=False)

    if not write_final_quality_report:
        return {
            "case_id": case_path.name,
            "case_dir": case_path,
            "timeseries": rows,
            "quality_report": report_seed,
            "errors": [],
            "warnings": [],
        }

    result = load_case(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
    )
    quality_report = _build_quality_report(result)
    quality_report["source_files"] = data["source_files"]
    quality_report["star_column_mapping"] = data["mapping"]
    quality_report["detected_units"] = data["units"]
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(quality_report, f, indent=2, ensure_ascii=False)

    # Reload with updated quality report
    result = load_case(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
    )
    return result


def write_quality_report(
    case_dir: str | Path,
    *,
    require_complete_schema: bool | None = None,
    check_mode: str | None = None,
) -> dict[str, Any]:
    """Validate an existing standard case directory and write quality_report.json.

    This is the standalone "check" step for the three-stage workflow:

    1. generate ``timeseries.csv`` and package files;
    2. validate the case and write ``quality_report.json``;
    3. generate diagnostic figures.
    """
    case_path = Path(case_dir)
    existing: dict[str, Any] = {}
    report_path = case_path / "quality_report.json"
    if report_path.exists():
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}

    result = load_case(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
    )
    quality_report = _build_quality_report(result)
    for key in ("source_files", "star_column_mapping", "detected_units", "figures"):
        if key in existing:
            quality_report[key] = existing[key]
    report_path.write_text(
        json.dumps(quality_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return quality_report


def ingest_star_product_dir(
    product_dir: str | Path,
    *,
    case_dir: str | Path,
    case_type: str = "unknown",
    manifest: dict[str, Any] | None = None,
    overwrite: bool = False,
    require_complete_schema: bool = True,
    generate_no_jet_schedule: bool = True,
    check_mode: str = "star_ingest",
    write_final_quality_report: bool = True,
) -> dict[str, Any]:
    """Ingest a STAR-CCM+ result folder into a standard case directory.

    The current product folder convention is a set of monitor CSV exports such
    as ``FZ_image_30000.csv``, ``Drag_Monitor_...csv``,
    ``Pitch_Moment_Monitor_...csv`` and similar files.  The folder does not
    contain ``timeseries.csv``; this function discovers recognized monitor CSVs,
    merges them on ``physical_time``, and writes the standard case package.
    """
    product_path = Path(product_dir)
    star_files = discover_star_export_csvs(product_path)
    if not star_files:
        raise ValueError(f"no recognized STAR monitor CSVs found in {product_path}")

    manifest_data = dict(manifest or {})
    manifest_data.setdefault("case_type", case_type)
    manifest_data.setdefault("source_product_dir", str(product_path.resolve()))
    manifest_data.setdefault("units", {"force": "N", "moment": "N-m", "massflow": "kg/s"})
    manifest_data.setdefault(
        "sign_convention",
        (
            "positive Fz = STAR monitor convention; "
            "positive Drag = STAR drag monitor convention; "
            "positive Pitch/Roll = STAR moment monitor convention"
        ),
    )

    actuation_schedule = None
    if generate_no_jet_schedule and str(case_type).lower() in {"no_jet", "passive", "reference"}:
        data = read_star_export_bundle(star_files)
        actuation_schedule = _build_no_jet_actuation_schedule(data["rows"])

    notes = (
        "## STAR Product Directory Ingestion\n\n"
        f"- Source product directory: `{product_path.resolve()}`\n"
        "- Ingested monitor CSV files:\n"
        + "\n".join(f"  - `{path.name}`" for path in star_files)
        + "\n\n"
        "The standard `timeseries.csv` was generated by merging these STAR "
        "monitor exports on `physical_time`.\n"
    )

    return ingest_star_export(
        star_files,
        case_dir=case_dir,
        manifest=manifest_data,
        actuation_schedule=actuation_schedule,
        notes=notes,
        overwrite=overwrite,
        require_complete_schema=require_complete_schema,
        check_mode=check_mode,
        write_final_quality_report=write_final_quality_report,
    )


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


def _build_no_jet_actuation_schedule(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    schedule: list[dict[str, Any]] = []
    times = [row.get("physical_time") for row in rows]
    numeric_times = [float(t) for t in times if isinstance(t, (int, float))]
    default_dt = (
        numeric_times[1] - numeric_times[0]
        if len(numeric_times) >= 2
        else 0.0
    )
    for idx, row in enumerate(rows):
        t = row.get("physical_time")
        next_t = rows[idx + 1].get("physical_time") if idx + 1 < len(rows) else None
        t_end = next_t if next_t is not None else (
            float(t) + default_dt if isinstance(t, (int, float)) else t
        )
        record: dict[str, Any] = {
            "physical_time": t,
            "window_id": idx,
            "t_start": t,
            "t_end": t_end,
        }
        for column in JET_COLUMNS:
            record[column] = 0
        for column in CMD_MASSFLOW_COLUMNS:
            record[column] = 0.0
        schedule.append(record)
    return schedule


def _add_common_timeseries_fields(rows: list[dict[str, Any]], *, case_type: str) -> None:
    for idx, row in enumerate(rows):
        row.setdefault("window_id", idx)
        row.setdefault("solver_status", "success")
        row.setdefault("case_stage", "starccm_ingest")
        if str(case_type).lower() in {"no_jet", "passive", "reference"}:
            for column in JET_COLUMNS:
                row.setdefault(column, 0)


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
        "window_id",
        *FZ_SENSOR_COLUMNS,
        *GLOBAL_COLUMNS,
        "solver_status",
        *JET_COLUMNS,
        *CMD_MASSFLOW_COLUMNS,
        *ACTUAL_MASSFLOW_COLUMNS,
        "case_stage",
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
        "check_mode": result.get("check_mode", "star_ingest"),
        "validation_mode": (
            "full_case" if result.get("require_complete_schema") else "partial_timeseries"
        ),
        "num_timeseries_rows": len(result["timeseries"]),
        "num_timeseries_columns": len(result["timeseries"][0]) if result["timeseries"] else 0,
        "run_success_flag": len(result["errors"]) == 0,
    }
