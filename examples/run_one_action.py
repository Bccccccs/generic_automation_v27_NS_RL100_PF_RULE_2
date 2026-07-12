#!/usr/bin/env python3
"""选择一种激励模式，只生成 actuation_schedule。"""

from _common import ACTION_LABELS, configure_project_root, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    print("选择要生成的激励模式，输入数字 1-6：")
    print("\n".join(ACTION_LABELS))
    action_choice = input("模式编号: ").strip()
    actions = [
        "no_jet_reference",
        "pulse_singlejet",
        "step_singlejet",
        "chirp_keyjets",
        "prbs_demo",
        "pilot_sparse24",
    ]
    if action_choice not in {"1", "2", "3", "4", "5", "6"}:
        raise SystemExit(f"无效输入：{action_choice}。请输入 1-6。")
    action_name = actions[int(action_choice) - 1]

    output_dir = f"runs/{action_name}"
    print(f"\nGenerating schedule: {action_name} -> {output_dir}", flush=True)
    run_module(
        "flow_control.generator.schedule_generator",
        "--config",
        f"configs/actions/{action_name}.yaml",
        "--output-dir",
        output_dir,
    )

    print("\nDone. Schedule outputs:")
    print(f"{output_dir}/input/actuation_schedule.csv")


if __name__ == "__main__":
    main()
