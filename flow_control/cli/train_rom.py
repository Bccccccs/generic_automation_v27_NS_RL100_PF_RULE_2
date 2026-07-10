"""Train and validate an ARX ROM from a flow-control case directory."""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.rom import train_arx_rom_from_case, train_arx_rom_from_dataset


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Train a minimal input-output ARX ROM on flow-control data.")
    parser.add_argument("--case-dir", default=None, help="Single case directory.")
    parser.add_argument("--dataset-dir", default=None, help="Dataset directory containing index.csv and many cases.")
    parser.add_argument("--out", default="runs/rom_mock_demo", help="Output directory.")
    parser.add_argument("--train-fraction", type=float, default=0.70, help="Chronological train fraction.")
    parser.add_argument("--input-lags", type=int, default=2, help="Number of input lag blocks, including u[t].")
    parser.add_argument("--output-lags", type=int, default=3, help="Number of past output lag blocks.")
    parser.add_argument("--ridge-alpha", type=float, default=1.0, help="Ridge penalty.")
    parser.add_argument(
        "--single-jet-case-dir",
        default=None,
        help="Optional case directory used for B06_single_jet_response_summary.csv.",
    )
    parser.add_argument(
        "--single-jet-summary",
        default="B06_single_jet_response_summary.csv",
        help="Output path for the optional single-jet response summary.",
    )
    args = parser.parse_args(argv)

    if args.dataset_dir:
        result = train_arx_rom_from_dataset(
            dataset_dir=args.dataset_dir,
            out_dir=args.out,
            train_fraction=1.0,
            input_lags=args.input_lags,
            output_lags=args.output_lags,
            ridge_alpha=args.ridge_alpha,
        )
        print(f"ARX ROM dataset training complete: {Path(result.out_dir)}")
        print(f"train cases: {result.train_cases}")
        print(f"train rows: {result.train_rows}")
    else:
        result = train_arx_rom_from_case(
            case_dir=args.case_dir or "runs/mock_full_prbs_demo",
            out_dir=args.out,
            train_fraction=args.train_fraction,
            input_lags=args.input_lags,
            output_lags=args.output_lags,
            ridge_alpha=args.ridge_alpha,
            single_jet_case_dir=args.single_jet_case_dir,
            single_jet_summary_path=args.single_jet_summary,
        )
        print(f"ARX ROM training complete: {Path(result.out_dir)}")

    print(f"metrics: {result.metrics_path}")
    print(f"model: {result.model_path}")
    if result.prediction_plot_path is not None:
        print(f"prediction plot: {result.prediction_plot_path}")
    if result.error_plot_path is not None:
        print(f"error plot: {result.error_plot_path}")
    if result.rmse_plot_path is not None:
        print(f"RMSE plot: {result.rmse_plot_path}")
    single_jet_summary_path = getattr(result, "single_jet_summary_path", None)
    if single_jet_summary_path is not None:
        print(f"single-jet summary: {single_jet_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
