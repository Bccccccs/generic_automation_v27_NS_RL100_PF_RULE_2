#!/usr/bin/env python3
"""ARX 降阶模型验证流程。"""

from pathlib import Path

from _common import configure_project_root, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    valid_dir = "runs/arx/vaild"
    model_path = "runs/arx/model/arx_model.json"
    validation_out = "runs/arx/validation"

    if not Path(model_path).is_file():
        raise SystemExit(f"模型不存在：{model_path}\n请先运行：python examples/run_rom_train.py")

    print(f"Generating ROM validation dataset -> {valid_dir}")
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

    print(f"\nValidating ARX ROM -> {validation_out}")
    run_module(
        "flow_control.cli.validate_rom",
        "--model",
        model_path,
        "--dataset-dir",
        valid_dir,
        "--out",
        validation_out,
    )

    print("\nDone. Validation outputs:")
    print(f"{validation_out}/metrics.json")
    print(f"{validation_out}/prediction_timeseries.csv")
    print(f"{validation_out}/prediction_6_load_cells.svg")
    print(f"{validation_out}/error_6_load_cells.svg")
    print(f"{validation_out}/rmse_bar.svg")


if __name__ == "__main__":
    main()
