#!/usr/bin/env python3
"""CCM 结果导入 Step 2：质量检查。"""

from pathlib import Path

from _common import configure_project_root, list_dirs, normalize_run_dir, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    case_dirs = sorted(path.parent for path in Path("runs").glob("*/timeseries.csv"))
    if not case_dirs:
        raise SystemExit("未找到可检查目录。需要目录中包含 timeseries.csv。")

    print("当前可检查目录路径：")
    list_dirs(case_dirs)
    print("\n请输入要执行的目录路径：")
    case_dir = normalize_run_dir(input("目录: ").strip())

    run_module(
        "flow_control.star_ingest.step2_check_case",
        "--case-dir",
        case_dir.as_posix(),
        "--check-mode",
        "ccm",
    )

    print("\nStep 2 done. Next:")
    print("python examples/run_ccm_ingest_step3_figures.py")


if __name__ == "__main__":
    main()
