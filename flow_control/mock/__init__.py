"""Mock Plant 模块 —— 在无 CFD 仿真时模拟喷气-载荷响应。

核心用途：
  - 在算法开发阶段替代 STAR-CCM+，实现快速迭代
  - 生成的 mock 数据结构与真实 STAR-CCM+ 输出完全兼容
  - 支持 24 喷口 → 6 区域载荷的模拟动态系统

主要组件：
  - MockDynamic24x6Config: 模拟系统参数配置
  - MockDynamicPlant24x6: 24 输入/6 区域输出的模拟动态系统
  - write_mock_dynamic_case: 运行一次模拟并写出标准 case 目录
  - run_actuation_to_mock: 从激励计划生成到模拟的一步式流水线
"""

from .mock_plant import (
    MockDynamic24x6Config,
    MockDynamicPlant24x6,
    read_actuation_schedule,
    spatial_nonuniformity,
    write_mock_dynamic_case,
)
from .pipeline import run_actuation_to_mock

__all__ = [
    "MockDynamic24x6Config",
    "MockDynamicPlant24x6",
    "read_actuation_schedule",
    "spatial_nonuniformity",
    "write_mock_dynamic_case",
    "run_actuation_to_mock",
]
