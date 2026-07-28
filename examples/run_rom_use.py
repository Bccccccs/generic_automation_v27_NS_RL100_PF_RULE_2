#!/usr/bin/env python3
"""使用已训练的 ARX 模型对纯 actuation_schedule 进行递推预测。"""

from pathlib import Path

from _common import (
    choose_arx_model,
    configure_project_root,
    find_schedule,
    find_timeseries,
    normalize_run_dir,
    reexec_with_project_python,
    run_module,
)


def has_timeseries(case_dir: Path) -> bool:
    try:
        find_timeseries(case_dir)
    except SystemExit:
        return False
    return True


def discover_schedule_dirs() -> list[Path]:
    schedule_dirs: set[Path] = set()
    for schedule in sorted(Path("runs").rglob("actuation_schedule.csv")):
        if schedule.as_posix().startswith("runs/arx/"):
            continue
        schedule_dir = schedule.parent.parent if schedule.parent.name == "input" else schedule.parent
        if has_timeseries(schedule_dir):
            continue
        schedule_dirs.add(schedule_dir)
    return sorted(schedule_dirs)


def main() -> None:
    reexec_with_project_python()
    configure_project_root()
    model_dir = choose_arx_model()
    model_path = (model_dir / "arx_model.json").as_posix()

    schedule_dirs = discover_schedule_dirs()
    if not schedule_dirs:
        raise SystemExit("未找到可用 schedule 目录。需要目录中包含 actuation_schedule.csv。")

    print("当前可用于 ROM 预测的 schedule 目录：")
    for idx, schedule_dir in enumerate(schedule_dirs, start=1):
        print(f"{idx}) {schedule_dir.as_posix()}")

    print("\n请输入目录编号，或直接输入目录路径：")
    selection = input("目录: ").strip()
    if not selection:
        raise SystemExit("目录不能为空。")

    if selection.isdigit():
        idx = int(selection)
        if idx < 1 or idx > len(schedule_dirs):
            raise SystemExit(f"无效编号：{selection}")
        schedule_dir = schedule_dirs[idx - 1]
    else:
        schedule_dir = normalize_run_dir(selection)

    if not schedule_dir.is_dir():
        raise SystemExit(f"目录不存在：{schedule_dir}")
    if schedule_dir.as_posix().startswith("runs/arx/"):
        raise SystemExit(f"ROM 预测输入不能使用 runs/arx 下的目录：{schedule_dir}")
    if has_timeseries(schedule_dir):
        raise SystemExit(f"目录已有 timeseries，请用验证入口处理已有 case：{schedule_dir}")

    schedule_path = find_schedule(schedule_dir)
    out_dir = schedule_dir.as_posix()

    print("\n请输入 ROM 时间步，直接回车表示读取 config_summary.yaml，缺失时使用 schedule 窗口长度：")
    time_step = input("time_step: ").strip()

    print(f"\nUsing ARX ROM on schedule {schedule_path} -> {out_dir}", flush=True)
    args = [
        "flow_control.cli.use_rom",
        "--model",
        model_path,
        "--schedule",
        schedule_path.as_posix(),
        "--out",
        out_dir,
    ]
    if time_step:
        args.extend(["--time-step", time_step])
    run_module(*args)

    print("\nDone. ROM prediction outputs:")
    print(f"{out_dir}/timeseries.csv")
    print(f"{out_dir}/quality_report.json")


if __name__ == "__main__":
    main()
