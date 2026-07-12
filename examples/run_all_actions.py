#!/usr/bin/env python3
"""一键生成所有 6 种激励模式的激励计划 CSV。"""

from _common import ACTION_NAMES, configure_project_root, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    for action_name in ACTION_NAMES:
        config_path = f"configs/actions/{action_name}.yaml"
        output_dir = f"runs/{action_name}"
        print(f"Generating {action_name} -> {output_dir}")
        run_module(
            "flow_control.generator.schedule_generator",
            "--config",
            config_path,
            "--output-dir",
            output_dir,
        )

    print("\nAll actions generated under runs/<action_name>/input/")


if __name__ == "__main__":
    main()
