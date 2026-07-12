#!/usr/bin/env python3
"""ARX 降阶模型验证流程。"""

from pathlib import Path

from _common import choose_arx_model, configure_project_root, normalize_run_dir, reexec_with_project_python, run_module


def discover_validation_cases() -> list[Path]:
    cases: list[Path] = []
    for timeseries in sorted(Path("runs").rglob("timeseries.csv")):
        text_path = timeseries.as_posix()
        if text_path.startswith("runs/arx/"):
            continue
        cases.append(timeseries.parent)
    return cases


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    model_dir = choose_arx_model()
    model_name = model_dir.name
    model_path = (model_dir / "arx_model.json").as_posix()
    validation_out = f"runs/arx/validations/{model_name}"

    print("选择验证方式，输入数字 1 或 2：")
    print("1) existing-case  — 选择当前已有标准 case 验证")
    print("2) auto-10-cases  — 自动生成 10 个 mock case 后验证")
    mode = input("方式编号: ").strip()

    if mode == "1":
        cases = discover_validation_cases()
        if not cases:
            raise SystemExit("未找到可验证 case。需要非 runs/arx 目录中包含 timeseries.csv。")

        print("\n当前可用于 ROM 验证的 case：")
        for idx, case_dir in enumerate(cases, start=1):
            print(f"{idx}) {case_dir.as_posix()}")

        print("\n请输入目录编号，或直接输入目录路径：")
        selection = input("目录: ").strip()
        if not selection:
            raise SystemExit("目录不能为空。")
        if selection.isdigit():
            index = int(selection)
            if index < 1 or index > len(cases):
                raise SystemExit(f"无效编号：{selection}")
            case_dir = cases[index - 1]
        else:
            case_dir = normalize_run_dir(selection)
        if case_dir.as_posix().startswith("runs/arx/"):
            raise SystemExit(f"验证输入不能使用 runs/arx 下的目录：{case_dir}")
        if not (case_dir / "timeseries.csv").is_file():
            raise SystemExit(f"目录缺少 timeseries.csv：{case_dir}")

        print(f"\nValidating ARX ROM on {case_dir} -> {validation_out}", flush=True)
        run_module(
            "flow_control.cli.validate_rom",
            "--model",
            model_path,
            "--case-dir",
            case_dir.as_posix(),
            "--out",
            validation_out,
        )
    elif mode == "2":
        valid_dir = f"runs/arx/vaild_cases/{model_name}"

        print(f"\nGenerating ROM validation dataset -> {valid_dir}", flush=True)
        run_module(
            "flow_control.rom.generate_arx_dataset",
            "--actuation-config",
            "configs/actions/pilot_sparse24.yaml",
            "--mock-config",
            "configs/mock_dynamic24x6.yaml",
            "--out",
            valid_dir,
            "--count",
            "10",
            "--start-seed",
            "20260718",
            "--overwrite",
        )

        print(f"\nValidating ARX ROM -> {validation_out}", flush=True)
        run_module(
            "flow_control.cli.validate_rom",
            "--model",
            model_path,
            "--dataset-dir",
            valid_dir,
            "--out",
            validation_out,
        )
    else:
        raise SystemExit(f"无效输入：{mode}。请输入 1 或 2。")

    print("\nDone. Validation outputs:")
    print(f"{validation_out}/metrics.json")
    print(f"{validation_out}/prediction_timeseries.csv")
    print(f"{validation_out}/prediction_6_load_cells.svg")
    print(f"{validation_out}/error_6_load_cells.svg")
    print(f"{validation_out}/rmse_bar.svg")


if __name__ == "__main__":
    main()
