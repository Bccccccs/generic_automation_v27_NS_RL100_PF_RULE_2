"""激励计划生成器：从配置模式生成物理时间激励计划。

工作流：
  YAML 配置 → 合并系统默认值 → ActuationConfig → 模式生成器
    → ScheduleTable → 写入 output/input/actuation_schedule.csv 及相关文件

生成的输出文件（均在 <output_dir>/input/ 下）：
  - actuation_schedule.csv:  主激励计划表
  - total_mass_flow.csv:     每窗口总质量流量
  - actuation_heatmap.svg:   喷口激活热图
  - total_mass_flow_curve.svg: 质量流量曲线
  - config_summary.yaml:     配置摘要
  - validation_report.json:  验证报告
"""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import load_config_with_system_defaults
from ..excitation_patterns import ActuationConfig, generate_pattern_table, write_pattern_outputs

INPUT_DIRNAME = "input"


def resolve_input_dir(output_dir: str | Path) -> Path:
    """返回 output_dir/input/ 固定输入目录路径。"""
    return Path(output_dir) / INPUT_DIRNAME


def generate_from_mapping(
    config_data: dict[str, Any],
    *,
    output_dir: str | Path,
) -> ActuationConfig:
    """从配置字典生成激励计划并写入输出目录。

    调用 excitation_patterns 中对应 mode 的生成器，
    然后写 CSV、SVG 和验证报告到 <output_dir>/input/。

    Args:
        config_data: 从 YAML 解析的配置字典。
        output_dir: 输出根目录。

    Returns:
        实际的 ActuationConfig（output_dir 已指向 <根>/input/）。
    """
    # 将 output_dir 重定向为 <output_dir>/input/
    config = replace(
        ActuationConfig.from_mapping(config_data),
        output_dir=resolve_input_dir(output_dir),
    )
    table, extra, errors = generate_pattern_table(config)
    write_pattern_outputs(config, table, validation_errors=errors, extra=extra)
    return config


def generate_from_yaml(
    config_path: str | Path,
    *,
    output_dir: str | Path,
) -> ActuationConfig:
    """从 YAML 文件加载配置并生成激励计划。"""
    return generate_from_mapping(
        load_config_with_system_defaults(config_path),
        output_dir=output_dir,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a physical-time jet actuation schedule."
    )
    parser.add_argument("--config", required=True, help="Actuation YAML configuration.")
    parser.add_argument(
        "--output-dir",
        required=True,
        help="Output root; generated schedule files are written to <output-dir>/input/.",
    )
    args = parser.parse_args()

    config = generate_from_yaml(args.config, output_dir=args.output_dir)
    print(
        "generated actuation schedule: "
        f"mode={config.mode}, jets={config.n_jets}, output={config.output_dir}"
    )


if __name__ == "__main__":
    main()
