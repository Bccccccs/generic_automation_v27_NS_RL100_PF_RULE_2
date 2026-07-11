"""激励计划生成 → Mock Plant 工作流胶水代码。

将两步过程合并为一步：
  1. 从 YAML 配置生成激励计划 CSV
  2. 运行 MockDynamicPlant24x6 模拟并将结果写入标准 case 目录

这是模块化设计的"胶水"层，确保上游（激励生成）和下游（mock 模拟）
之间的数据流转清晰。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..generator import generate_from_yaml
from .mock_plant import write_mock_dynamic_case


def run_actuation_to_mock(
    *,
    actuation_config_path: str | Path,
    mock_config_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """生成激励计划并运行 mock 模拟，输出到标准目录。

    工作流程：
      1. 从 actuation_config 生成激励计划 → <output_dir>/input/
      2. 用生成的 CSV 驱动 MockDynamicPlant24x6 模拟
      3. 模拟结果通过 CaseSchema.write_case() 输出标准 case 目录

    Args:
        actuation_config_path: 激励配置 YAML 路径。
        mock_config_path: Mock 参数 YAML 路径。
        output_dir: 输出目录，同时也是 case 目录。

    Returns:
        CaseSchema.write_case() 的结果字典。
    """
    actuation_config = generate_from_yaml(
        actuation_config_path,
        output_dir=Path(output_dir),
    )
    schedule_path = actuation_config.output_dir / "actuation_schedule.csv"
    return write_mock_dynamic_case(
        schedule_path=schedule_path,
        config_path=mock_config_path,
        output_dir=output_dir,
    )
