#!/usr/bin/env python3
"""Step 2: validate a generated standard case directory."""

from __future__ import annotations

import argparse

from .case_data_loader import write_quality_report
from ..case_paths import resolve_case_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 2: validate STAR ingest case data.")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--case-id", help="Case id under --runs-root, default runs/<case-id>.")
    target_group.add_argument("--case-dir", help="Explicit standard case directory to check.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Only require physical_time; skip full required-column checks.",
    )
    parser.add_argument(
        "--check-mode",
        default=None,
        choices=("star_ingest", "mock", "arx_use", "ccm"),
        help="Override the check mode recorded in the manifest.",
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
    report = write_quality_report(
        case_dir,
        require_complete_schema=not args.partial,
        check_mode=args.check_mode,
    )
    print(f"quality report: {case_dir / 'quality_report.json'}")
    print(f"errors={report['num_errors']} warnings={report['num_warnings']}")
    for error in report["errors"][:20]:
        print(f"ERROR: {error}")
    if len(report["errors"]) > 20:
        print(f"... {len(report['errors']) - 20} more errors")
    for warning in report["warnings"][:20]:
        print(f"WARNING: {warning}")
    if len(report["warnings"]) > 20:
        print(f"... {len(report['warnings']) - 20} more warnings")

if __name__ == "__main__":
    main()
