"""
flow_control —— 稀疏喷气流动控制调度原型包。

这个包是项目的工作流核心，提供了从激励模式生成 → CFD 仿真驱动 →
数据提取 → 模型训练/验证 → 模型部署的完整链路。主要子模块包括：

- excitation_patterns: 6 种喷气激励模式生成器（脉冲、阶跃、chirp、PRBS、稀疏随机组、参考）
- generator: 从 YAML 配置驱动激励模式生成并写入 actuation_schedule.csv
- adapters: 将激励计划适配到 STAR-CCM+ 运行时（生成 Java 宏、运行仿真）
- star_ingest: 提取 STAR-CCM+ 导出数据，打包成标准 case 目录并进行质量检查
- mock: 模拟动态 plant（24 输入→6 区域输出），用于算法开发时替代真实 CFD
- rom: 降阶模型（ARX 线性模型）的训练、验证、推理全流程
- cli: 命令行入口点

数据流动方向（典型工作流）：
  YAML 配置 → 激励模式生成 → actuation_schedule.csv
    → [STAR-CCM+ 仿真 / Mock Plant 模拟]
    → timeseries.csv + case_manifest.yaml
    → star_ingest 质量检查
    → ARX ROM 训练 / 验证 / 使用
"""

from .data_schema import (
    CaseSchema,
    ControlAction,
    ExperimentConfig,
    JET_COLUMNS,
    MANIFEST_REQUIRED_FIELDS,
    PlantObservation,
    Schedule,
    ScheduleStep,
    TIMESERIES_REQUIRED_COLUMNS,
)

__all__ = [
    "CaseSchema",
    "ControlAction",
    "ExperimentConfig",
    "JET_COLUMNS",
    "MANIFEST_REQUIRED_FIELDS",
    "PlantObservation",
    "Schedule",
    "ScheduleStep",
    "TIMESERIES_REQUIRED_COLUMNS",
]
