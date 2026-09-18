"""无喷气参考（基准）激励模式。

原理：
  所有喷口在整个实验期间保持关闭（全零计划）。
  用于采集基准流场数据，作为有喷气 case 的对比参考。

适用于：
  - 建立零喷气条件下的基线载荷
  - 用于背景噪声分析和系统误差评估
  - 作为闭环控制的参考状态
"""

from __future__ import annotations

from .common import ActuationConfig, ScheduleTable, empty_switches, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    """生成全关参考激励计划。

    Args:
        config: 仅使用总窗口数和喷口数。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    switches = empty_switches(config.total_windows, config.n_jets)
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {"purpose": "baseline with all jet valves closed"}, []
