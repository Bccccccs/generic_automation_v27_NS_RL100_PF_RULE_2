"""仿真适配器包，连接 flow-control 工作流与 STAR-CCM+ 仿真后端。

当前对外暴露：
  - FlowControlStarCCMAdapter: 将激励计划转换为 STAR-CCM+ 运行时命令计划

内部模块：
  - starccm_adapter: 计划生成（激励行 → 运行时命令）
  - starccm_runner:  宏生成与仿真执行（生成 Java 宏并启动 STAR-CCM+）
"""

from .starccm_adapter import FlowControlStarCCMAdapter

__all__ = ["FlowControlStarCCMAdapter"]
