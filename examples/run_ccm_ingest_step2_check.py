#!/usr/bin/env python3
"""CCM 结果导入 Step 2：质量检查。"""

from pathlib import Path

from _common import choose_path_or_prompt, configure_project_root, list_numbered_dirs, reexec_with_project_python


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    case_dirs = sorted(path.parent for path in Path("runs").glob("*/timeseries.csv"))
    if not case_dirs:
        raise SystemExit("未找到可检查目录。需要目录中包含 timeseries.csv。")

    print("当前可检查目录路径：")
    list_numbered_dirs(case_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    case_dir = choose_path_or_prompt(case_dirs)

    from flow_control.star_ingest.case_data_loader import write_quality_report

    report = write_quality_report(
        case_dir,
        require_complete_schema=True,
        check_mode="ccm",
    )

    print(f"\nquality report: {case_dir / 'quality_report.json'}")
    print(f"errors={report['num_errors']} warnings={report['num_warnings']}")
    for error in report["errors"][:20]:
        print(f"ERROR: {error}")
    if len(report["errors"]) > 20:
        print(f"... {len(report['errors']) - 20} more errors")
    for warning in report["warnings"][:20]:
        print(f"WARNING: {warning}")
    if len(report["warnings"]) > 20:
        print(f"... {len(report['warnings']) - 20} more warnings")

    print("\nStep 2 done. Next:")
    print("python examples/run_ccm_ingest_step3_figures.py")


if __name__ == "__main__":
    main()
