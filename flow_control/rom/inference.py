"""Use a trained ARX ROM to produce a standard prediction case."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from flow_control.data_schema import CaseSchema
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.star_ingest.case_data_loader import write_quality_report
from starccm.control.control_spec import GLOBAL_OUTPUT_COLUMNS, JET_COLUMNS

from .arx_model import ARXModel
from .identifier import (
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    load_case_table,
    matrix_from_rows,
    read_csv_rows,
)

ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))


@dataclass(frozen=True)
class ARXUseResult:
    """Artifacts produced by using an existing ARX model."""

    out_dir: Path
    prediction_case_dir: Path
    prediction_timeseries_path: Path
    quality_report_path: Path
    source_rows: int
    predicted_rows: int
    warmup_rows: int
    run_success_flag: bool


def use_arx_rom_on_case(
    *,
    model_path: str | Path,
    case_dir: str | Path,
    out_dir: str | Path,
) -> ARXUseResult:
    """Run a trained ARX model on an explicit case and write a prediction case.

    The source case supplies the input sequence and the initial output history.
    Rows before ``model.max_lag`` are copied as warmup history. Rows at and
    after ``model.max_lag`` are recursive ARX predictions.
    """

    model = ARXModel.from_dict(_read_json(model_path))
    rows = load_case_table(case_dir)
    if len(rows) <= model.max_lag:
        raise ValueError(f"case is too short for max_lag={model.max_lag}: {case_dir}")

    inputs = matrix_from_rows(rows, ROM_INPUT_COLUMNS)
    observed_outputs = matrix_from_rows(rows, ROM_OUTPUT_COLUMNS)
    prediction = model.predict_recursive(
        inputs,
        observed_outputs,
        start_index=model.max_lag,
    )
    prediction_rows = _prediction_case_rows(
        source_rows=rows,
        observed_outputs=observed_outputs,
        prediction=prediction,
        warmup_rows=model.max_lag,
    )
    schedule_rows = _read_schedule_rows(case_dir)

    output_path = Path(out_dir)
    old_root = CaseSchema.runs_root
    CaseSchema.runs_root = output_path.parent
    try:
        schema_result = CaseSchema.write_case(
            {
                "case_id": output_path.name,
                "manifest": {
                    "geometry_version": "arx-rom",
                    "mesh_version": "not-applicable",
                    "flow_velocity": 0.0,
                    "gap": 0.0,
                    "time_step": _infer_time_step(rows),
                    "jet_amplitude": _max_total_massflow(schedule_rows),
                    "window_duration": _infer_time_step(rows),
                    "random_seed": 0,
                    "case_stage": "arx_model_use",
                    "check_mode": "arx_use",
                    "source_case_dir": str(case_dir),
                    "source_model": str(model_path),
                    "validation_mode": "full_case",
                },
                "timeseries": prediction_rows,
                "actuation_schedule": schedule_rows,
                "quality_report": {
                    "case_stage": "arx_model_use",
                    "source_case_dir": str(case_dir),
                    "source_model": str(model_path),
                    "warmup_rows": model.max_lag,
                    "predicted_rows": len(prediction_rows) - model.max_lag,
                },
            }
        )
    finally:
        CaseSchema.runs_root = old_root

    quality_report = write_quality_report(schema_result["run_dir"], check_mode="arx_use")
    return ARXUseResult(
        out_dir=Path(schema_result["run_dir"]),
        prediction_case_dir=Path(schema_result["run_dir"]),
        prediction_timeseries_path=Path(schema_result["files"]["timeseries"]),
        quality_report_path=Path(schema_result["files"]["quality_report"]),
        source_rows=len(rows),
        predicted_rows=len(prediction_rows) - model.max_lag,
        warmup_rows=model.max_lag,
        run_success_flag=bool(quality_report.get("run_success_flag", False)),
    )


def _prediction_case_rows(
    *,
    source_rows: list[dict[str, str]],
    observed_outputs: np.ndarray,
    prediction: np.ndarray,
    warmup_rows: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_idx, source in enumerate(source_rows):
        record: dict[str, Any] = {
            "physical_time": float(source.get("physical_time", row_idx)),
            "window_id": int(float(source.get("window_id", row_idx))),
        }
        for column in JET_COLUMNS:
            record[column] = float(source.get(column, 0.0) or 0.0)
        for column in MASSFLOW_COLUMNS:
            record[column] = float(source.get(column, 0.0) or 0.0)
        for jet_column, cmd_column, actual_column in zip(
            JET_COLUMNS,
            MASSFLOW_COLUMNS,
            ACTUAL_MASSFLOW_COLUMNS,
        ):
            actual_default = float(record[jet_column]) * float(record[cmd_column])
            record[actual_column] = float(source.get(actual_column, actual_default) or 0.0)

        output_values = (
            observed_outputs[row_idx]
            if row_idx < warmup_rows
            else prediction[row_idx - warmup_rows]
        )
        for out_idx, column in enumerate(ROM_OUTPUT_COLUMNS):
            record[column] = float(output_values[out_idx])
        for column in GLOBAL_OUTPUT_COLUMNS:
            if column not in record:
                record[column] = source.get(column, "success" if column == "solver_status" else 0.0)
        record["solver_status"] = "success"
        record["case_stage"] = "arx_warmup" if row_idx < warmup_rows else "arx_prediction"
        rows.append(record)
    return rows


def _read_schedule_rows(case_dir: str | Path) -> list[dict[str, str]]:
    path = Path(case_dir) / "actuation_schedule.csv"
    if path.exists():
        return read_csv_rows(path)
    return [
        {
            "physical_time": row.get("physical_time", ""),
            "window_id": row.get("window_id", idx),
            **{column: row.get(column, 0.0) for column in JET_COLUMNS},
            **{column: row.get(column, 0.0) for column in MASSFLOW_COLUMNS},
        }
        for idx, row in enumerate(load_case_table(case_dir))
    ]


def _infer_time_step(rows: list[dict[str, str]]) -> float:
    if len(rows) < 2:
        return 0.0
    return float(rows[1].get("physical_time", 0.0)) - float(rows[0].get("physical_time", 0.0))


def _max_total_massflow(rows: list[dict[str, str]]) -> float:
    max_value = 0.0
    for row in rows:
        total = sum(float(row.get(column, 0.0) or 0.0) for column in MASSFLOW_COLUMNS)
        max_value = max(max_value, total)
    return max_value


def _read_json(path: str | Path) -> dict[str, Any]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))
