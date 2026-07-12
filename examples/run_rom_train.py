#!/usr/bin/env python3
"""ARX 降阶模型（ROM）训练完整流程。"""

from _common import configure_project_root, reexec_with_project_python, run_module


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    train_dir = "runs/arx/training"
    model_dir = "runs/arx/model"

    print(f"Generating ROM training dataset -> {train_dir}")
    run_module(
        "flow_control.rom.generate_arx_dataset",
        "--actuation-config",
        "configs/actions/pilot_sparse24.yaml",
        "--mock-config",
        "configs/mock_dynamic24x6.yaml",
        "--out",
        train_dir,
        "--count",
        "100",
        "--overwrite",
    )

    print(f"\nTraining ARX ROM -> {model_dir}")
    run_module(
        "flow_control.cli.train_rom",
        "--dataset-dir",
        train_dir,
        "--out",
        model_dir,
        "--input-lags",
        "2",
        "--output-lags",
        "3",
        "--ridge-alpha",
        "1.0",
    )

    print("\nDone. ROM model:")
    print(f"{model_dir}/arx_model.json")
    print(f"{model_dir}/training_summary.json")


if __name__ == "__main__":
    main()
