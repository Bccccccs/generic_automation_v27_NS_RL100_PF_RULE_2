"""/summarize_single_jet CLI：分析单喷气实验的上升沿响应特征并生成汇总。

这个功能对应 B06 实验要求，分析每次喷气开启（上升沿）后各载荷输出的响应：
  - 哪个载荷输出对喷气最敏感（dominant_output）
  - 响应的峰值变化量（peak_delta）
  - 峰值出现的时间延迟（peak_lag_steps / peak_lag_seconds）

此操作不训练模型，仅基于已有 case 的时序数据计算统计特征。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.rom import load_case_table, write_single_jet_response_summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize rising-edge load responses from an explicit single-jet case."
    )
    parser.add_argument("--case-dir", required=True, help="Single-jet case directory.")
    parser.add_argument(
        "--out",
        default="artifacts/reports/B06_single_jet_response_summary.csv",
        help="Summary CSV output path.",
    )
    args = parser.parse_args(argv)

    output_path = Path(args.out)
    rows = load_case_table(args.case_dir)
    write_single_jet_response_summary(output_path, rows)
    print(f"single-jet response summary: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
