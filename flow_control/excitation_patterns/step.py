"""单喷口阶跃激励模式。

原理：
  在指定起始窗口打开一个喷口，持续到结束窗口。
  阶跃信号包含从直流到高频的宽带频谱，适合系统辨识。

适用于：
  - 获取单喷口到各载荷通道的阶跃响应
  - 分析稳态增益和上升时间/稳定时间等时域指标
  - 验证系统的 DC 增益
"""

from __future__ import annotations

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    """生成单喷口阶跃激励计划。

    Args:
        config: 指定 jet_ids[0]（喷口）、step_start_window（阶跃起始）和
                step_end_window（阶跃结束，默认到最后）。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    jet_id = config.jet_ids[0] if config.jet_ids else 3
    start = config.step_start_window
    end = config.step_end_window if config.step_end_window is not None else config.total_windows
    switches = empty_switches(config.total_windows, config.n_jets)
    errors: list[str] = []
    if start < 0 or start >= config.total_windows:
        errors.append(f"step_start_window {start} outside total_windows={config.total_windows}")
    if end <= start or end > config.total_windows:
        errors.append(f"step_end_window {end} must be in ({start}, {config.total_windows}]")
    idx = jet_index(jet_id, config.n_jets)
    # 在 step_start_window 到 step_end_window 之间持续开启喷口
    for window_id in range(max(start, 0), min(end, config.total_windows)):
        switches[window_id][idx] = 1
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {"jet_id": jet_id, "step_start_window": start, "step_end_window": end}, errors
