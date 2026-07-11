#!/usr/bin/env python3
"""Step 3: generate diagnostic figures for a validated case directory."""

from __future__ import annotations

import argparse
import json

from .case_data_loader import load_case
from .figures_generator import generate_all_figures
from ..case_paths import resolve_case_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Step 3: generate STAR ingest figures.")
    target_group = parser.add_mutually_exclusive_group(required=True)
    target_group.add_argument("--case-id", help="Case id under --runs-root, default runs/<case-id>.")
    target_group.add_argument("--case-dir", help="Explicit standard case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument(
        "--partial",
        action="store_true",
        help="Use partial-timeseries validation mode when loading the case.",
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
    result = load_case(case_dir, require_complete_schema=not args.partial)
    figures = generate_all_figures(result, case_dir / "figures")

    report_path = case_dir / "quality_report.json"
    report = {}
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}
    report["figures"] = {
        name: str(path.relative_to(case_dir)) if path else None
        for name, path in figures.items()
    }
    report_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"figures directory: {case_dir / 'figures'}")
    for name, path in figures.items():
        print(f"{name}: {path}")

if __name__ == "__main__":
    main()
