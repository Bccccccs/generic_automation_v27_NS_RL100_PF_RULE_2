"""喷气激励模式生成器包，为 flow-control 提供多种激励计划生成策略。

支持的激励模式（6 种）：
  - no_jet_reference:   全零基准模式（无喷气开启）
  - pulse_singlejet:    单喷口脉冲
  - step_singlejet:     单喷口阶跃
  - chirp_keyjets:      关键喷口的频率扫描（chirp）
  - prbs_demo:          伪随机二进制序列切换
  - sparse_random_groups: 稀疏随机分组（激励 + 参考窗口交替）

所有模式都通过统一接口 generate_pattern_table(config) 调用，
返回 ScheduleTable（包含开关状态和指令质量流量）。
"""

from .common import (
    MASSFLOW_COLUMNS,
    SUPPORTED_ACTUATION_MODES,
    ActuationConfig,
    ScheduleTable,
    generate_pattern_table,
    write_pattern_outputs,
)

__all__ = [
    "MASSFLOW_COLUMNS",
    "SUPPORTED_ACTUATION_MODES",
    "ActuationConfig",
    "ScheduleTable",
    "generate_pattern_table",
    "write_pattern_outputs",
]
