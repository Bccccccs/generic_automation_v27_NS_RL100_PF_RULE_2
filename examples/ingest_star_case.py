#!/usr/bin/env python3
"""Ingest a STAR-CCM+ export CSV into the standard case format.

Usage::

    python examples/ingest_star_case.py --star-file <path> --case-dir <path>

Example::

    python examples/ingest_star_case.py \\\\
        --star-file runs/real_star_ingest_demo/FZ.csv \\\\
        --case-dir runs/real_star_ingest_demo

This script:
1. Reads the STAR-CCM+ export CSV (handles Chinese column names)
2. Maps columns to standard names (Fz_S1L … Fz_S3R, etc.)
3. Computes Fz_Total from sensor columns
4. Writes the standard case directory structure
5. Runs quality checks and generates figures
6. Prints a summary of errors and warnings
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.star_ingest.case_data_loader import ingest_star_export
from flow_control.star_ingest.figures_generator import generate_all_figures
from flow_control.star_ingest.star_export_reader import (
    compute_fz_total,
    read_star_export_csv,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest STAR-CCM+ export CSV into standard case format."
    )
    parser.add_argument(
        "--star-file",
        required=True,
        action="append",
        help=(
            "Path to a STAR-CCM+ export CSV. Repeat --star-file to merge "
            "separate Fz/drag/moment/jet exports on physical_time."
        ),
    )
    parser.add_argument(
        "--case-dir",
        required=True,
        help="Target case directory (will be created)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing case directory",
    )
    parser.add_argument(
        "--jet",
        action="store_true",
        help="Mark as jet case (expect JET_NN and massflow columns)",
    )
    parser.add_argument(
        "--case-type",
        choices=("unknown", "no_jet", "jet_on"),
        default=None,
        help="Case type recorded in case_manifest.yaml. Defaults to jet_on with --jet, otherwise unknown.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Allow a single STAR timeseries subset without full required-column errors.",
    )
    args = parser.parse_args()

    star_paths = [Path(value) for value in args.star_file]
    missing = [path for path in star_paths if not path.exists()]
    if missing:
        print(f"ERROR: STAR file(s) not found: {missing}")
        sys.exit(1)

    case_path = Path(args.case_dir)
    print(f"\n{'='*60}")
    print(f"STAR Export Ingestion")
    print(f"{'='*60}")
    print(f"  Sources: {len(star_paths)} file(s)")
    for star_path in star_paths:
        print(f"           {star_path}")
    print(f"  Target:  {case_path}")

    # ── Step 1: Read STAR export ─────────────────────────────────────────
    print(f"\n[1/5] Reading STAR export CSV ...")
    from flow_control.star_ingest.star_export_reader import read_star_export_bundle
    data = (
        read_star_export_csv(star_paths[0])
        if len(star_paths) == 1
        else read_star_export_bundle(star_paths)
    )
    print(f"  Detected {len(data['rows'])} rows, {len(data['columns'])} columns")
    print(f"  Column mapping:")
    for std, raw in data["mapping"].items():
        print(f"    {std:25s} ← {raw}")

    # ── Step 2: Compute derived quantities ───────────────────────────────
    print(f"\n[2/5] Computing derived quantities ...")
    compute_fz_total(data["rows"])
    if data["rows"] and "Fz_Total" in data["rows"][0]:
        print(f"  Fz_Total computed from sensor columns")

    # ── Step 3: Ingest into case directory ───────────────────────────────
    print(f"\n[3/5] Ingesting into case directory ...")
    case_type = args.case_type or ("jet_on" if args.jet else "unknown")
    manifest = {
        "case_type": case_type,
        "geometry_version": "unknown",
        "mesh_version": "unknown",
        "flow_velocity": 0.0,
        "gap": 0.0,
        "time_step": 0.0,
        "jet_amplitude": 0.0,
        "window_duration": 0.0,
        "random_seed": 0,
        "units": {
            "force": "N",
            "moment": "Nm",
            "massflow": "kg/s",
        },
        "sign_convention": (
            "positive Fz = lift upward; "
            "positive Drag = downstream; "
            "positive Pitch = nose up; "
            "positive Roll = right wing down"
        ),
    }

    result = ingest_star_export(
        star_paths,
        case_dir=case_path,
        manifest=manifest,
        overwrite=args.force,
        require_complete_schema=not args.partial,
        notes=(
            f"Auto-ingested from STAR exports: {[p.name for p in star_paths]}\n"
            f"Original columns: {list(data['mapping'].keys())}\n"
            f"Sources: {[str(p.resolve()) for p in star_paths]}"
        ),
    )

    print(f"\n[4/5] Generating figures ...")
    figs = generate_all_figures(result, case_path / "figures")

    # ── Step 4: Quality check summary ────────────────────────────────────
    print(f"\n[5/5] Quality check results:")
    print(f"  Errors:   {len(result['errors'])}")
    print(f"  Warnings: {len(result['warnings'])}")

    if result["errors"]:
        print(f"\n  ERRORS:")
        for e in result["errors"]:
            print(f"    ! {e}")

    if result["warnings"]:
        print(f"\n  WARNINGS:")
        for w in result["warnings"]:
            print(f"    ? {w}")

    if not result["errors"]:
        print(f"  ✓ Case passed all checks!")
    else:
        print(f"\n  ✗ Case has {len(result['errors'])} error(s) — see above")

    quality_report = result["quality_report"]
    quality_report["figures"] = {
        name: str(path.relative_to(case_path)) if path else None
        for name, path in figs.items()
    }
    import json
    with (case_path / "quality_report.json").open("w", encoding="utf-8") as f:
        json.dump(quality_report, f, indent=2, ensure_ascii=False)

    # ── Output paths ─────────────────────────────────────────────────────
    print(f"\nOutput files:")
    print(f"  {case_path / 'case_manifest.yaml'}")
    print(f"  {case_path / 'timeseries.csv'}")
    print(f"  {case_path / 'actuation_schedule.csv'}")
    print(f"  {case_path / 'quality_report.json'}")
    print(f"  {case_path / 'figures' / ''}")
    print(f"  {case_path / 'notes.md'}")
    print(f"\n{'='*60}")
    print(f"Ingestion complete.  Case ID: {result['case_id']}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
