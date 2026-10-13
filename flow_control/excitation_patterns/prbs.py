"""伪随机二进制序列（PRBS）激励模式。

原理：
  每个喷口维护一个 0/1 状态，在每个时间窗口以一定概率翻转状态。
  这种生成的序列具有类似白噪声的频谱特性，适合系统辨识。

  用额外的约束确保每窗口活跃喷口数不超过上限。
  当活跃数超限时，随机选取 max_active 个喷口保持活动。

  这种模式模拟了现实中"随机分组测试"的策略。
"""

from __future__ import annotations

import random

from .common import ActuationConfig, ScheduleTable, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    """生成 PRBS 激励计划。

    Args:
        config: 激励配置，使用 prbs_switch_probability 控制切换概率。

    Returns:
        (ScheduleTable, extra_metadata, errors) 三元组。
    """
    rng = random.Random(config.random_seed)
    # 计算每窗口最大活跃喷口数
    max_active = config.max_active_jets or config.n_active_per_window
    if config.max_total_mass_flow is not None:
        max_active = min(max_active, int(config.max_total_mass_flow // config.mass_flow_rate))
    max_active = max(0, min(max_active, config.n_jets))

    switches: list[list[int]] = []
    state = [0] * config.n_jets
    for _ in range(config.total_windows):
        # 每个喷口独立决定是否翻转状态
        for jet_idx in range(config.n_jets):
            if rng.random() < config.prbs_switch_probability:
                state[jet_idx] = 1 - state[jet_idx]
        # 如果活跃喷口数超限，随机裁剪
        active = [idx for idx, value in enumerate(state) if value]
        if len(active) > max_active:
            keep = set(rng.sample(active, max_active))
            state = [1 if idx in keep else 0 for idx in range(config.n_jets)]
        switches.append(state[:])

    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    extra = {
        "random_seed": config.random_seed,
        "max_active_jets": max_active,
        "prbs_switch_probability": config.prbs_switch_probability,
    }
    return table, extra, []
