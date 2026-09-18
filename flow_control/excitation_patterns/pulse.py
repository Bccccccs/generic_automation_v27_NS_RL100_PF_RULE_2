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
        config: 使用 jet_ids[0] 指定喷口，pulse_windows 使用从 1 开始的
            窗口编号。例如窗口 5 在 window_duration=0.1 s 时对应
            [0.4, 0.5) s，写入动作表的 window_id=4。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    # 默认使用 3 号喷口，在第 1 个窗口激活
    jet_id = config.jet_ids[0] if config.jet_ids else 3
    pulse_window_numbers = config.pulse_window_numbers or (1,)
    switches = empty_switches(config.total_actuation_windows, config.n_jets)
    idx = jet_index(jet_id, config.n_jets)
    errors: list[str] = []
    for window_number in pulse_window_numbers:
        if not 1 <= window_number <= config.total_actuation_windows:
            errors.append(
                f"pulse window {window_number} outside "
                f"1..{config.total_actuation_windows}"
            )
            continue
        window_id = window_number - 1
        switches[window_id][idx] = 1
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {
        "jet_id": jet_id,
        "pulse_window_numbers": list(pulse_window_numbers),
        "pulse_window_id_zero_based": [number - 1 for number in pulse_window_numbers],
    }, errors
