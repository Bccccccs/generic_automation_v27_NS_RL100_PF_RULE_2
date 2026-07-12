#!/usr/bin/env python3
"""CCM 结果导入 Step 3：生成诊断图。"""

from pathlib import Path

from _common import configure_project_root, list_dirs, normalize_run_dir, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    case_dirs = sorted(path.parent for path in Path("runs").glob("*/quality_report.json"))
    if not case_dirs:
        raise SystemExit("未找到可生成图片目录。需要目录中包含 quality_report.json。")

    print("当前可生成图片目录路径：")
    list_dirs(case_dirs)
    print("\n请输入要执行的目录路径：")
    case_dir = normalize_run_dir(input("目录: ").strip())

    run_module(
        "flow_control.star_ingest.step3_generate_figures",
        "--case-dir",
        case_dir.as_posix(),
    )

    print("\nStep 3 done. Figures:")
    print((case_dir / "figures").as_posix())


if __name__ == "__main__":
    main()
