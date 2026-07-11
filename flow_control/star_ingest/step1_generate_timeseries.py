#!/usr/bin/env python3
"""Step 1: generate standard case files from STAR CSV exports.

This step writes ``timeseries.csv`` and the surrounding case package skeleton.
It intentionally does not run quality checks or generate figures.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .case_data_loader import ingest_star_export, ingest_star_product_dir
from ..case_paths import resolve_case_dir


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Step 1: generate timeseries.csv from STAR exports."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument("--star-dir", help="STAR product directory containing monitor CSVs.")
    source_group.add_argument(
        "--star-file",
        action="append",
        help="STAR monitor CSV. Repeat to merge multiple files on physical_time.",
    )
    output_group = parser.add_mutually_exclusive_group(required=True)
    output_group.add_argument("--case-id", help="Case id written under --runs-root, default runs/<case-id>.")
    output_group.add_argument("--case-dir", help="Explicit output standard case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id outputs.")
    parser.add_argument("--case-type", choices=("unknown", "no_jet", "jet_on"), default="unknown")
    parser.add_argument("--force", action="store_true", help="Overwrite the output case directory.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Mark the generated package as partial_timeseries instead of full_case.",
    )
    parser.add_argument(
        "--check-mode",
        default="star_ingest",
        choices=("star_ingest", "mock", "arx_use", "ccm"),
        help="Quality-check mode recorded in case_manifest.yaml.",
    )
    args = parser.parse_args()

    try:
        case_dir = resolve_case_dir(
            case_id=args.case_id,
            case_dir=args.case_dir,
            runs_root=args.runs_root,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.star_dir:
        result = ingest_star_product_dir(
            args.star_dir,
            case_dir=case_dir,
            case_type=args.case_type,
            overwrite=args.force,
            require_complete_schema=not args.partial,
            check_mode=args.check_mode,
            write_final_quality_report=False,
        )
    else:
        star_files = [Path(value) for value in args.star_file or []]
        missing = [path for path in star_files if not path.exists()]
        if missing:
            raise SystemExit(f"STAR file(s) not found: {missing}")
        result = ingest_star_export(
            star_files,
            case_dir=case_dir,
            manifest={
                "case_type": args.case_type,
                "check_mode": args.check_mode,
                "case_stage": "starccm_ingest",
            },
            overwrite=args.force,
            require_complete_schema=not args.partial,
            check_mode=args.check_mode,
            write_final_quality_report=False,
        )
    (case_dir / "notes.md").write_text(
        "# STAR timeseries generation\n\n"
        "Step 1 completed. Run `python -m flow_control.star_ingest.step2_check_case` next.\n",
        encoding="utf-8",
    )

    print(f"generated timeseries: {case_dir / 'timeseries.csv'}")
    print(
        "rows="
        f"{result['quality_report'].get('num_timeseries_rows', len(result.get('timeseries', [])))} "
        f"columns={result['quality_report'].get('num_timeseries_columns', 0)}"
    )


if __name__ == "__main__":
    main()
