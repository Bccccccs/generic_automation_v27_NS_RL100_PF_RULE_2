"""One-step STAR ingest pipeline.

This module keeps the one-shot entry point next to the three explicit steps:

1. ``step1_generate_timeseries`` writes the standard case package skeleton.
2. ``step2_check_case`` writes ``quality_report.json``.
3. ``step3_generate_figures`` writes diagnostic figures.

The one-step pipeline runs the same high-level flow in one command for the
common case where the user wants a complete case package immediately.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .case_data_loader import ingest_star_export, ingest_star_product_dir
from .figures_generator import generate_all_figures
from .star_export_reader import (
    compute_fz_total,
    discover_star_export_csvs,
    read_star_export_bundle,
    read_star_export_csv,
)


def run_star_ingest_pipeline(
    *,
    case_dir: str | Path,
    star_files: list[str | Path] | None = None,
    star_dir: str | Path | None = None,
    force: bool = False,
    jet: bool = False,
    case_type: str | None = None,
    partial: bool = False,
    check_mode: str = "star_ingest",
) -> dict[str, Any]:
    """Run STAR export ingestion, quality checks, and figure generation."""

    source = _resolve_sources(star_files=star_files, star_dir=star_dir)
    resolved_case_type = case_type or ("jet_on" if jet else "unknown")
    case_path = Path(case_dir)
    manifest = _default_manifest(resolved_case_type, check_mode=check_mode)

    print(f"\n{'=' * 60}")
    print("STAR Export Ingestion")
    print(f"{'=' * 60}")
    if source["product_dir"] is not None:
        print(f"  Product: {source['product_dir']}")
    print(f"  Sources: {len(source['star_paths'])} file(s)")
    for star_path in source["star_paths"]:
        print(f"           {star_path}")
    print(f"  Target:  {case_path}")

    print("\n[1/5] Reading STAR export CSV ...")
    data = _read_sources(source["star_paths"])
    print(f"  Detected {len(data['rows'])} rows, {len(data['columns'])} columns")
    print("  Column mapping:")
    for standard_name, raw_name in data["mapping"].items():
        print(f"    {standard_name:25s} <- {raw_name}")

    print("\n[2/5] Computing derived quantities ...")
    compute_fz_total(data["rows"])
    if data["rows"] and "Fz_Total" in data["rows"][0]:
        print("  Fz_Total computed from sensor columns")

    print("\n[3/5] Ingesting into case directory ...")
    if source["product_dir"] is not None:
        result = ingest_star_product_dir(
            source["product_dir"],
            case_dir=case_path,
            case_type=resolved_case_type,
            manifest=manifest,
            overwrite=force,
            require_complete_schema=not partial,
            check_mode=check_mode,
        )
    else:
        result = ingest_star_export(
            source["star_paths"],
            case_dir=case_path,
            manifest=manifest,
            overwrite=force,
            require_complete_schema=not partial,
            check_mode=check_mode,
            notes=(
                f"Auto-ingested from STAR exports: {[path.name for path in source['star_paths']]}\n"
                f"Original columns: {list(data['mapping'].keys())}\n"
                f"Sources: {[str(path.resolve()) for path in source['star_paths']]}"
            ),
        )

    print("\n[4/5] Generating figures ...")
    figures = generate_all_figures(result, case_path / "figures")

    print("\n[5/5] Quality check results:")
    print(f"  Errors:   {len(result['errors'])}")
    print(f"  Warnings: {len(result['warnings'])}")
    _print_quality_messages(result)

    _attach_figures_to_quality_report(case_path, figures)
    _print_output_paths(case_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One-step STAR ingest: read exports, package case, check quality, and generate figures."
    )
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--star-file",
        action="append",
        help=(
            "Path to a STAR-CCM+ export CSV. Repeat --star-file to merge "
            "separate Fz/drag/moment/jet exports on physical_time."
        ),
    )
    source_group.add_argument(
        "--star-dir",
        help=(
            "Path to a STAR-CCM+ product directory containing monitor CSVs. "
            "Recognized force/moment CSVs are merged into timeseries.csv."
        ),
    )
    parser.add_argument("--case-dir", required=True, help="Target case directory.")
    parser.add_argument("--force", action="store_true", help="Overwrite existing case directory.")
    parser.add_argument("--jet", action="store_true", help="Mark as jet case.")
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
    parser.add_argument(
        "--check-mode",
        default="star_ingest",
        choices=("star_ingest", "mock", "arx_use", "ccm"),
        help="Quality-check mode written to the manifest and quality report.",
    )
    args = parser.parse_args(argv)

    run_star_ingest_pipeline(
        star_files=args.star_file,
        star_dir=args.star_dir,
        case_dir=args.case_dir,
        force=args.force,
        jet=args.jet,
        case_type=args.case_type,
        partial=args.partial,
        check_mode=args.check_mode,
    )
    return 0


def _resolve_sources(
    *,
    star_files: list[str | Path] | None,
    star_dir: str | Path | None,
) -> dict[str, Any]:
    if star_dir is not None:
        product_dir = Path(star_dir)
        if not product_dir.is_dir():
            raise SystemExit(f"ERROR: STAR product directory not found: {product_dir}")
        star_paths = discover_star_export_csvs(product_dir)
        if not star_paths:
            raise SystemExit(f"ERROR: no recognized STAR monitor CSVs found in {product_dir}")
        return {"product_dir": product_dir, "star_paths": star_paths}

    star_paths = [Path(value) for value in star_files or []]
    missing = [path for path in star_paths if not path.exists()]
    if missing:
        raise SystemExit(f"ERROR: STAR file(s) not found: {missing}")
    return {"product_dir": None, "star_paths": star_paths}


def _read_sources(star_paths: list[Path]) -> dict[str, Any]:
    return (
        read_star_export_csv(star_paths[0])
        if len(star_paths) == 1
        else read_star_export_bundle(star_paths)
    )


def _default_manifest(case_type: str, *, check_mode: str) -> dict[str, Any]:
    return {
        "case_type": case_type,
        "check_mode": check_mode,
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


def _print_quality_messages(result: dict[str, Any]) -> None:
    if result["errors"]:
        print("\n  ERRORS:")
        for error in result["errors"]:
            print(f"    ! {error}")
    if result["warnings"]:
        print("\n  WARNINGS:")
        for warning in result["warnings"]:
            print(f"    ? {warning}")
    if not result["errors"]:
        print("  Case passed all checks.")
    else:
        print(f"\n  Case has {len(result['errors'])} error(s); see above.")


def _attach_figures_to_quality_report(case_path: Path, figures: dict[str, Path | None]) -> None:
    report_path = case_path / "quality_report.json"
    quality_report: dict[str, Any] = {}
    if report_path.exists():
        try:
            quality_report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            quality_report = {}
    quality_report["figures"] = {
        name: str(path.relative_to(case_path)) if path else None
        for name, path in figures.items()
    }
    report_path.write_text(
        json.dumps(quality_report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _print_output_paths(case_path: Path, result: dict[str, Any]) -> None:
    print("\nOutput files:")
    print(f"  {case_path / 'case_manifest.yaml'}")
    print(f"  {case_path / 'timeseries.csv'}")
    print(f"  {case_path / 'actuation_schedule.csv'}")
    print(f"  {case_path / 'quality_report.json'}")
    print(f"  {case_path / 'figures' / ''}")
    print(f"  {case_path / 'notes.md'}")
    print(f"\n{'=' * 60}")
    print(f"Ingestion complete. Case ID: {result['case_id']}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    raise SystemExit(main())
