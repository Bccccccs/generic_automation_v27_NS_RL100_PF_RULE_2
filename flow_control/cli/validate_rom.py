"""/validate_rom CLI：在验证数据上评估已训练的 ARX ROM 性能。

验证方式（互斥）：
  --case-id/--case-dir: 在单个 case 上验证
  --dataset-dir:         在 dataset 的多个 case 上验证（可指定起止索引）

输出指标包括每列的 RMSE、NRMSE、相关系数、平均误差和最大绝对误差。
同时生成预测对比图、误差图和 RMSE 柱状图。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.rom import validate_arx_rom
from flow_control.case_paths import resolve_case_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an existing ARX ROM snapshot.")
    parser.add_argument("--model", required=True, help="Path to arx_model.json.")
    parser.add_argument("--out", required=True, help="Output validation directory.")
    # --- 数据源（三选一） ---
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset-dir", help="Dataset directory containing index.csv and many cases.")
    source.add_argument("--case-id", help="Single case id under --runs-root.")
    source.add_argument("--case-dir", help="Single case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    # --- dataset 模式下可选择验证范围 ---
    parser.add_argument("--case-start", type=int, default=0, help="First dataset case index to validate.")
    parser.add_argument("--case-count", type=int, default=None, help="Number of dataset cases to validate.")
    args = parser.parse_args(argv)

    case_dir = (
        resolve_case_dir(case_id=args.case_id, runs_root=args.runs_root)
        if args.case_id
        else args.case_dir
    )
    result = validate_arx_rom(
        model_path=args.model,
        out_dir=args.out,
        dataset_dir=args.dataset_dir,
        case_dir=case_dir,
        case_start=args.case_start,
        case_count=args.case_count,
    )

    print(f"ARX ROM validation complete: {Path(result.out_dir)}")
    print(f"cases: {result.case_count}, rows: {result.validation_rows}")
    print(f"metrics: {result.metrics_path}")
    print(f"prediction CSV: {result.prediction_csv_path}")
    print(f"prediction plot: {result.prediction_plot_path}")
    print(f"error plot: {result.error_plot_path}")
    print(f"RMSE plot: {result.rmse_plot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
