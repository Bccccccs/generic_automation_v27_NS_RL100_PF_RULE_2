#!/usr/bin/env python3
"""数据导入 Step 2：质量检查。

先选择 case 目录，再选择检查模式：
- mock：沿用 mock/ROM 基础数据检查
- ccm：真实 STAR/CCM 数据检查，包含 B03/B04 物理接口检查
"""

from pathlib import Path

from _common import (
    choose_path_or_prompt,
    configure_project_root,
    discover_case_dirs_with_timeseries,
    list_numbered_dirs,
    reexec_with_project_python,
)


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    case_dirs = discover_case_dirs_with_timeseries()
    if not case_dirs:
        raise SystemExit("未找到可检查目录。需要目录中包含 processed/timeseries.csv 或 timeseries.csv。")

    print("当前可检查目录路径：")
    list_numbered_dirs(case_dirs)
    print("\n请输入目录编号，或直接输入目录路径：")
    case_dir = choose_path_or_prompt(case_dirs)
    check_mode = choose_check_mode()

    from flow_control.star_ingest.case_data_loader import write_quality_report

    report = write_quality_report(
        case_dir,
        require_complete_schema=True,
        check_mode=check_mode,
    )

    print(f"\nquality report: {case_dir / 'quality_report.json'}")
    print(f"check_profile={report.get('check_profile')}")
    print(f"errors={report['num_errors']} warnings={report['num_warnings']}")
    if report.get("check_profile") == "ccm":
        print(f"ccm_contract_blocking={report.get('num_ccm_contract_blocking_issues', 0)}")
        print(f"physics_blocking={report.get('num_physics_blocking_issues', 0)}")
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


def choose_check_mode() -> str:
    """Prompt for the data-check mode after the case directory is selected."""

    print("\n请选择数据检查模式：")
    print("1) ccm  - 真实 STAR/CCM 数据检查，包含 B03/B04")
    print("2) mock - mock/ROM 基础数据检查")
    selection = input("检查模式 [1/ccm]: ").strip().lower()
    if selection in {"", "1", "ccm"}:
        return "ccm"
    if selection in {"2", "mock"}:
        return "mock"
    raise SystemExit(f"无效检查模式：{selection}。请输入 1/ccm 或 2/mock。")


if __name__ == "__main__":
    main()
