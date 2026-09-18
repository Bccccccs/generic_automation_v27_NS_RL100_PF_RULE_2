"""CLI for case quality validation and diagnostic figure generation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from flow_control.cli.organize_outputs import _choose_directory
from flow_control.star_ingest.case_data_loader import load_case, write_quality_report
from flow_control.star_ingest.figures_generator import generate_all_figures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a standard case and generate diagnostic figures.")
    parser.add_argument(
        "--case-dir",
        help="Standard case directory; omit to choose interactively under runs.",
    )
    parser.add_argument("--mode", choices=("ccm", "mock"), default="ccm", help="Quality-check profile.")
    parser.add_argument("--partial", action="store_true", help="Allow an incomplete schema for debugging.")
    args = parser.parse_args(argv)

    case_dir = (
        Path(args.case_dir).expanduser()
        if args.case_dir
        else _choose_directory(Path("runs"), label="质量检查 Case 目录")
    )
    print(f"[1/2] 正在检查 Case：{case_dir}")
    report = write_quality_report(
        case_dir,
        require_complete_schema=not args.partial,
        check_mode=args.mode,
    )
    print("[2/2] 正在生成质量诊断 PNG…")
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
    print(f"quality figures: {case_dir / 'figures'}")
    print(f"errors: {report.get('num_errors', len(report.get('errors', [])))}")
    print(f"warnings: {report.get('num_warnings', len(report.get('warnings', [])))}")
    print(
        "blocking_issues: "
        f"{int(report.get('num_ccm_contract_blocking_issues', 0)) + int(report.get('num_physics_blocking_issues', 0))}"
    )
    print(f"run_success_flag: {report.get('run_success_flag')}")
    print("next: python scripts/workflow.py figures")
    return 0 if report.get("run_success_flag") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
