"""CLI for case quality validation and diagnostic figure generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flow_control.star_ingest.case_data_loader import load_case, write_quality_report
from flow_control.star_ingest.figures_generator import generate_all_figures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a standard case and generate diagnostic figures.")
    parser.add_argument("--case-dir", required=True, help="Standard case directory.")
    parser.add_argument("--mode", choices=("ccm", "mock"), default="ccm", help="Quality-check profile.")
    parser.add_argument("--partial", action="store_true", help="Allow an incomplete schema for debugging.")
    args = parser.parse_args(argv)

    case_dir = Path(args.case_dir)
    report = write_quality_report(
        case_dir,
        require_complete_schema=not args.partial,
        check_mode=args.mode,
    )
    checked = load_case(
        case_dir,
        require_complete_schema=not args.partial,
        check_mode=args.mode,
    )
    figures = generate_all_figures(checked, case_dir / "figures")
    report["figures"] = {
        name: str(path.relative_to(case_dir)) if path else None
        for name, path in figures.items()
    }
    (case_dir / "quality_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"quality_report: {case_dir / 'quality_report.json'}")
    print(f"figures: {case_dir / 'figures'}")
    print(f"errors: {report.get('num_errors', len(report.get('errors', [])))}")
    print(f"warnings: {report.get('num_warnings', len(report.get('warnings', [])))}")
    print(
        "blocking_issues: "
        f"{int(report.get('num_ccm_contract_blocking_issues', 0)) + int(report.get('num_physics_blocking_issues', 0))}"
    )
    print(f"run_success_flag: {report.get('run_success_flag')}")
    return 0 if report.get("run_success_flag") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
