"""Reduced-order modeling utilities for flow-control response prediction."""

from .arx_model import ARXModel
from .identifier import (
    MASSFLOW_COLUMNS,
    REGIONAL_OUTPUT_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    compute_metrics,
    load_case_table,
    matrix_from_rows,
    merge_schedule_columns,
    read_csv_rows,
    require_columns,
    time_values_from_rows,
    write_error_svg,
    write_json,
    write_prediction_csv,
    write_prediction_svg,
    write_rmse_bar_svg,
    write_single_jet_response_summary,
)
from .training import ARXDatasetTrainingResult, ARXTrainingResult, train_arx_rom_from_case, train_arx_rom_from_dataset
from .inference import ARXUseResult, use_arx_rom_on_case
from .validation import ARXValidationResult, validate_arx_rom

__all__ = [
    "ARXModel",
    "ARXDatasetTrainingResult",
    "ARXTrainingResult",
    "ARXUseResult",
    "ARXValidationResult",
    "MASSFLOW_COLUMNS",
    "REGIONAL_OUTPUT_COLUMNS",
    "ROM_INPUT_COLUMNS",
    "ROM_OUTPUT_COLUMNS",
    "compute_metrics",
    "load_case_table",
    "matrix_from_rows",
    "merge_schedule_columns",
    "read_csv_rows",
    "require_columns",
    "time_values_from_rows",
    "train_arx_rom_from_case",
    "train_arx_rom_from_dataset",
    "use_arx_rom_on_case",
    "validate_arx_rom",
    "write_error_svg",
    "write_json",
    "write_prediction_csv",
    "write_prediction_svg",
    "write_rmse_bar_svg",
    "write_single_jet_response_summary",
]
