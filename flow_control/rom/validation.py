"""Validation workflow for already trained ARX ROM snapshots."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .arx_model import ARXModel
from .identifier import (
    REGIONAL_OUTPUT_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    compute_metrics,
    load_case_table,
    matrix_from_rows,
    time_values_from_rows,
    write_error_svg,
    write_json,
    write_prediction_svg,
    write_rmse_bar_svg,
)


@dataclass(frozen=True)
class ARXValidationResult:
    """Paths and metrics produced by validating an existing ARX ROM."""

    out_dir: Path
    metrics_path: Path
    prediction_csv_path: Path
    prediction_plot_path: Path
    error_plot_path: Path
    rmse_plot_path: Path
    case_count: int
    validation_rows: int
    metrics: dict[str, dict[str, float]]


def validate_arx_rom(
    *,
    model_path: str | Path,
    out_dir: str | Path,
    dataset_dir: str | Path | None = None,
    case_dir: str | Path | None = None,
    case_start: int = 0,
    case_count: int | None = None,
) -> ARXValidationResult:
    """Validate a trained ARX ROM on one case or a case dataset."""

    if (dataset_dir is None) == (case_dir is None):
        raise ValueError("provide exactly one of dataset_dir or case_dir")

    model = _load_model(model_path)
    sequences = (
        [_load_rom_sequence(case_dir)]
        if case_dir is not None
        else _load_dataset_sequences(Path(dataset_dir), case_start=case_start, case_count=case_count)
    )
    if not sequences:
        raise ValueError("no validation cases selected")

    prediction_parts: list[np.ndarray] = []
    truth_parts: list[np.ndarray] = []
    prediction_rows: list[dict[str, Any]] = []
    for sequence in sequences:
        inputs = sequence["inputs"]
        outputs = sequence["outputs"]
        if len(inputs) <= model.max_lag:
            raise ValueError(f"case {sequence['case_id']} is too short for max_lag={model.max_lag}")
        prediction = model.predict_recursive(inputs, outputs, start_index=model.max_lag)
        truth = outputs[model.max_lag :]
        prediction_parts.append(prediction)
        truth_parts.append(truth)
        _extend_prediction_rows(
            prediction_rows,
            case_id=sequence["case_id"],
            time_values=sequence["time_values"][model.max_lag :],
            truth=truth,
            prediction=prediction,
        )

    prediction_all = np.vstack(prediction_parts)
    truth_all = np.vstack(truth_parts)
    metrics = compute_metrics(truth_all, prediction_all, ROM_OUTPUT_COLUMNS)

    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    metrics_path = output_path / "metrics.json"
    prediction_csv_path = output_path / "prediction_timeseries.csv"
    prediction_plot_path = output_path / "prediction_6_load_cells.svg"
    error_plot_path = output_path / "error_6_load_cells.svg"
    rmse_plot_path = output_path / "rmse_bar.svg"

    write_json(
        metrics_path,
        {
            "phase": "validation",
            "training_performed": False,
            "model_path": str(model_path),
            "dataset_dir": str(dataset_dir) if dataset_dir is not None else None,
            "case_dir": str(case_dir) if case_dir is not None else None,
            "case_count": len(sequences),
            "validation_rows": int(len(truth_all)),
            "warmup_rows_per_case": model.max_lag,
            "case_ids": [sequence["case_id"] for sequence in sequences],
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
            "metrics": metrics,
            "validation_policy": (
                "all explicitly selected validation cases are evaluated; the first max_lag rows of each "
                "case initialize ARX history, then recursive prediction uses no measured outputs and no fitting"
            ),
            "error_interpretation": {
                "delay": "Shifted peaks can indicate insufficient input/output lags or an unmodeled transport delay.",
                "noise": "Irregular high-frequency residuals are expected when the source data contains output noise.",
                "model_order": "Too few lags underfit slow dynamics; too many lags can be fragile for limited data.",
                "input_correlation": "Jets activated together can make individual input coefficients correlated.",
                "data_amount": "Short or weakly excited datasets limit identification quality.",
            },
        },
    )
    _write_prediction_csv(prediction_csv_path, prediction_rows)

    regional_count = len(REGIONAL_OUTPUT_COLUMNS)
    validation_axis = np.arange(len(truth_all), dtype=float)
    write_prediction_svg(
        prediction_plot_path,
        validation_axis,
        truth_all[:, :regional_count],
        prediction_all[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_error_svg(
        error_plot_path,
        validation_axis,
        truth_all[:, :regional_count],
        prediction_all[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_rmse_bar_svg(rmse_plot_path, metrics, REGIONAL_OUTPUT_COLUMNS)

    return ARXValidationResult(
        out_dir=output_path,
        metrics_path=metrics_path,
        prediction_csv_path=prediction_csv_path,
        prediction_plot_path=prediction_plot_path,
        error_plot_path=error_plot_path,
        rmse_plot_path=rmse_plot_path,
        case_count=len(sequences),
        validation_rows=int(len(truth_all)),
        metrics=metrics,
    )


def _load_model(path: str | Path) -> ARXModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ARXModel.from_dict(payload)


def _load_dataset_sequences(
    dataset_dir: Path,
    *,
    case_start: int,
    case_count: int | None,
) -> list[dict[str, Any]]:
    records = _read_dataset_index(dataset_dir)
    selected = records[case_start:] if case_count is None else records[case_start : case_start + case_count]
    return [_load_rom_sequence(record["case_dir"]) for record in selected]


def _read_dataset_index(dataset_dir: Path) -> list[dict[str, Any]]:
    index_path = dataset_dir / "index.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"dataset index not found: {index_path}")
    with index_path.open("r", encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    for record in records:
        record["case_index"] = int(record.get("case_index", 0))
        record["case_dir"] = record.get("case_dir") or str(dataset_dir / record["case_id"])
    return sorted(records, key=lambda record: record["case_index"])


def _load_rom_sequence(case_dir: str | Path) -> dict[str, Any]:
    rows = load_case_table(case_dir)
    return {
        "case_id": Path(case_dir).name,
        "time_values": time_values_from_rows(rows),
        "inputs": matrix_from_rows(rows, ROM_INPUT_COLUMNS),
        "outputs": matrix_from_rows(rows, ROM_OUTPUT_COLUMNS),
    }


def _extend_prediction_rows(
    rows: list[dict[str, Any]],
    *,
    case_id: str,
    time_values: np.ndarray,
    truth: np.ndarray,
    prediction: np.ndarray,
) -> None:
    for row_idx, time_value in enumerate(time_values):
        record: dict[str, Any] = {
            "case_id": case_id,
            "physical_time": float(time_value),
        }
        for col_idx, column in enumerate(ROM_OUTPUT_COLUMNS):
            record[f"{column}_true"] = float(truth[row_idx, col_idx])
            record[f"{column}_pred"] = float(prediction[row_idx, col_idx])
            record[f"{column}_error"] = float(prediction[row_idx, col_idx] - truth[row_idx, col_idx])
        rows.append(record)


def _write_prediction_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["case_id", "physical_time"]
    for column in ROM_OUTPUT_COLUMNS:
        fieldnames.extend([f"{column}_true", f"{column}_pred", f"{column}_error"])
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
