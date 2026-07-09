"""Train and validate the minimal B06 ARX ROM on mock data.

Default run:

    python3 examples/train_rom_mock.py

The script uses a chronological split.  It never shuffles time points and the
validation forecast feeds back only previous predictions, not future measured
outputs.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from models import ARXModel
from rom_identifier import (
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a minimal input-output ARX ROM on mock data.")
    parser.add_argument("--case-dir", default="runs/mock_full_prbs_demo", help="Mock case directory.")
    parser.add_argument("--out", default="runs/rom_mock_demo", help="Output directory.")
    parser.add_argument("--train-fraction", type=float, default=0.70, help="Chronological train fraction.")
    parser.add_argument("--input-lags", type=int, default=2, help="Number of input lag blocks, including u[t].")
    parser.add_argument("--output-lags", type=int, default=3, help="Number of past output lag blocks.")
    parser.add_argument("--ridge-alpha", type=float, default=1.0, help="Small ridge penalty.")
    parser.add_argument(
        "--single-jet-case-dir",
        default="runs/mock_full_step_singlejet",
        help="Case directory used for B06_single_jet_response_summary.csv.",
    )
    args = parser.parse_args()

    rows = load_case_table(args.case_dir)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

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
        input_lags=args.input_lags,
        output_lags=args.output_lags,
        include_current_input=True,
        ridge_alpha=args.ridge_alpha,
    )
    split = chronological_split_index(
        len(rows),
        args.train_fraction,
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

    metrics_payload = {
        "case_dir": args.case_dir,
        "model": {
            "type": "ARX",
            "input_lags": args.input_lags,
            "output_lags": args.output_lags,
            "include_current_input": True,
            "ridge_alpha": args.ridge_alpha,
            "uses_future_outputs": False,
            "split_policy": "chronological contiguous split; no random shuffling",
        },
        "data": {
            "row_count": len(rows),
            "train_rows": split,
            "validation_rows": len(rows) - split,
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
        },
        "metrics": metrics,
        "error_interpretation": {
            "delay": "If peaks are shifted in the error plot, increase input/output lags or add an explicit transport delay.",
            "noise": "Irregular high-frequency residuals are expected because the mock plant adds output noise.",
            "model_order": "Too few lags underfit slow dynamics; too many lags are fragile with only 80 mock rows.",
            "input_correlation": "PRBS rows may activate several jets together, so individual jet coefficients can be correlated.",
            "data_amount": "The default demo has 56 train rows and 24 validation rows; coefficient estimates are only a smoke test.",
        },
    }
    write_json(out_dir / "metrics.json", metrics_payload)
    write_json(out_dir / "arx_model.json", model.to_dict())
    write_prediction_csv(out_dir / "prediction_timeseries.csv", validation_time, truth, prediction, ROM_OUTPUT_COLUMNS)

    regional_count = len(REGIONAL_OUTPUT_COLUMNS)
    write_prediction_svg(
        out_dir / "prediction_6_load_cells.svg",
        validation_time,
        truth[:, :regional_count],
        prediction[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_error_svg(
        out_dir / "error_6_load_cells.svg",
        validation_time,
        truth[:, :regional_count],
        prediction[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_rmse_bar_svg(out_dir / "rmse_bar.svg", metrics, REGIONAL_OUTPUT_COLUMNS)

    single_jet_rows = load_case_table(args.single_jet_case_dir)
    write_single_jet_response_summary("B06_single_jet_response_summary.csv", single_jet_rows)

    print(f"ARX ROM mock demo complete: {out_dir}")
    print(f"metrics: {out_dir / 'metrics.json'}")
    print(f"prediction plot: {out_dir / 'prediction_6_load_cells.svg'}")
    print(f"error plot: {out_dir / 'error_6_load_cells.svg'}")
    print(f"RMSE plot: {out_dir / 'rmse_bar.svg'}")


if __name__ == "__main__":
    main()
