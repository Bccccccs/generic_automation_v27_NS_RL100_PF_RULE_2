#!/usr/bin/env python3
"""从已有激励计划目录运行 Mock 模拟。"""

from pathlib import Path

from _common import (
    configure_project_root,
    find_schedule,
    list_dirs,
    normalize_run_dir,
    reexec_with_project_python,
    run_module,
)


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    runs = sorted(path for path in Path("runs").glob("*") if path.is_dir())
    print("runs 下现有目录：")
    list_dirs(runs)

    print("\n请输入要运行 mock 的目录，例如 runs/pulse_singlejet 或 pulse_singlejet：")
    selected_dir = normalize_run_dir(input("目录: ").strip())
    if not selected_dir.is_dir():
        raise SystemExit(f"目录不存在：{selected_dir}")

    schedule_path = find_schedule(selected_dir)
    print("\nRunning mock from existing schedule:")
    print(schedule_path.as_posix())
    run_module(
        "flow_control.cli.run_mock_dynamic24x6",
        "--schedule",
        schedule_path.as_posix(),
        "--config",
        "configs/mock_dynamic24x6.yaml",
        "--out",
        selected_dir.as_posix(),
    )

    print("\nDone. Mock outputs generated in:")
    print(selected_dir.as_posix())


if __name__ == "__main__":
    main()
