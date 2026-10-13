""""喷气控制指令 → STAR-CCM+ 运行时命令" 翻译器。

职责：
  将高层"喷气控制窗口"（哪个喷口开多少流量）翻译成 STAR-CCM+
  运行时层能执行的具体命令序列。每个命令包括：
  1. SetBoundaryProfile — 设置喷口边界条件（质量流量值）
  2. SetReportBinding — 绑定载荷报告到输出点
  3. RunTimeWindow — 推进求解器运行一个时间窗口
  4. ReadReports — 读取当前窗口的载荷报告值

设计原则：
  - 与 STAR-CCM+ 运行时层解耦：只生成命令对象，不负责执行
  - 支持 Mapping 和 Sequence 两种喷气指令输入格式"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from starccm.control import DEFAULT_STARCCM_SPEC, StarCCMControlSpec
from starccm.runtime import (
    ReadReports,
    RunTimeWindow,
    SetBoundaryProfile,
    SetReportBinding,
    StarCCMCommand,
    StarCCMCommandPlan,
)


class FlowControlStarCCMTranslator:
    """将喷气指令/载荷需求翻译为共享运行时命令的翻译器。

    每个窗口的翻译输出是一个 StarCCMCommandPlan，
    包含多个按顺序执行的底层命令。
    """

    def __init__(self, spec: StarCCMControlSpec = DEFAULT_STARCCM_SPEC) -> None:
        """初始化翻译器。

        Args:
            spec: STAR-CCM+ 控制规格说明，包含喷口定义、载荷报告等信息。
                  默认使用 DEFAULT_STARCCM_SPEC。
        """
        self.spec = spec.require_valid()

    def translate_window(
        self,
        jet_commands: Mapping[str, Any] | Sequence[float],
        *,
        window_id: int,
        duration: float | None = None,
        time_step: float | None = None,
    ) -> StarCCMCommandPlan:
        """将一个喷气控制窗口翻译为完整的运行时命令计划。

        生成命令序列：
          1. 为每个载荷点绑定报告（SetReportBinding）
          2. 为每个喷口设置质量流量边界条件（SetBoundaryProfile）
          3. 运行一个时间窗口的求解器推进（RunTimeWindow）
          4. 读取当前窗口的载荷报告值（ReadReports）

        Args:
            jet_commands: 喷气指令，可以是 {列名: 值} 字典或 [值, ...] 序列
            window_id: 当前窗口的 ID，用于调试和日志
            duration: 时间窗口长度（秒）。为 None 时使用 spec 中的默认值
            time_step: 求解器步长（秒）。为 None 时使用模板仿真文件的步长

        Returns:
            StarCCMCommandPlan: 包含所有命令和元数据的执行计划
        """
        normalized = self._normalize_commands(jet_commands)
        commands: list[StarCCMCommand] = []
        # 步骤 1：绑定所有载荷报告到输出点
        commands.extend(self._load_bindings())
        # 步骤 2：为每个喷口设置质量流量边界条件
        for jet in self.spec.jets:
            commands.append(
                SetBoundaryProfile(
                    boundary_name=jet.boundary_name,
                    profile_name=jet.profile_name,
                    value=normalized[jet.column],
                    column=jet.column,
                )
            )
        # 步骤 3：运行时间窗口
        commands.append(
            RunTimeWindow(
                duration=float(duration if duration is not None else self.spec.window_duration),
                time_step=time_step,
            )
        )
        # 步骤 4：读取报告
        commands.append(
            ReadReports(
                report_names=(
                    *self.spec.load_report_names,
                    *(f"actual_massflow_{idx:02d}" for idx in range(1, 25)),
                )
            )
        )
        return StarCCMCommandPlan(
            source="flow_control",
            commands=tuple(commands),
            metadata={
                "window_id": int(window_id),
                "active_jets": [
                    column for column, value in normalized.items() if float(value) != 0.0
                ],
            },
        )

    def _normalize_commands(self, jet_commands: Mapping[str, Any] | Sequence[float]) -> dict[str, float]:
        """将喷气指令统一标准化为 {列名: 值} 字典格式。

        支持两种输入格式：
        - Mapping（dict）：按列名取值，缺失列默认为 0
        - Sequence（list/tuple）：按喷口顺序取值，必须与 spec.jets 长度一致

        Args:
            jet_commands: 原始喷气指令输入。

        Returns:
            标准化后的 {列名: 浮点值} 字典。
        """
        if isinstance(jet_commands, Mapping):
            return {
                jet.column: float(jet_commands.get(jet.column, 0.0))
                for jet in self.spec.jets
            }
        if len(jet_commands) != len(self.spec.jets):
            raise ValueError(f"expected {len(self.spec.jets)} jet command values")
        return {
            jet.column: float(jet_commands[idx])
            for idx, jet in enumerate(self.spec.jets)
        }

    def _load_bindings(self) -> tuple[SetReportBinding, ...]:
        """为所有载荷点生成报告绑定命令。

        每个载荷点需要将 STAR-CCM+ 的报告（如 Fz_S1L）绑定到
        特定的零件（part）和方向（direction），以便运行时读取。

        Returns:
            报告绑定命令元组。
        """
        return tuple(
            SetReportBinding(
                report_name=point.report_name,
                part_name=point.part_name,
                direction=point.direction,
                column=point.column,
            )
            for point in self.spec.load_points
        )
