"""Use an existing ARX ROM snapshot to write a prediction case."""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.case_paths import resolve_case_dir
from flow_control.rom import use_arx_rom_on_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Use an existing ARX ROM on a case and write a prediction case.")
    parser.add_argument("--model", required=True, help="Path to arx_model.json.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case-id", help="Input case id under --runs-root.")
    source.add_argument("--case-dir", help="Input case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument("--out", required=True, help="Output prediction case directory.")
    args = parser.parse_args(argv)

    case_dir = (
        resolve_case_dir(case_id=args.case_id, runs_root=args.runs_root)
        if args.case_id
        else args.case_dir
    )
    result = use_arx_rom_on_case(
        model_path=args.model,
        case_dir=case_dir,
        out_dir=args.out,
    )
    print(f"ARX ROM use complete: {Path(result.out_dir)}")
    print(f"source rows: {result.source_rows}")
    print(f"warmup rows: {result.warmup_rows}")
    print(f"predicted rows: {result.predicted_rows}")
    print(f"timeseries: {result.prediction_timeseries_path}")
    print(f"quality report: {result.quality_report_path}")
    print(f"run_success_flag: {result.run_success_flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
