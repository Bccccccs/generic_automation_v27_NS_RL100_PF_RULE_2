"""关键喷口 Chirp（频率扫描）激励模式。

原理：
  对选定的喷口施加一个正弦波包络，其频率从 start_frequency 线性
  变化到 end_frequency。幅值用正弦包络线调制，实现平滑的频率扫描。

  这种模式用于识别系统的频率响应特性，帮助确定喷气对载荷的
  频域传递函数。

物理公式：
  f(t) = f_start + (f_end - f_start) * (t / T_total)
  envelope(t) = 0.5 * (1 + sin(2*pi * f(t) * t))
  massflow(t) = mass_flow_rate * envelope(t)
"""

from __future__ import annotations

import math

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    """生成 chirp 频率扫描激励计划。

    Args:
        config: 激励配置，使用 jet_ids（默认 [3,7,14,18]）指定扫描的喷口。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    # 默认选择 4 个代表性喷口进行频率扫描
    jet_ids = config.jet_ids or (3, 7, 14, 18)
    switches = empty_switches(config.total_windows, config.n_jets)
    massflows = [[0.0] * config.n_jets for _ in range(config.total_windows)]
    errors: list[str] = []
    total_duration = max(config.total_windows * config.window_duration, config.window_duration)

    for window_id in range(config.total_windows):
        # (window_id + 0.5) 取窗口中间时刻，避免边界效应
        t_mid = (window_id + 0.5) * config.window_duration
        progress = t_mid / total_duration  # 0→1 的归一化进度
        # 当前频率：从 start 线性增加到 end
        frequency = (
            config.chirp_start_frequency_hz
            + (config.chirp_end_frequency_hz - config.chirp_start_frequency_hz) * progress
        )
        # 正弦包络：0→1 平滑变化
        envelope = 0.5 * (1.0 + math.sin(2.0 * math.pi * frequency * t_mid))
        value = config.mass_flow_rate * envelope
        is_on = value > 1e-12
        for jet_id in jet_ids:
            idx = jet_index(jet_id, config.n_jets)
            switches[window_id][idx] = 1 if is_on else 0
            massflows[window_id][idx] = value if is_on else 0.0

    table = ScheduleTable(switches=switches, massflows=massflows)
    extra = {
        "jet_ids": list(jet_ids),
        "chirp_start_frequency_hz": config.chirp_start_frequency_hz,
        "chirp_end_frequency_hz": config.chirp_end_frequency_hz,
        "note": "mass-flow amplitude follows a sinusoidal chirp envelope",
    }
    return table, extra, errors
