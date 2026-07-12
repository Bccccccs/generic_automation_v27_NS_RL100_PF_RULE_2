#!/usr/bin/env python3
"""选择一种激励模式，然后选择运行方式（Mock 或 CCM）。"""

from pathlib import Path

from _common import (
    ACTION_LABELS,
    ccm_command_args,
    configure_project_root,
    read_ccm_config,
    reexec_with_project_python,
    run_module,
)


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

    print("\n选择运行方式，输入数字 1 或 2：")
    print("1) mock  — 使用 MockDynamicPlant24x6（免 CCM 许可，适合算法开发）")
    print("2) ccm   — 使用 STAR-CCM+（需要 CCM 许可和已配置的 .sim 文件）")
    mode_choice = input("方式编号: ").strip()

    if mode_choice == "1":
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
        print(f"\nMock outputs in: {output_dir}")
        return

    if mode_choice == "2":
        ccm = read_ccm_config()
        sim_path = str(ccm["sim_path"])
        if not Path(sim_path).is_file():
            raise SystemExit(f".sim 文件不存在：{sim_path}")
        output_dir = Path(f"runs/starccm_{action_name}")
        print(f"\nGenerating schedule and launching CCM: {action_name} -> {output_dir}")
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
        print(f"\nCCM outputs in: {output_dir}")
        return

    raise SystemExit(f"无效输入：{mode_choice}。请输入 1 或 2。")


if __name__ == "__main__":
    main()
