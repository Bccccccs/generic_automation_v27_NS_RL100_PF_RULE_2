#!/usr/bin/env python3
"""选择一种激励模式，生成计划并运行 Mock 模拟。"""

from _common import choose_action, configure_project_root, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    action_name = choose_action("请选择要生成并运行 mock 的动作，输入数字 1-6：")
    output_dir = f"runs/mock_{action_name}"

    print(f"\nGenerating schedule and running mock: {action_name} -> {output_dir}")
    run_module(
        "flow_control.cli.run_mock_dynamic24x6",
        "--actuation-config",
        f"configs/actions/{action_name}.yaml",
        "--config",
        "configs/mock_dynamic24x6.yaml",
        "--out",
        output_dir,
    )

    print("\nDone. Mock outputs:")
    print(f"{output_dir}/input/actuation_schedule.csv")
    print(f"{output_dir}/timeseries.csv")
    print(f"{output_dir}/quality_report.json")


if __name__ == "__main__":
    main()
