"""/use_rom CLI：使用已训练的 ARX 模型对 case 数据进行预测并写出预测结果。

工作流程：
  1. 加载已训练的 ARX 模型（arx_model.json）
  2. 加载 source case 的时序数据和激励计划
  3. 前 max_lag 行作为"预热"历史（复制原始输出）
  4. 从 max_lag 开始进行递推预测（用模型自身输出作为后续预测的反馈）
  5. 将预测结果打包为标准 case 目录（带 case_manifest.yaml 和 quality_report）
"""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.case_paths import resolve_case_dir
from flow_control.rom import use_arx_rom_on_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Use an existing ARX ROM on a case and write a prediction case.")
    parser.add_argument("--model", required=True, help="Path to arx_model.json.")
    # --- 数据源（二选一） ---
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--case-id", help="Input case id under --runs-root.")
    source.add_argument("--case-dir", help="Input case directory.")
    parser.add_argument("--runs-root", default="runs", help="Root for --case-id inputs.")
    parser.add_argument("--out", required=True, help="Output prediction case directory.")
    args = parser.parse_args(argv)

    case_dir = (
        resolve_case_dir(case_id=args.case_id, runs_root=args.runs_root)
        if args.case_id
        else args.case_dir
    )
    result = use_arx_rom_on_case(
        model_path=args.model,
        case_dir=case_dir,
        out_dir=args.out,
    )
    print(f"ARX ROM use complete: {Path(result.out_dir)}")
    print(f"source rows: {result.source_rows}")
    print(f"warmup rows: {result.warmup_rows}")
    print(f"predicted rows: {result.predicted_rows}")
    print(f"timeseries: {result.prediction_timeseries_path}")
    print(f"quality report: {result.quality_report_path}")
    print(f"run_success_flag: {result.run_success_flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
