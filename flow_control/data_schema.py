"""Data structures and case IO schema for sparse-jet flow-control runs."""

from __future__ import annotations

import csv
import json
import logging
import math
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from starccm.control.control_spec import (
    GLOBAL_OUTPUT_COLUMNS,
    JET_COLUMNS,
    LOAD_COLUMNS,
)

GLOBAL_COLUMNS = GLOBAL_OUTPUT_COLUMNS
TIMESERIES_REQUIRED_COLUMNS = (
    "physical_time",
    "window_id",
    *JET_COLUMNS,
    *LOAD_COLUMNS,
    *GLOBAL_COLUMNS,
)
PRESSURE_SENSOR_REQUIRED_COLUMNS = (
    "physical_time",
    "window_id",
    "sensor_id",
    "pressure",
)
MANIFEST_REQUIRED_FIELDS = (
    "geometry_version",
    "mesh_version",
    "flow_velocity",
    "gap",
    "time_step",
    "jet_amplitude",
    "window_duration",
    "random_seed",
    "git_commit",
    "created_time",
)
CASE_DIRECTORIES = ("figures", "logs", "flow_snapshots")


def _is_nan_like(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() == "nan"
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _normalize_tabular(data: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Convert DataFrame-like, row-mapping, or mapping-of-lists data to rows."""

    if hasattr(data, "to_dict") and hasattr(data, "columns"):
        columns = [str(column) for column in data.columns]
        rows = [
            {str(key): value for key, value in row.items()}
            for row in data.to_dict(orient="records")
        ]
        return columns, rows

    if isinstance(data, dict):
        columns = [str(column) for column in data.keys()]
        lengths = {len(values) for values in data.values()}
        if len(lengths) > 1:
            raise ValueError("tabular mapping values must all have the same length")
        row_count = next(iter(lengths), 0)
        rows = [
            {column: data[column][row_idx] for column in columns}
            for row_idx in range(row_count)
        ]
        return columns, rows

    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        rows = [{str(key): value for key, value in row.items()} for row in data]
        columns: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for column in row:
                if column not in seen:
                    columns.append(column)
                    seen.add(column)
        return columns, rows

    raise TypeError("tabular data must be a DataFrame-like object, mapping, or iterable of mappings")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _current_git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CaseSchema:
    """Strict case storage contract shared by CFD, mock plant, and RL runs."""

    runs_root = Path("runs")
    timeseries_required_columns = TIMESERIES_REQUIRED_COLUMNS
    pressure_sensor_required_columns = PRESSURE_SENSOR_REQUIRED_COLUMNS
    manifest_required_fields = MANIFEST_REQUIRED_FIELDS
    case_directories = CASE_DIRECTORIES

    @classmethod
    def validate_timeseries(cls, df: Any) -> list[str]:
        """Return schema errors for timeseries data; an empty list means valid."""

        errors: list[str] = []
        columns, rows = _normalize_tabular(df)
        column_set = set(columns)

        missing_columns = [
            column for column in cls.timeseries_required_columns if column not in column_set
        ]
        if missing_columns:
            errors.append(f"timeseries.csv missing required columns: {', '.join(missing_columns)}")

        missing_jets = [column for column in JET_COLUMNS if column not in column_set]
        if missing_jets:
            errors.append(f"timeseries.csv missing jet columns: {', '.join(missing_jets)}")

        if not rows:
            errors.append("timeseries.csv must contain at least one row")
            return errors

        for row_idx, row in enumerate(rows):
            for column in columns:
                if _is_nan_like(row.get(column)):
                    errors.append(f"timeseries.csv contains missing/NaN value at row {row_idx}, column {column}")
                    break

        if "window_id" in column_set:
            try:
                window_ids = [int(row["window_id"]) for row in rows]
            except (TypeError, ValueError):
                errors.append("window_id must be integer-like for every row")
            else:
                expected = list(range(window_ids[0], window_ids[0] + len(window_ids)))
                if window_ids != expected:
                    errors.append(
                        "window_id must be consecutive with step 1 in row order "
                        f"(expected {expected[0]}..{expected[-1]}, got {window_ids[0]}..{window_ids[-1]})"
                    )

        return errors

    @classmethod
    def validate_manifest(cls, yaml_dict: dict[str, Any]) -> list[str]:
        """Return manifest schema errors; an empty list means valid."""

        errors: list[str] = []
        missing_fields = [
            field_name
            for field_name in cls.manifest_required_fields
            if field_name not in yaml_dict or _is_nan_like(yaml_dict[field_name])
        ]
        if missing_fields:
            errors.append(f"case_manifest.yaml missing required fields: {', '.join(missing_fields)}")
        return errors

    @classmethod
    def validate_pressure_sensors(cls, df: Any) -> list[str]:
        """Return schema errors for optional pressure_sensors.csv data."""

        errors: list[str] = []
        columns, rows = _normalize_tabular(df)
        column_set = set(columns)

        missing_columns = [
            column
            for column in cls.pressure_sensor_required_columns
            if column not in column_set
        ]
        if missing_columns:
            errors.append(f"pressure_sensors.csv missing required columns: {', '.join(missing_columns)}")

        if not rows:
            errors.append("pressure_sensors.csv must contain at least one row when provided")
            return errors

        for row_idx, row in enumerate(rows):
            for column in cls.pressure_sensor_required_columns:
                if _is_nan_like(row.get(column)):
                    errors.append(
                        f"pressure_sensors.csv contains missing/NaN value at row {row_idx}, column {column}"
                    )
                    break
            try:
                int(row.get("window_id"))
            except (TypeError, ValueError):
                errors.append(f"pressure_sensors.csv row {row_idx} has non-integer window_id")
            try:
                float(row.get("physical_time"))
                float(row.get("pressure"))
            except (TypeError, ValueError):
                errors.append(f"pressure_sensors.csv row {row_idx} has non-numeric time or pressure")

        return errors

    @classmethod
    def build_run_directory(cls, case_id: str) -> Path:
        """Create and return the standard run directory for a case."""

        if not str(case_id).strip():
            raise ValueError("case_id must be non-empty")
        if Path(str(case_id)).name != str(case_id):
            raise ValueError("case_id must be a plain directory name without path separators")

        run_dir = cls.runs_root / str(case_id)
        for directory_name in cls.case_directories:
            (run_dir / directory_name).mkdir(parents=True, exist_ok=True)
        return run_dir

    @classmethod
    def write_case(cls, case_data: dict[str, Any]) -> dict[str, Any]:
        """Validate and write a complete case bundle under ``runs/<case_id>/``."""

        case_id = str(case_data.get("case_id", "")).strip()
        run_dir = cls.build_run_directory(case_id)
        logger = cls._case_logger(run_dir)
        logger.info("starting case write for %s", case_id)

        manifest = dict(case_data.get("manifest") or {})
        manifest.setdefault("git_commit", _current_git_commit())
        manifest.setdefault("created_time", _utc_now_iso())

        timeseries = case_data.get("timeseries")
        if timeseries is None:
            raise ValueError("case_data must include 'timeseries'")
        ts_columns, ts_rows = _normalize_tabular(timeseries)
        ts_columns = cls._ordered_columns(ts_columns, cls.timeseries_required_columns)

        validation_errors = []
        validation_errors.extend(cls.validate_manifest(manifest))
        validation_errors.extend(cls.validate_timeseries(ts_rows))
        validation_errors.extend(cls._validate_directory(run_dir))
        if validation_errors:
            logger.error("case validation failed: %s", validation_errors)
            raise ValueError("; ".join(validation_errors))

        schedule = case_data.get("actuation_schedule")
        if schedule is None:
            schedule_columns, schedule_rows = cls._actuation_schedule_from_timeseries(ts_rows)
        else:
            schedule_columns, schedule_rows = _normalize_tabular(schedule)

        pressure_sensors = case_data.get("pressure_sensors")
        pressure_sensor_rows: list[dict[str, Any]] | None = None
        pressure_sensor_columns: list[str] | None = None
        if pressure_sensors is not None:
            pressure_sensor_columns, pressure_sensor_rows = _normalize_tabular(pressure_sensors)
            pressure_sensor_columns = cls._ordered_columns(
                pressure_sensor_columns,
                cls.pressure_sensor_required_columns,
            )
            pressure_errors = cls.validate_pressure_sensors(pressure_sensor_rows)
            if pressure_errors:
                logger.error("pressure sensor validation failed: %s", pressure_errors)
                raise ValueError("; ".join(pressure_errors))

        quality_report = cls._build_quality_report(ts_rows)
        quality_report.update(dict(case_data.get("quality_report") or {}))
        quality_report.setdefault("run_success_flag", len(validation_errors) == 0)

        with (run_dir / "case_manifest.yaml").open("w", encoding="utf-8") as handle:
            yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=True)
        _write_csv_rows(run_dir / "timeseries.csv", ts_columns, ts_rows)
        _write_csv_rows(run_dir / "actuation_schedule.csv", schedule_columns, schedule_rows)
        if pressure_sensor_rows is not None and pressure_sensor_columns is not None:
            _write_csv_rows(run_dir / "pressure_sensors.csv", pressure_sensor_columns, pressure_sensor_rows)
        with (run_dir / "quality_report.json").open("w", encoding="utf-8") as handle:
            json.dump(quality_report, handle, indent=2, ensure_ascii=False)

        logger.info("case write completed for %s", case_id)
        return {
            "case_id": case_id,
            "run_dir": run_dir,
            "files": {
                "manifest": run_dir / "case_manifest.yaml",
                "actuation_schedule": run_dir / "actuation_schedule.csv",
                "timeseries": run_dir / "timeseries.csv",
                "pressure_sensors": run_dir / "pressure_sensors.csv",
                "flow_snapshots": run_dir / "flow_snapshots",
                "quality_report": run_dir / "quality_report.json",
                "log": run_dir / "logs" / "case_io.log",
            },
            "quality_report": quality_report,
        }

    @classmethod
    def load_case(cls, case_id: str) -> dict[str, Any]:
        """Load a complete case bundle and re-run strict validation."""

        run_dir = cls.runs_root / str(case_id)
        required_paths = {
            "manifest": run_dir / "case_manifest.yaml",
            "actuation_schedule": run_dir / "actuation_schedule.csv",
            "timeseries": run_dir / "timeseries.csv",
            "quality_report": run_dir / "quality_report.json",
        }
        missing = [name for name, path in required_paths.items() if not path.exists()]
        if missing:
            raise FileNotFoundError(f"case {case_id} missing files/directories: {', '.join(missing)}")

        with required_paths["manifest"].open("r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle) or {}
        timeseries = _read_csv_rows(required_paths["timeseries"])
        actuation_schedule = _read_csv_rows(required_paths["actuation_schedule"])
        pressure_sensor_path = run_dir / "pressure_sensors.csv"
        pressure_sensors = _read_csv_rows(pressure_sensor_path) if pressure_sensor_path.exists() else []
        with required_paths["quality_report"].open("r", encoding="utf-8") as handle:
            quality_report = json.load(handle)

        validation_errors = []
        validation_errors.extend(cls.validate_manifest(manifest))
        validation_errors.extend(cls.validate_timeseries(timeseries))
        if pressure_sensors:
            validation_errors.extend(cls.validate_pressure_sensors(pressure_sensors))
        validation_errors.extend(cls._validate_directory(run_dir))
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        return {
            "case_id": str(case_id),
            "run_dir": run_dir,
            "manifest": manifest,
            "actuation_schedule": actuation_schedule,
            "timeseries": timeseries,
            "pressure_sensors": pressure_sensors,
            "flow_snapshots_dir": run_dir / "flow_snapshots",
            "quality_report": quality_report,
        }

    @classmethod
    def _ordered_columns(cls, columns: list[str], required: tuple[str, ...]) -> list[str]:
        extras = [column for column in columns if column not in required]
        return list(required) + extras

    @classmethod
    def _validate_directory(cls, run_dir: Path) -> list[str]:
        errors = []
        for directory_name in cls.case_directories:
            path = run_dir / directory_name
            if not path.exists() or not path.is_dir():
                errors.append(f"run directory missing {directory_name}/")
        return errors

    @classmethod
    def _actuation_schedule_from_timeseries(
        cls, rows: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        columns = ["physical_time", "window_id", *JET_COLUMNS]
        schedule_rows = [
            {column: row[column] for column in columns}
            for row in rows
        ]
        return columns, schedule_rows

    @classmethod
    def _build_quality_report(cls, rows: list[dict[str, Any]]) -> dict[str, Any]:
        solver_statuses = [str(row["solver_status"]).lower() for row in rows]
        success_count = sum(status in {"ok", "success", "converged", "stable", "1", "true"} for status in solver_statuses)
        violation_count = len(rows) - success_count

        jet_activation_stats = {}
        for jet in JET_COLUMNS:
            values = [float(row[jet]) for row in rows]
            active = [value for value in values if value != 0.0]
            jet_activation_stats[jet] = {
                "activation_count": len(active),
                "activation_fraction": len(active) / len(values),
                "mean_command": sum(values) / len(values),
                "max_command": max(values),
                "min_command": min(values),
            }

        numeric_columns = [
            column for column in (*LOAD_COLUMNS, "Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z")
        ]
        correlations = cls._pairwise_abs_correlations(rows, numeric_columns)
        if correlations:
            strongest_pair, max_abs_offdiag = max(correlations, key=lambda item: item[1])
            mean_abs_offdiag = sum(value for _, value in correlations) / len(correlations)
        else:
            strongest_pair = ("", "")
            mean_abs_offdiag = 0.0
            max_abs_offdiag = 0.0

        missing_count = sum(
            1
            for row in rows
            for value in row.values()
            if _is_nan_like(value)
        )
        total_cells = sum(len(row) for row in rows)

        return {
            "stability_score": success_count / len(rows),
            "constraint_violation_count": violation_count,
            "jet_activation_stats": jet_activation_stats,
            "correlation_matrix_summary": {
                "columns": numeric_columns,
                "mean_abs_offdiag": mean_abs_offdiag,
                "max_abs_offdiag": max_abs_offdiag,
                "strongest_pair": list(strongest_pair),
            },
            "data_completeness": {
                "missing_count": missing_count,
                "total_cells": total_cells,
                "complete": missing_count == 0,
            },
            "run_success_flag": violation_count == 0 and missing_count == 0,
        }

    @classmethod
    def _pairwise_abs_correlations(
        cls,
        rows: list[dict[str, Any]],
        numeric_columns: list[str],
    ) -> list[tuple[tuple[str, str], float]]:
        correlations: list[tuple[tuple[str, str], float]] = []
        if len(rows) < 2:
            return correlations

        series = {
            column: [float(row[column]) for row in rows]
            for column in numeric_columns
        }
        for left_idx, left in enumerate(numeric_columns):
            for right in numeric_columns[left_idx + 1 :]:
                correlations.append(((left, right), abs(cls._pearson(series[left], series[right]))))
        return correlations

    @staticmethod
    def _pearson(left: list[float], right: list[float]) -> float:
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        left_centered = [value - left_mean for value in left]
        right_centered = [value - right_mean for value in right]
        numerator = sum(left_value * right_value for left_value, right_value in zip(left_centered, right_centered))
        left_denom = math.sqrt(sum(value * value for value in left_centered))
        right_denom = math.sqrt(sum(value * value for value in right_centered))
        if left_denom == 0.0 or right_denom == 0.0:
            return 0.0
        return numerator / (left_denom * right_denom)

    @classmethod
    def _case_logger(cls, run_dir: Path) -> logging.Logger:
        logger = logging.getLogger(f"flow_control.case_schema.{run_dir.name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        log_path = run_dir / "logs" / "case_io.log"
        if not any(
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) == log_path
            for handler in logger.handlers
        ):
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        return logger


@dataclass(frozen=True)
class ControlAction:
    """One sparse-jet control command."""

    jet_id: str
    enabled: bool
    mass_flow_rate: float
    duty_cycle: float
    frequency_hz: float


@dataclass(frozen=True)
class ScheduleStep:
    """Control commands applied at one solver/control iteration."""

    step_id: int
    iteration: int
    duration_iterations: int
    actions: tuple[ControlAction, ...]


@dataclass(frozen=True)
class Schedule:
    """A complete control schedule for one experiment."""

    name: str
    steps: tuple[ScheduleStep, ...]


@dataclass(frozen=True)
class PlantObservation:
    """Minimal observation emitted by the mock plant or future real adapter."""

    iteration: int
    drag: float
    pressure_loss: float
    stable: bool
    notes: str = ""


@dataclass(frozen=True)
class ExperimentConfig:
    """Top-level configuration for the first maglev sparse-jet workflow."""

    project_name: str
    case_name: str
    max_iterations: int
    control_interval_iterations: int
    jet_ids: tuple[str, ...]
    default_mass_flow_rate: float
    default_duty_cycle: float
    default_frequency_hz: float
    output_dir: Path = field(default=Path("runs"))

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ExperimentConfig":
        experiment = data.get("experiment", {})
        control = data.get("control", {})
        jets = control.get("jets", [])
        defaults = control.get("defaults", {})
        output = data.get("output", {})

        return cls(
            project_name=str(experiment.get("project_name", "maglev_sparse_jet_9w")),
            case_name=str(experiment.get("case_name", "baseline")),
            max_iterations=int(experiment.get("max_iterations", 1000)),
            control_interval_iterations=int(control.get("interval_iterations", 50)),
            jet_ids=tuple(str(jet["id"]) for jet in jets),
            default_mass_flow_rate=float(defaults.get("mass_flow_rate", 0.0)),
            default_duty_cycle=float(defaults.get("duty_cycle", 0.0)),
            default_frequency_hz=float(defaults.get("frequency_hz", 0.0)),
            output_dir=Path(output.get("run_dir", "runs")),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return cls.from_mapping(data)
