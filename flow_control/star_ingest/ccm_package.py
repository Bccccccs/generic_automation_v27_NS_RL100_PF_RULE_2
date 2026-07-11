"""Package CCM runtime output into a standard case and run star_ingest checks."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import yaml

from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS

from .case_data_loader import CASE_REQUIRED_DIRS, write_quality_report

ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))
REPORT_TO_STANDARD = {
    "total": "Fz_Total",
    "drag": "Drag_Total",
    "fc_load_S1L": "Fz_S1L",
    "fc_load_S1R": "Fz_S1R",
    "fc_load_S2L": "Fz_S2L",
    "fc_load_S2R": "Fz_S2R",
    "fc_load_S3L": "Fz_S3L",
    "fc_load_S3R": "Fz_S3R",
}


def package_ccm_run_case(
    *,
    ccm_timeseries_path: str | Path,
    schedule_path: str | Path,
    case_dir: str | Path,
    manifest: dict[str, Any] | None = None,
    require_complete_schema: bool = True,
) -> dict[str, Any]:
    """Write standard case files from CCM's raw runtime CSV and check them."""

    raw_rows = _read_csv_rows(ccm_timeseries_path)
    schedule_rows = _read_csv_rows(schedule_path)
    rows = _standard_timeseries_rows(raw_rows, schedule_rows)
    case_path = Path(case_dir)
    case_path.mkdir(parents=True, exist_ok=True)
    for directory_name in CASE_REQUIRED_DIRS:
        (case_path / directory_name).mkdir(exist_ok=True)

    _write_csv(case_path / "timeseries.csv", _ordered_timeseries_columns(rows), rows)
    schedule_columns = list(schedule_rows[0]) if schedule_rows else ["physical_time"]
    _write_csv(case_path / "actuation_schedule.csv", schedule_columns, schedule_rows)
    _write_csv(case_path / "input" / "actuation_schedule.csv", schedule_columns, schedule_rows)

    manifest_data = dict(manifest or {})
    manifest_data.setdefault("geometry_version", "starccm-runtime")
    manifest_data.setdefault("mesh_version", "unknown")
    manifest_data.setdefault("flow_velocity", 0.0)
    manifest_data.setdefault("gap", 0.0)
    manifest_data.setdefault("time_step", _infer_time_step(rows))
    manifest_data.setdefault("jet_amplitude", _max_total_massflow(schedule_rows))
    manifest_data.setdefault("window_duration", _infer_time_step(rows))
    manifest_data.setdefault("random_seed", 0)
    manifest_data.setdefault("case_type", "jet_on")
    manifest_data.setdefault("case_stage", "starccm_runtime")
    manifest_data["check_mode"] = "ccm"
    manifest_data["validation_mode"] = "full_case" if require_complete_schema else "partial_timeseries"
    manifest_data["source_ccm_timeseries"] = str(ccm_timeseries_path)
    manifest_data["source_schedule"] = str(schedule_path)
    manifest_data.setdefault("units", {"force": "N", "moment": "N-m", "massflow": "kg/s"})
    manifest_data.setdefault(
        "sign_convention",
        "positive values follow the STAR-CCM+ report convention exported by the runtime macro",
    )
    (case_path / "case_manifest.yaml").write_text(
        yaml.safe_dump(manifest_data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (case_path / "quality_report.json").write_text("{}", encoding="utf-8")
    (case_path / "notes.md").write_text(
        "# STAR-CCM+ runtime case\n\n"
        "Generated from flow_control_timeseries.csv and actuation_schedule.csv, then checked by star_ingest.\n",
        encoding="utf-8",
    )
    quality_report = write_quality_report(
        case_path,
        require_complete_schema=require_complete_schema,
        check_mode="ccm",
    )
    return {
        "case_dir": case_path,
        "timeseries_path": case_path / "timeseries.csv",
        "quality_report_path": case_path / "quality_report.json",
        "quality_report": quality_report,
    }


def _standard_timeseries_rows(
    raw_rows: list[dict[str, str]],
    schedule_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    schedule_by_window = {
        int(float(row.get("window_id", idx))): row
        for idx, row in enumerate(schedule_rows)
    }
    rows: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_rows):
        window_id = int(float(raw.get("window_id", idx)))
        schedule = schedule_by_window.get(window_id, {})
        record: dict[str, Any] = {
            "physical_time": float(raw.get("physical_time", schedule.get("physical_time", idx))),
            "window_id": window_id,
        }
        for jet_column in JET_COLUMNS:
            record[jet_column] = float(schedule.get(jet_column, 0.0) or 0.0)
        for column in MASSFLOW_COLUMNS:
            record[column] = float(schedule.get(column, 0.0) or 0.0)
        for jet_column, cmd_column, actual_column in zip(
            JET_COLUMNS,
            MASSFLOW_COLUMNS,
            ACTUAL_MASSFLOW_COLUMNS,
        ):
            record[actual_column] = float(record[jet_column]) * float(record[cmd_column])

        for raw_column, raw_value in raw.items():
            standard = _standard_report_column(raw_column)
            if standard is not None and raw_value not in {None, ""}:
                record[standard] = float(raw_value)
        if all(column in record for column in LOAD_COLUMNS) and "Fz_Total" not in record:
            record["Fz_Total"] = sum(float(record[column]) for column in LOAD_COLUMNS)
        record.setdefault("solver_status", "success")
        record["case_stage"] = "starccm_runtime"
        rows.append(record)
    return rows


def _standard_report_column(column: str) -> str | None:
    if column in {"physical_time", "window_id"}:
        return None
    if column in LOAD_COLUMNS or column in {"Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z"}:
        return column
    if column in REPORT_TO_STANDARD:
        return REPORT_TO_STANDARD[column]
    if column.endswith(" Monitor"):
        return REPORT_TO_STANDARD.get(column[: -len(" Monitor")])
    return None


def _ordered_timeseries_columns(rows: list[dict[str, Any]]) -> list[str]:
    priority = (
        "physical_time",
        "window_id",
        *JET_COLUMNS,
        *MASSFLOW_COLUMNS,
        *ACTUAL_MASSFLOW_COLUMNS,
        *LOAD_COLUMNS,
        "Fz_Total",
        "Drag_Total",
        "Pitch_Moment",
        "Roll_Moment",
        "Jet_Reaction_Z",
        "solver_status",
        "case_stage",
    )
    present = set().union(*(row.keys() for row in rows)) if rows else set()
    return [column for column in priority if column in present] + sorted(present - set(priority))


def _read_csv_rows(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _infer_time_step(rows: list[dict[str, Any]]) -> float:
    if len(rows) < 2:
        return 0.0
    return float(rows[1]["physical_time"]) - float(rows[0]["physical_time"])


def _max_total_massflow(rows: list[dict[str, str]]) -> float:
    max_value = 0.0
    for row in rows:
        total = sum(float(row.get(column, 0.0) or 0.0) for column in MASSFLOW_COLUMNS)
        max_value = max(max_value, total)
    return max_value
