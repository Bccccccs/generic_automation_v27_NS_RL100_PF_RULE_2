"""单喷口脉冲激励模式。

原理：
  在选定的时间窗口内，仅打开指定编号的一个喷口，
  其余窗口所有喷口关闭。用于测试单个喷口对载荷的瞬态响应。

适用于：
  - 识别单喷口到各载荷通道的脉冲响应函数
  - 分析喷气开启/关闭的瞬态效应
"""

from __future__ import annotations

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    """生成单喷口脉冲激励计划。

    Args:
        config: 使用 jet_ids[0] 指定喷口，pulse_windows 指定脉冲时刻。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    # 默认使用 3 号喷口，在第 1 个窗口激活
    jet_id = config.jet_ids[0] if config.jet_ids else 3
    pulse_windows = config.pulse_windows or (1,)
    switches = empty_switches(config.total_windows, config.n_jets)
    idx = jet_index(jet_id, config.n_jets)
    errors: list[str] = []
    for window_id in pulse_windows:
        if not 0 <= window_id < config.total_windows:
            errors.append(f"pulse window {window_id} outside total_windows={config.total_windows}")
            continue
        switches[window_id][idx] = 1
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {"jet_id": jet_id, "pulse_windows": list(pulse_windows)}, errors
