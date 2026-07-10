"""High-level ARX ROM training workflow used by examples and integrations."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .arx_model import ARXModel
from .identifier import (
    MASSFLOW_COLUMNS,
    REGIONAL_OUTPUT_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    chronological_split_index,
    compute_metrics,
    load_case_table,
    matrix_from_rows,
    time_values_from_rows,
    write_error_svg,
    write_json,
    write_prediction_csv,
    write_prediction_svg,
    write_rmse_bar_svg,
    write_single_jet_response_summary,
)


@dataclass(frozen=True)
class ARXTrainingResult:
    """Paths and summary data produced by an ARX ROM training run."""

    out_dir: Path
    model_path: Path
    metrics_path: Path
    prediction_csv_path: Path
    prediction_plot_path: Path
    error_plot_path: Path
    rmse_plot_path: Path
    single_jet_summary_path: Path | None
    split_index: int
    train_rows: int
    validation_rows: int
    metrics: dict[str, dict[str, float]]


@dataclass(frozen=True)
class ARXDatasetTrainingResult:
    """Paths and summary data produced by a multi-case ARX training run."""

    out_dir: Path
    model_path: Path
    metrics_path: Path
    prediction_csv_path: Path | None
    prediction_plot_path: Path | None
    error_plot_path: Path | None
    rmse_plot_path: Path | None
    train_cases: int
    validation_cases: int
    train_rows: int
    validation_rows: int
    metrics: dict[str, dict[str, float]]


def train_arx_rom_from_case(
    *,
    case_dir: str | Path,
    out_dir: str | Path,
    train_fraction: float = 0.70,
    input_lags: int = 2,
    output_lags: int = 3,
    ridge_alpha: float = 1.0,
    single_jet_case_dir: str | Path | None = None,
    single_jet_summary_path: str | Path | None = None,
) -> ARXTrainingResult:
    """Train, recursively validate, and persist a B06-style ARX ROM.

    ``case_dir`` must contain ``timeseries.csv`` and an ``actuation_schedule.csv``
    with the mass-flow command columns used by the ROM inputs.
    """

    rows = load_case_table(case_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    missing_massflow = [column for column in MASSFLOW_COLUMNS if column not in rows[0]]
    if missing_massflow:
        raise ValueError(
            "mass-flow command columns are required for B06 ARX input. "
            "Use a case directory with actuation_schedule.csv. Missing: "
            + ", ".join(missing_massflow[:4])
            + ("..." if len(missing_massflow) > 4 else "")
        )

    time_values = time_values_from_rows(rows)
    inputs = matrix_from_rows(rows, ROM_INPUT_COLUMNS)
    outputs = matrix_from_rows(rows, ROM_OUTPUT_COLUMNS)

    model = ARXModel(
        input_lags=input_lags,
        output_lags=output_lags,
        include_current_input=True,
        ridge_alpha=ridge_alpha,
    )
    split = chronological_split_index(
        len(rows),
        train_fraction,
        min_train_rows=max(model.max_lag + 8, 12),
    )
    model.fit(
        inputs,
        outputs,
        end_index=split,
        input_names=ROM_INPUT_COLUMNS,
        output_names=ROM_OUTPUT_COLUMNS,
    )

    prediction = model.predict_recursive(inputs, outputs, start_index=split)
    truth = outputs[split:]
    validation_time = time_values[split:]
    metrics = compute_metrics(truth, prediction, ROM_OUTPUT_COLUMNS)

    metrics_payload = _metrics_payload(
        case_dir=case_dir,
        row_count=len(rows),
        split=split,
        input_lags=input_lags,
        output_lags=output_lags,
        ridge_alpha=ridge_alpha,
        metrics=metrics,
    )

    metrics_path = output_path / "metrics.json"
    model_path = output_path / "arx_model.json"
    prediction_csv_path = output_path / "prediction_timeseries.csv"
    prediction_plot_path = output_path / "prediction_6_load_cells.svg"
    error_plot_path = output_path / "error_6_load_cells.svg"
    rmse_plot_path = output_path / "rmse_bar.svg"

    write_json(metrics_path, metrics_payload)
    write_json(model_path, model.to_dict())
    write_prediction_csv(prediction_csv_path, validation_time, truth, prediction, ROM_OUTPUT_COLUMNS)

    regional_count = len(REGIONAL_OUTPUT_COLUMNS)
    write_prediction_svg(
        prediction_plot_path,
        validation_time,
        truth[:, :regional_count],
        prediction[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_error_svg(
        error_plot_path,
        validation_time,
        truth[:, :regional_count],
        prediction[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_rmse_bar_svg(rmse_plot_path, metrics, REGIONAL_OUTPUT_COLUMNS)

    summary_path = _write_optional_single_jet_summary(
        single_jet_case_dir=single_jet_case_dir,
        single_jet_summary_path=single_jet_summary_path,
    )

    return ARXTrainingResult(
        out_dir=output_path,
        model_path=model_path,
        metrics_path=metrics_path,
        prediction_csv_path=prediction_csv_path,
        prediction_plot_path=prediction_plot_path,
        error_plot_path=error_plot_path,
        rmse_plot_path=rmse_plot_path,
        single_jet_summary_path=summary_path,
        split_index=split,
        train_rows=split,
        validation_rows=len(rows) - split,
        metrics=metrics,
    )


def train_arx_rom_from_dataset(
    *,
    dataset_dir: str | Path,
    out_dir: str | Path,
    train_fraction: float = 1.0,
    input_lags: int = 2,
    output_lags: int = 3,
    ridge_alpha: float = 1.0,
) -> ARXDatasetTrainingResult:
    """Train an ARX ROM from many standard case directories.

    Cases are split chronologically by ``case_index`` from ``index.csv``. Lagged
    features are built within each case only, so histories never cross case
    boundaries.
    """

    dataset_path = Path(dataset_dir)
    case_records = _read_dataset_index(dataset_path)
    if len(case_records) < 2:
        raise ValueError("dataset training requires at least two cases")
    if not 0.2 <= train_fraction <= 1.0:
        raise ValueError("train_fraction should be between 0.2 and 1.0")

    model = ARXModel(
        input_lags=input_lags,
        output_lags=output_lags,
        include_current_input=True,
        ridge_alpha=ridge_alpha,
    )
    if train_fraction >= 1.0:
        split_case_count = len(case_records)
    else:
        split_case_count = int(np.floor(len(case_records) * train_fraction))
        split_case_count = min(max(split_case_count, 1), len(case_records) - 1)
    train_records = case_records[:split_case_count]
    validation_records = case_records[split_case_count:]

    train_sequences = [_load_rom_sequence(record["case_dir"]) for record in train_records]
    validation_sequences = [_load_rom_sequence(record["case_dir"]) for record in validation_records]

    model.input_names_ = list(ROM_INPUT_COLUMNS)
    model.output_names_ = list(ROM_OUTPUT_COLUMNS)
    x_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    train_rows = 0
    for sequence in train_sequences:
        inputs = sequence["inputs"]
        outputs = sequence["outputs"]
        if len(inputs) <= model.max_lag:
            raise ValueError(f"case {sequence['case_id']} is too short for max_lag={model.max_lag}")
        x_case, y_case = model._design_matrix(inputs, outputs, model.max_lag, len(inputs))
        x_parts.append(x_case)
        y_parts.append(y_case)
        train_rows += len(y_case)

    x_train = np.vstack(x_parts)
    y_train = np.vstack(y_parts)
    penalty = ridge_alpha * np.eye(x_train.shape[1], dtype=float)
    penalty[0, 0] = 0.0
    lhs = x_train.T @ x_train + penalty
    rhs = x_train.T @ y_train
    try:
        model.coefficients_ = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        model.coefficients_ = np.linalg.pinv(lhs) @ rhs

    prediction_all: np.ndarray | None = None
    truth_all: np.ndarray | None = None
    prediction_rows: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, float]] = {}
    if validation_sequences:
        prediction_parts: list[np.ndarray] = []
        truth_parts: list[np.ndarray] = []
        for sequence in validation_sequences:
            inputs = sequence["inputs"]
            outputs = sequence["outputs"]
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
    model_path = output_path / "arx_model.json"
    prediction_csv_path = output_path / "prediction_timeseries.csv" if validation_sequences else None
    prediction_plot_path = output_path / "prediction_6_load_cells.svg" if validation_sequences else None
    error_plot_path = output_path / "error_6_load_cells.svg" if validation_sequences else None
    rmse_plot_path = output_path / "rmse_bar.svg" if validation_sequences else None

    metrics_payload = _dataset_metrics_payload(
        dataset_dir=dataset_path,
        case_records=case_records,
        train_records=train_records,
        validation_records=validation_records,
        train_rows=train_rows,
        validation_rows=0 if truth_all is None else len(truth_all),
        input_lags=input_lags,
        output_lags=output_lags,
        ridge_alpha=ridge_alpha,
        metrics=metrics,
    )
    write_json(metrics_path, metrics_payload)
    write_json(model_path, model.to_dict())
    if validation_sequences and prediction_csv_path and prediction_plot_path and error_plot_path and rmse_plot_path:
        _write_dataset_prediction_csv(prediction_csv_path, prediction_rows)
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

    return ARXDatasetTrainingResult(
        out_dir=output_path,
        model_path=model_path,
        metrics_path=metrics_path,
        prediction_csv_path=prediction_csv_path,
        prediction_plot_path=prediction_plot_path,
        error_plot_path=error_plot_path,
        rmse_plot_path=rmse_plot_path,
        train_cases=len(train_records),
        validation_cases=len(validation_records),
        train_rows=train_rows,
        validation_rows=0 if truth_all is None else len(truth_all),
        metrics=metrics,
    )


def _metrics_payload(
    *,
    case_dir: str | Path,
    row_count: int,
    split: int,
    input_lags: int,
    output_lags: int,
    ridge_alpha: float,
    metrics: dict[str, dict[str, float]],
) -> dict[str, Any]:
    return {
        "case_dir": str(case_dir),
        "model": {
            "type": "ARX",
            "input_lags": input_lags,
            "output_lags": output_lags,
            "include_current_input": True,
            "ridge_alpha": ridge_alpha,
            "uses_future_outputs": False,
            "split_policy": "chronological contiguous split; no random shuffling",
        },
        "data": {
            "row_count": row_count,
            "train_rows": split,
            "validation_rows": row_count - split,
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
        },
        "metrics": metrics,
        "error_interpretation": {
            "delay": "If peaks are shifted in the error plot, increase input/output lags or add an explicit transport delay.",
            "noise": "Irregular high-frequency residuals are expected because the mock plant adds output noise.",
            "model_order": "Too few lags underfit slow dynamics; too many lags are fragile with small training sets.",
            "input_correlation": "PRBS rows may activate several jets together, so individual jet coefficients can be correlated.",
            "data_amount": "Short demo cases are workflow checks; final coefficients need richer excitation data.",
        },
    }


def _dataset_metrics_payload(
    *,
    dataset_dir: Path,
    case_records: list[dict[str, Any]],
    train_records: list[dict[str, Any]],
    validation_records: list[dict[str, Any]],
    train_rows: int,
    validation_rows: int,
    input_lags: int,
    output_lags: int,
    ridge_alpha: float,
    metrics: dict[str, dict[str, float]],
) -> dict[str, Any]:
    return {
        "dataset_dir": str(dataset_dir),
        "model": {
            "type": "ARX",
            "input_lags": input_lags,
            "output_lags": output_lags,
            "include_current_input": True,
            "ridge_alpha": ridge_alpha,
            "uses_future_outputs": False,
            "split_policy": "case-level chronological split by dataset index; no lag history crosses case boundaries",
        },
        "data": {
            "case_count": len(case_records),
            "train_cases": len(train_records),
            "validation_cases": len(validation_records),
            "train_rows": train_rows,
            "validation_rows": validation_rows,
            "train_case_ids": [record["case_id"] for record in train_records],
            "validation_case_ids": [record["case_id"] for record in validation_records],
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
        },
        "metrics": metrics,
    }


def _read_dataset_index(dataset_dir: Path) -> list[dict[str, Any]]:
    index_path = dataset_dir / "index.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"dataset index not found: {index_path}")
    with index_path.open("r", encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    if not records:
        raise ValueError(f"dataset index contains no cases: {index_path}")
    for record in records:
        record["case_index"] = int(record.get("case_index", len(records)))
        record["case_dir"] = str(dataset_dir / record["case_id"]) if not record.get("case_dir") else record["case_dir"]
    return sorted(records, key=lambda record: record["case_index"])


def _load_rom_sequence(case_dir: str | Path) -> dict[str, Any]:
    rows = load_case_table(case_dir)
    missing_massflow = [column for column in MASSFLOW_COLUMNS if column not in rows[0]]
    if missing_massflow:
        raise ValueError(
            f"{case_dir} is missing mass-flow command columns: "
            + ", ".join(missing_massflow[:4])
            + ("..." if len(missing_massflow) > 4 else "")
        )
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


def _write_dataset_prediction_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["case_id", "physical_time"]
    for column in ROM_OUTPUT_COLUMNS:
        fieldnames.extend([f"{column}_true", f"{column}_pred", f"{column}_error"])
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_optional_single_jet_summary(
    *,
    single_jet_case_dir: str | Path | None,
    single_jet_summary_path: str | Path | None,
) -> Path | None:
    if single_jet_case_dir is None:
        return None
    summary_path = Path(single_jet_summary_path or "B06_single_jet_response_summary.csv")
    single_jet_rows = load_case_table(single_jet_case_dir)
    write_single_jet_response_summary(summary_path, single_jet_rows)
    return summary_path
