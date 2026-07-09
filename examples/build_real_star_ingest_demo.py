#!/usr/bin/env python3
"""Build ``real_star_ingest_demo`` from the real single-jet STAR exports.

Generates:
  - case_manifest.yaml
  - timeseries.csv
  - actuation_schedule.csv
  - quality_report.json
  - figures/force_timeseries.png
  - figures/jet_schedule.png
  - figures/massflow_check.png
  - figures/quality_summary.png
  - notes.md
"""

from __future__ import annotations

import json
import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from flow_control.star_ingest.case_data_loader import ingest_star_export
from flow_control.star_ingest.figures_generator import generate_all_figures
from flow_control.star_ingest.star_export_reader import read_star_export_bundle


DEFAULT_CASE_DIR = PROJECT_ROOT / "runs" / "real_star_ingest_demo"
DEFAULT_SOURCE_DIR = PROJECT_ROOT / "单喷气" / "Excel兼容_UTF8_BOM"


def discover_star_files(source_dir: Path) -> list[Path]:
    """Return all CSV exports in a user-selected STAR output directory."""
    if not source_dir.is_dir():
        raise FileNotFoundError(f"STAR source directory not found: {source_dir}")
    files = sorted(source_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"No CSV files found in: {source_dir}")
    return files


def build_demo(source_dir: Path, case_dir: Path, case_type: str = "jet_on") -> dict:
    """Run ingestion on all real exports and return the validated case."""
    star_files = discover_star_files(source_dir)
    print(f"Building demo case from: {source_dir}")
    print(f"  Found {len(star_files)} CSV file(s)")

    data = read_star_export_bundle(star_files)
    print(f"  Read {len(data['rows'])} rows, {len(data['columns'])} columns")
    is_jet_case = case_type == "jet_on"

    manifest = {
        "geometry_version": "wind-tunnel-001",
        "mesh_version": "hex-dominant-v2",
        "flow_velocity": 45.0,
        "gap": 0.012,
        "time_step": 1e-4,
        "jet_amplitude": 0.0,
        "window_duration": 0.3,
        "random_seed": 0,
        "case_type": case_type,
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
        "notes": (
            "Real STAR run. "
            + (
                "Jet state and commanded/actual mass-flow channels were not "
                "included in the supplied exports."
                if is_jet_case
                else "This case is declared as no-jet."
            )
        ),
    }

    result = ingest_star_export(
        star_files,
        case_dir=case_dir,
        manifest=manifest,
        overwrite=True,
        require_complete_schema=True,
        notes=(
            "## Demo: Real STAR Export Ingestion\n\n"
            "This case was built from six real STAR-CCM+ monitor exports.\n\n"
            "### Source\n"
            f"- Directory: `{source_dir.resolve()}`\n"
            f"- Files: {', '.join(path.name for path in star_files)}\n"
            f"- Rows: {len(data['rows'])}\n"
            f"- Time range: {data['rows'][0]['physical_time']:.4f}s – "
            f"{data['rows'][-1]['physical_time']:.4f}s\n\n"
            "### Columns\n"
            "- 6 Fz sensor columns (Fz_S1L … Fz_S3R)\n"
            "- Fz_Total (read from the independent STAR Fz Monitor export)\n"
            "- Drag_Total, Pitch_Moment, Roll_Moment and Jet_Reaction_Z\n\n"
            "### Status\n"
            + (
                "- **Single-jet case**.\n"
                "- JET_01...24, cmd_massflow_01...24 and actual_massflow_01...24 "
                "were not supplied, so quality validation intentionally fails "
                "those required columns; no zero-valued data is fabricated.\n"
                if is_jet_case
                else
                "- **No-jet case**. Jet schedule and mass-flow figures are "
                "marked not applicable.\n"
            )
        ),
    )

    print(f"  Errors:   {len(result['errors'])}")
    print(f"  Warnings: {len(result['warnings'])}")
    if result["errors"]:
        print(f"  ! Errors: {result['errors']}")
    if result["warnings"]:
        print(f"  ? Warnings: {result['warnings']}")

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a selectable STAR CSV directory into a standard case package."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=DEFAULT_SOURCE_DIR,
        help=f"Directory containing STAR CSV exports (default: {DEFAULT_SOURCE_DIR})",
    )
    parser.add_argument(
        "--case-dir",
        type=Path,
        default=DEFAULT_CASE_DIR,
        help=f"Output standard case directory (default: {DEFAULT_CASE_DIR})",
    )
    parser.add_argument(
        "--case-type",
        choices=("jet_on", "no_jet"),
        default="jet_on",
        help="Physical case type; controls jet-specific quality checks (default: jet_on)",
    )
    args = parser.parse_args()
    source_dir = args.source_dir.expanduser().resolve()
    case_dir = args.case_dir.expanduser().resolve()

    try:
        result = build_demo(source_dir, case_dir, args.case_type)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)

    # Generate figures
    print(f"\n  Generating figures ...")
    figs = generate_all_figures(result, case_dir / "figures")

    # Update quality report with figure paths
    qr_path = case_dir / "quality_report.json"
    with qr_path.open("r") as f:
        qr = json.load(f)
    qr["figures"] = {name: str(path.relative_to(case_dir)) if path else None
                     for name, path in figs.items()}
    with qr_path.open("w") as f:
        json.dump(qr, f, indent=2, ensure_ascii=False)

    print(f"\n  Output files:")
    print(f"    {case_dir / 'case_manifest.yaml'}")
    print(f"    {case_dir / 'timeseries.csv'}")
    print(f"    {case_dir / 'actuation_schedule.csv'}")
    print(f"    {case_dir / 'quality_report.json'}")
    for name, path in figs.items():
        if path:
            print(f"    {path}")
    print(f"    {case_dir / 'notes.md'}")
    print(f"\n  Demo build complete. Case ID: {result['case_id']}")


if __name__ == "__main__":
    main()
