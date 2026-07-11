"""Fit an ARX ROM on an explicitly supplied training set."""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.rom import train_arx_rom_from_case, train_arx_rom_from_dataset
from flow_control.case_paths import resolve_case_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Train a minimal ARX ROM on the complete explicitly supplied training set."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case-id", help="Single training case id under --runs-root.")
    source.add_argument("--case-dir", help="Single training case directory; every usable row is fitted.")
    source.add_argument(
        "--dataset-dir",
        help="Training dataset directory; every case listed in index.csv is fitted.",
    )
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument("--out", default="runs/rom_mock_demo/model", help="Model output directory.")
    parser.add_argument("--input-lags", type=int, default=2, help="Number of input lag blocks, including u[t].")
    parser.add_argument("--output-lags", type=int, default=3, help="Number of past output lag blocks.")
    parser.add_argument("--ridge-alpha", type=float, default=1.0, help="Ridge penalty.")
    args = parser.parse_args(argv)

    if args.dataset_dir:
        result = train_arx_rom_from_dataset(
            dataset_dir=args.dataset_dir,
            out_dir=args.out,
            input_lags=args.input_lags,
            output_lags=args.output_lags,
            ridge_alpha=args.ridge_alpha,
        )
    else:
        case_dir = (
            resolve_case_dir(case_id=args.case_id, runs_root=args.runs_root)
            if args.case_id
            else args.case_dir
        )
        result = train_arx_rom_from_case(
            case_dir=case_dir,
            out_dir=args.out,
            input_lags=args.input_lags,
            output_lags=args.output_lags,
            ridge_alpha=args.ridge_alpha,
        )

    print(f"ARX ROM training complete: {Path(result.out_dir)}")
    print(f"training cases: {result.train_cases}")
    print(f"source rows: {result.source_rows}")
    print(f"fit rows: {result.fit_rows}")
    print(f"model: {result.model_path}")
    print(f"training summary: {result.training_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
