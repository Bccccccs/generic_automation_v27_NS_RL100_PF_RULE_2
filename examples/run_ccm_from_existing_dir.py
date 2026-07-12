#!/usr/bin/env python3
"""从已有激励计划目录启动 STAR-CCM+ 仿真。"""

from pathlib import Path

from _common import (
    ccm_command_args,
    configure_project_root,
    find_schedule,
    list_dirs,
    normalize_run_dir,
    read_ccm_config,
    reexec_with_project_python,
    run_module,
)


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    runs = sorted(path for path in Path("runs").glob("*") if path.is_dir())
    print("runs 下现有目录：")
    list_dirs(runs)

    print("\n请输入要启动 CCM 的目录，例如 runs/pulse_singlejet 或 pulse_singlejet：")
    selected_dir = normalize_run_dir(input("目录: ").strip())
    if not selected_dir.is_dir():
        raise SystemExit(f"目录不存在：{selected_dir}")

    schedule_path = find_schedule(selected_dir)
    ccm = read_ccm_config()
    sim_path = str(ccm["sim_path"])
    if not Path(sim_path).is_file():
        raise SystemExit(f".sim 文件不存在：{sim_path}")

    print(
        "\nCCM config: "
        f"sim={sim_path}, starccm={ccm['starccm_path']}, "
        f"np={ccm['num_cores']}, region={ccm['region']}"
    )
    print("Launching CCM from existing schedule:")
    print(schedule_path.as_posix())

    run_module(
        "flow_control.cli.run_starccm",
        "--schedule",
        schedule_path.as_posix(),
        *ccm_command_args(
            sim_path=sim_path,
            out_dir=selected_dir,
            starccm_path=str(ccm["starccm_path"]),
            num_cores=ccm["num_cores"],
            region_name=str(ccm["region"]),
            podkey=str(ccm.get("podkey") or ""),
        ),
    )

    print("\nDone. CCM outputs generated in:")
    print(selected_dir.as_posix())
    for name in [
        "actuation_schedule.csv",
        "FlowControlRunMacro.java",
        "starccm_runtime_plan.json",
        "starccm_flow_control.log",
        "flow_control_timeseries.csv",
        "timeseries.csv",
        "quality_report.json",
    ]:
        print((selected_dir / name).as_posix())


if __name__ == "__main__":
    main()
