#!/usr/bin/env python3
"""选择一种激励模式，生成计划并启动 STAR-CCM+ 仿真。"""

from pathlib import Path

from _common import (
    ccm_command_args,
    choose_action,
    configure_project_root,
    read_ccm_config,
    reexec_with_project_python,
    run_module,
)


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    action_name = choose_action("请选择要生成并启动 CCM 的动作，输入数字 1-6：")
    ccm = read_ccm_config()
    sim_path = str(ccm["sim_path"])
    if not Path(sim_path).is_file():
        raise SystemExit(f".sim 文件不存在：{sim_path}")

    output_dir = Path(f"runs/starccm_{action_name}")
    print(
        "\nCCM config: "
        f"sim={sim_path}, starccm={ccm['starccm_path']}, "
        f"np={ccm['num_cores']}, region={ccm['region']}"
    )
    print(f"Generating schedule and launching CCM: {action_name} -> {output_dir}")

    run_module(
        "flow_control.cli.run_starccm",
        "--actuation-config",
        f"configs/actions/{action_name}.yaml",
        *ccm_command_args(
            sim_path=sim_path,
            out_dir=output_dir,
            starccm_path=str(ccm["starccm_path"]),
            num_cores=ccm["num_cores"],
            region_name=str(ccm["region"]),
            podkey=str(ccm.get("podkey") or ""),
        ),
    )

    print("\nDone. CCM outputs:")
    for name in [
        "input/actuation_schedule.csv",
        "FlowControlRunMacro.java",
        "starccm_runtime_plan.json",
        "starccm_flow_control.log",
        "flow_control_timeseries.csv",
        "timeseries.csv",
        "quality_report.json",
    ]:
        print((output_dir / name).as_posix())


if __name__ == "__main__":
    main()
