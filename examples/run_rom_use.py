#!/usr/bin/env python3
"""使用已训练的 ARX 模型对新 case 进行递推预测。"""

from pathlib import Path

from _common import configure_project_root, normalize_run_dir, reexec_with_project_python, run_module


def discover_case_dirs() -> list[Path]:
    case_dirs: list[Path] = []
    for timeseries in sorted(Path("runs").rglob("timeseries.csv")):
        text_path = timeseries.as_posix()
        if text_path.startswith("runs/arx/model/"):
            continue
        if text_path.startswith("runs/arx/validation/"):
            continue
        if text_path.startswith("runs/arx/use_"):
            continue
        case_dirs.append(timeseries.parent)
    return case_dirs


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    model_path = "runs/arx/model/arx_model.json"
    if not Path(model_path).is_file():
        raise SystemExit(f"模型不存在：{model_path}\n请先运行：python examples/run_rom_train.py")

    case_dirs = discover_case_dirs()
    if not case_dirs:
        raise SystemExit("未找到可用 case 目录。需要目录中包含 timeseries.csv。")

    print("当前可用于 ROM 的目录：")
    for idx, case_dir in enumerate(case_dirs, start=1):
        print(f"{idx}) {case_dir.as_posix()}")

    print("\n请输入目录编号，或直接输入目录路径：")
    selection = input("目录: ").strip()
    if not selection:
        raise SystemExit("目录不能为空。")

    if selection.isdigit():
        idx = int(selection)
        if idx < 1 or idx > len(case_dirs):
            raise SystemExit(f"无效编号：{selection}")
        case_dir = case_dirs[idx - 1]
    else:
        case_dir = normalize_run_dir(selection)

    if not case_dir.is_dir():
        raise SystemExit(f"目录不存在：{case_dir}")
    if not (case_dir / "timeseries.csv").is_file():
        raise SystemExit(f"目录缺少 timeseries.csv：{case_dir}")

    safe_name = case_dir.as_posix().removeprefix("runs/").replace("/", "__").replace(" ", "__")
    out_dir = f"runs/arx/use_{safe_name}"

    print(f"\nUsing ARX ROM on {case_dir} -> {out_dir}")
    run_module(
        "flow_control.cli.use_rom",
        "--model",
        model_path,
        "--case-dir",
        case_dir.as_posix(),
        "--out",
        out_dir,
    )

    print("\nDone. ROM prediction outputs:")
    print(f"{out_dir}/timeseries.csv")
    print(f"{out_dir}/quality_report.json")


if __name__ == "__main__":
    main()
