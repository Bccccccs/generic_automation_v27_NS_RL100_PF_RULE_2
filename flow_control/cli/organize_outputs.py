"""CLI for organizing CCM outputs into the standard Week4 case structure."""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.sampling import SAMPLE_OWNERSHIP_AUTO, SAMPLE_OWNERSHIP_MODES
from flow_control.star_ingest.output_organizer import organize_ccm_outputs


def _discover_input_dirs(root: Path = Path("runs")) -> list[Path]:
    if not root.is_dir():
        return []
    return sorted(path for path in root.iterdir() if path.is_dir())


def _choose_directory(root: Path, *, label: str) -> Path:
    """从 root 开始逐级选择目录，0 表示选中当前目录。"""
    current_dir = root
    while True:
        candidates = _discover_input_dirs(current_dir)
        print(f"\n[{label}] 当前目录：{current_dir}")
        print("0) 选择当前目录")
        for idx, path in enumerate(candidates, start=1):
            print(f"{idx}) {path.name}/")
        value = input(f"请选择{label}编号，或直接输入目录路径：").strip()
        if not value:
            raise SystemExit(f"{label}不能为空。")
        if not value.isdigit():
            return Path(value).expanduser()
        idx = int(value)
        if idx == 0:
            return current_dir
        if not 1 <= idx <= len(candidates):
            raise SystemExit(f"无效编号：{value}")
        current_dir = candidates[idx - 1]


def _choose_target_dir(runs_root: Path = Path("runs")) -> tuple[Path, bool]:
    print("\n[整理后存放位置]")
    print("1) 放到新目录")
    print("2) 放到某个已有目录")
    choice = input("请选择 1 或 2：").strip()
    if choice == "1":
        parent = _choose_directory(runs_root, label="新目录的上级目录")
        name = input("请输入新目录名：").strip()
        if not name or Path(name).name != name or name in {".", ".."}:
            raise SystemExit("新目录名不能为空或包含路径分隔符。")
        target = parent / name
        if target.exists():
            raise SystemExit(f"目录已存在：{target}；请选择“已有目录”。")
        return target, False
    if choice == "2":
        return _choose_directory(runs_root, label="已有目标目录"), True
    raise SystemExit(f"无效选择：{choice}。请输入 1 或 2。")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Organize a directory of STAR monitor CSV outputs into a standard Week4 case."
    )
    parser.add_argument(
        "--input-dir",
        help="Directory containing actuation_schedule.csv; omit to choose interactively.",
    )
    parser.add_argument(
        "--star-output-dir",
        help="Directory containing STAR/CCM monitor outputs; omit to choose interactively.",
    )
    parser.add_argument(
        "--output-dir",
        help="Target standard case directory; omit to enter it interactively.",
    )
    parser.add_argument("--force", action="store_true", help="Allow overwriting generated files in the target case.")
    parser.add_argument(
        "--sample-ownership",
        default=SAMPLE_OWNERSHIP_AUTO,
        choices=list(SAMPLE_OWNERSHIP_MODES),
        help=(
            "STAR 样本归属哪个动作窗口：left_closed 表示 [t_start,t_end)，"
            "right_closed 表示 (t_start,t_end]，embedded 表示信任 runtime CSV 自带的 window_id。"
            "auto 仅在 runtime 行自带 window_id 时才推断为 embedded；"
            "monitor-only 导出必须显式指定，不会静默猜测。"
        ),
    )
    args = parser.parse_args(argv)

    runs_root = Path("runs")
    input_dir = (
        Path(args.input_dir).expanduser()
        if args.input_dir
        else _choose_directory(runs_root, label="输入目录（动作表来源）")
    )
    star_output_dir = (
        Path(args.star_output_dir).expanduser()
        if args.star_output_dir
        else _choose_directory(runs_root, label="输出目录（STAR/CCM 结果来源）")
    )
    if args.output_dir:
        output_dir = Path(args.output_dir).expanduser()
        overwrite = args.force
    else:
        output_dir, existing_target = _choose_target_dir(runs_root)
        overwrite = args.force or existing_target

    result = organize_ccm_outputs(
        input_dir=input_dir,
        output_dir=output_dir,
        star_output_dir=star_output_dir,
        overwrite=overwrite,
        sample_ownership=args.sample_ownership,
    )
    print(f"case_dir: {result['case_dir']}")
    print(f"timeseries: {result['timeseries_path']}")
    print(f"schedule: {result['schedule_path']}")
    print(f"raw_star: {result['raw_star_dir']}")
    print(f"sample_ownership: {result['sample_ownership']}")
    print("next: python scripts/workflow.py check --case-dir " + str(result["case_dir"]) + " --mode ccm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
