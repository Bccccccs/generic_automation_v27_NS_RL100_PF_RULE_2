"""Training-only workflows for the B06 ARX ROM.

Training and validation are deliberately separate.  Every usable row from the
explicitly supplied training case or dataset is used to fit the model.  This
module never selects a validation segment, computes validation metrics, or
writes prediction plots; those responsibilities belong to ``validation.py``.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .arx_model import ARXModel
from .identifier import (
    MASSFLOW_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    load_case_table,
    matrix_from_rows,
    write_json,
)


@dataclass(frozen=True)
class ARXTrainingResult:
    """Artifacts and row counts produced by a training-only ARX run."""

    out_dir: Path
    model_path: Path
    training_summary_path: Path
    train_cases: int
    source_rows: int
    fit_rows: int
    case_ids: tuple[str, ...]


@dataclass(frozen=True)
class ARXDatasetTrainingResult(ARXTrainingResult):
    """Training result for a dataset listed by ``index.csv``."""


def train_arx_rom_from_case(
    *,
    case_dir: str | Path,
    out_dir: str | Path,
    input_lags: int = 2,
    output_lags: int = 3,
    ridge_alpha: float = 1.0,
) -> ARXTrainingResult:
    """Fit on the complete explicitly supplied case and persist the model.

    ``case_dir`` must contain ``timeseries.csv`` and an
    ``actuation_schedule.csv`` with the mass-flow command columns used by the
    ROM inputs.  No rows are reserved for validation.
    """

    sequence = _load_rom_sequence(case_dir)
    model, source_rows, fit_rows = _fit_sequences(
        [sequence],
        input_lags=input_lags,
        output_lags=output_lags,
        ridge_alpha=ridge_alpha,
    )
    result = _persist_training(
        model=model,
        out_dir=out_dir,
        source_kind="case",
        source_path=Path(case_dir),
        sequences=[sequence],
        source_rows=source_rows,
        fit_rows=fit_rows,
    )
    return ARXTrainingResult(**result)


def train_arx_rom_from_dataset(
    *,
    dataset_dir: str | Path,
    out_dir: str | Path,
    input_lags: int = 2,
    output_lags: int = 3,
    ridge_alpha: float = 1.0,
) -> ARXDatasetTrainingResult:
    """Fit on every case listed in the supplied dataset ``index.csv``.

    Lagged features are built independently inside each case, so history never
    crosses case boundaries.  All listed cases are training cases; this
    function has no internal train/validation split.
    """

    dataset_path = Path(dataset_dir)
    case_records = _read_dataset_index(dataset_path)
    sequences = [_load_rom_sequence(record["case_dir"]) for record in case_records]
    model, source_rows, fit_rows = _fit_sequences(
        sequences,
        input_lags=input_lags,
        output_lags=output_lags,
        ridge_alpha=ridge_alpha,
    )
    result = _persist_training(
        model=model,
        out_dir=out_dir,
        source_kind="dataset",
        source_path=dataset_path,
        sequences=sequences,
        source_rows=source_rows,
        fit_rows=fit_rows,
    )
    return ARXDatasetTrainingResult(**result)


def _fit_sequences(
    sequences: list[dict[str, Any]],
    *,
    input_lags: int,
    output_lags: int,
    ridge_alpha: float,
) -> tuple[ARXModel, int, int]:
    if not sequences:
        raise ValueError("training set contains no cases")

    model = ARXModel(
        input_lags=input_lags,
        output_lags=output_lags,
        include_current_input=True,
        ridge_alpha=ridge_alpha,
    )
    model.input_names_ = list(ROM_INPUT_COLUMNS)
    model.output_names_ = list(ROM_OUTPUT_COLUMNS)

    x_parts: list[np.ndarray] = []
    y_parts: list[np.ndarray] = []
    source_rows = 0
    fit_rows = 0
    for sequence in sequences:
        inputs = sequence["inputs"]
        outputs = sequence["outputs"]
        source_rows += len(inputs)
        if len(inputs) <= model.max_lag:
            raise ValueError(
                f"training case {sequence['case_id']} is too short for max_lag={model.max_lag}"
            )
        x_case, y_case = model._design_matrix(inputs, outputs, model.max_lag, len(inputs))
        x_parts.append(x_case)
        y_parts.append(y_case)
        fit_rows += len(y_case)

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
    return model, source_rows, fit_rows


def _persist_training(
    *,
    model: ARXModel,
    out_dir: str | Path,
    source_kind: str,
    source_path: Path,
    sequences: list[dict[str, Any]],
    source_rows: int,
    fit_rows: int,
) -> dict[str, Any]:
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    model_path = output_path / "arx_model.json"
    training_summary_path = output_path / "training_summary.json"
    case_ids = tuple(str(sequence["case_id"]) for sequence in sequences)

    model_payload = model.to_dict()
    model_payload["training_data"] = {
        "source_kind": source_kind,
        "source_path": str(source_path),
        "case_ids": list(case_ids),
        "source_rows": source_rows,
        "fit_rows": fit_rows,
        "fit_policy": "all usable rows from the explicitly supplied training set; no internal split",
    }
    write_json(model_path, model_payload)
    write_json(
        training_summary_path,
        {
            "phase": "training",
            "validation_performed": False,
            "source_kind": source_kind,
            "source_path": str(source_path),
            "case_count": len(sequences),
            "case_ids": list(case_ids),
            "source_rows": source_rows,
            "fit_rows": fit_rows,
            "lag_context_rows_per_case": model.max_lag,
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
            "model": {
                "type": "ARX",
                "input_lags": model.input_lags,
                "output_lags": model.output_lags,
                "include_current_input": model.include_current_input,
                "ridge_alpha": model.ridge_alpha,
            },
            "fit_policy": "all usable rows from the explicitly supplied training set; no internal split",
        },
    )
    return {
        "out_dir": output_path,
        "model_path": model_path,
        "training_summary_path": training_summary_path,
        "train_cases": len(sequences),
        "source_rows": source_rows,
        "fit_rows": fit_rows,
        "case_ids": case_ids,
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
        record["case_dir"] = (
            str(dataset_dir / record["case_id"])
            if not record.get("case_dir")
            else record["case_dir"]
        )
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
        "inputs": matrix_from_rows(rows, ROM_INPUT_COLUMNS),
        "outputs": matrix_from_rows(rows, ROM_OUTPUT_COLUMNS),
    }
