"""/run_mock_dynamic24x6 CLI：通过 MockDynamic24x6 模拟运行激励计划。

两种输入模式（互斥）：
  --actuation-config: 从 YAML 实时生成激励计划后再运行 mock
  --schedule:         使用已有的 actuation_schedule.csv 运行 mock

适用于：
  - 在无 STAR-CCM+ 许可时快速验证激励计划设计
  - ARX ROM 训练/验证数据集的批量生成
  - 算法原型开发的快速迭代

数据流：
  YAML 配置 → 激励模式生成 → actuation_schedule.csv → MockDynamicPlant24x6
    → timeseries.csv + case_manifest.yaml + quality_report.json
"""

from __future__ import annotations

import argparse
from pathlib import Path

from flow_control.mock import run_actuation_to_mock, write_mock_dynamic_case


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate or load an actuation schedule, then run MockDynamic24x6."
    )
    # 数据来源：实时生成 vs 已有 CSV（互斥）
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--actuation-config",
        help="Action YAML. The schedule is generated into <out>/input/ before mock runs.",
    )
    source.add_argument(
        "--schedule",
        help="Existing actuation_schedule.csv. The mock output is written to --out.",
    )
    # Mock 参数配置
    parser.add_argument(
        "--config",
        default="configs/mock_dynamic24x6.yaml",
        help="Mock dynamic system YAML.",
    )
    parser.add_argument("--out", required=True, help="Mock case output directory.")
    args = parser.parse_args(argv)

    if args.actuation_config:
        # 一步式：生成激励计划 → 运行 mock
        result = run_actuation_to_mock(
            actuation_config_path=args.actuation_config,
            mock_config_path=args.config,
            output_dir=args.out,
        )
    else:
        # 两步式：使用已有的激励计划 CSV 运行 mock
        result = write_mock_dynamic_case(
            schedule_path=args.schedule,
            config_path=args.config,
            output_dir=args.out,
        )

    output_dir = Path(args.out)
    print(f"mock output: {output_dir}")
    print(f"timeseries: {output_dir / 'timeseries.csv'}")
    print(f"quality_report: {output_dir / 'quality_report.json'}")
    print(f"run_success_flag: {result.get('quality_report', {}).get('run_success_flag')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
