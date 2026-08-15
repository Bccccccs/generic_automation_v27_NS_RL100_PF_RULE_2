"""Flow-control 适配器：将激励计划行翻译为 STAR-CCM+ 运行时命令序列。

核心职责：
  - 读取 actuation_schedule.csv（激励计划表）
  - 将每一行（每个时间窗口的喷气指令）通过 FlowControlStarCCMTranslator
    翻译为 STAR-CCM+ 运行时命令
  - 将所有窗口的命令拼接为一个扁平化的 StarCCMCommandPlan
  - 提供将计划写入 JSON 文件的能力
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.starccm_translator import FlowControlStarCCMTranslator
from starccm.control import DEFAULT_STARCCM_SPEC, StarCCMControlSpec
from starccm.control.control_spec import JET_COLUMNS
from starccm.runtime import StarCCMCommand, StarCCMCommandPlan


class FlowControlStarCCMAdapter:
    """将 flow-control 激励窗口打包为共享 STAR 运行时层所需的命令计划。

    典型用法：
        adapter = FlowControlStarCCMAdapter()
        plan = adapter.plan_from_schedule_csv("actuation_schedule.csv")
        plan.write_json("starccm_runtime_plan.json")
    """

    def __init__(
        self,
        *,
        spec: StarCCMControlSpec = DEFAULT_STARCCM_SPEC,
        translator: FlowControlStarCCMTranslator | None = None,
    ) -> None:
        """初始化适配器。

        Args:
            spec: STAR-CCM+ 控制规格说明，定义喷口、载荷点等。
            translator: 喷气指令翻译器。为 None 时自动创建。
        """
        self.spec = spec.require_valid()
        self.translator = translator or FlowControlStarCCMTranslator(self.spec)

    def plan_from_schedule_csv(
        self,
        schedule_path: str | Path,
        *,
        time_step: float | None = None,
    ) -> StarCCMCommandPlan:
        """读取 actuation_schedule.csv 并返回扁平化的运行时计划。

        Args:
            schedule_path: 激励计划 CSV 文件的路径。
            time_step: 求解器步长（秒）。为 None 时使用模板仿真文件的步长。

        Returns:
            包含所有窗口命令的 StarCCMCommandPlan。
        """
        path = Path(schedule_path)
        rows = self._read_schedule_rows(path)
        return self.plan_from_schedule_rows(
            rows,
            schedule_path=path,
            time_step=time_step,
        )

    def write_runtime_plan(
        self,
        schedule_path: str | Path,
        output_path: str | Path | None = None,
        *,
        time_step: float | None = None,
    ) -> Path:
        """读取激励计划并写入运行时计划 JSON 文件。

        默认将运行时计划写到激励计划同目录下的 starccm_runtime_plan.json。

        Args:
            schedule_path: 激励计划 CSV 文件路径。
            output_path: 输出 JSON 路径。为 None 时默认写到 schedule 同级目录。
            time_step: 求解器步长。

        Returns:
            写入的运行时计划文件路径。
        """
        schedule = Path(schedule_path)
        plan = self.plan_from_schedule_csv(schedule, time_step=time_step)
        destination = Path(output_path) if output_path is not None else schedule.parent / "starccm_runtime_plan.json"
        plan.write_json(destination)
        return destination

    def plan_from_schedule_rows(
        self,
        rows: Iterable[dict[str, Any]],
        *,
        schedule_path: str | Path | None = None,
        time_step: float | None = None,
    ) -> StarCCMCommandPlan:
        """将激励计划行翻译为 STAR-CCM+ 的一个有序命令计划。

        每个窗口翻译为 SetBoundaryProfile + RunTimeWindow + ReadReports 命令。
        所有窗口的命令按顺序拼接在一起。

        Args:
            rows: 激励计划行（dict 的可迭代对象），每行对应一个时间窗口。
            schedule_path: 原始 CSV 路径（仅用于元数据记录）。
            time_step: 求解器步长。

        Returns:
            用于 STAR-CCM+ 执行器的一个完整命令计划。

        Raises:
            ValueError: 计划为空或行数据无效时抛出。
        """
        raw_schedule_rows = [dict(row) for row in rows]
        if not raw_schedule_rows:
            raise ValueError("actuation schedule must contain at least one row")
        schedule_rows = self._group_schedule_rows(raw_schedule_rows)

        commands: list[StarCCMCommand] = []
        window_ids: list[int] = []
        active_jets: set[str] = set()
        physical_times: list[float] = []

        # 逐行翻译每个时间窗口的喷气指令
        for row_idx, row in enumerate(schedule_rows):
            window_id = self._window_id(row, row_idx)
            duration = self._window_duration(row)
            jet_commands = self._jet_commands(row)
            window_plan = self.translator.translate_window(
                jet_commands,
                window_id=window_id,
                duration=duration,
                time_step=time_step,
            )
            commands.extend(window_plan.commands)
            window_ids.append(window_id)
            active_jets.update(
                column for column, value in jet_commands.items() if float(value) != 0.0
            )
            if "physical_time" in row:
                physical_times.append(self._float_field(row, "physical_time"))

        metadata: dict[str, Any] = {
            "schedule_path": str(Path(schedule_path)) if schedule_path is not None else None,
            "window_count": len(schedule_rows),
            "schedule_row_count": len(raw_schedule_rows),
            "window_ids": window_ids,
            "active_jets": sorted(active_jets),
            "command_source": "cmd_massflow_columns",  # 命令来源：质量流量列
        }
        if physical_times:
            metadata["physical_time_start"] = physical_times[0]
            metadata["physical_time_end"] = round(
                physical_times[-1] + self._window_duration(schedule_rows[-1]),
                12,
            )

        return StarCCMCommandPlan(
            source="flow_control",
            commands=tuple(commands),
            metadata=metadata,
        )

    def _group_schedule_rows(
        self, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """按连续 window_id 合并求解器时间步行。

        同一喷气窗口内的时间必须连续，且 24 路质量流量指令必须保持不变。
        """
        grouped: list[dict[str, Any]] = []
        grouped_commands: list[dict[str, float]] = []
        for row_idx, source in enumerate(rows):
            row = dict(source)
            window_id = self._window_id(row, row_idx)
            commands = self._jet_commands(row)
            if not grouped or self._window_id(grouped[-1], len(grouped) - 1) != window_id:
                if grouped:
                    previous_id = self._window_id(grouped[-1], len(grouped) - 1)
                    if window_id != previous_id + 1:
                        raise ValueError(
                            f"row {row_idx} window_id must increase from {previous_id} to {previous_id + 1}"
                        )
                    if "t_start" in row and "t_end" in grouped[-1]:
                        if abs(self._float_field(row, "t_start") - self._float_field(grouped[-1], "t_end")) > 1.0e-12:
                            raise ValueError(f"row {row_idx} starts outside the previous action window end")
                grouped.append(row)
                grouped_commands.append(commands)
                continue

            previous = grouped[-1]
            if "t_start" in row and "t_end" in previous:
                if abs(self._float_field(row, "t_start") - self._float_field(previous, "t_end")) > 1.0e-12:
                    raise ValueError(f"row {row_idx} is not contiguous inside window_id {window_id}")
            for column, value in commands.items():
                if abs(value - grouped_commands[-1][column]) > 1.0e-12:
                    raise ValueError(
                        f"row {row_idx} changes {column} inside window_id {window_id}"
                    )
            if "t_end" in row:
                previous["t_end"] = row["t_end"]
        return grouped

    def _jet_commands(self, row: dict[str, Any]) -> dict[str, float]:
        """从一行数据中提取喷气指令值。

        优先使用 cmd_massflow_NN 列（质量流量指令），
        如果不存在则退化为使用 JET_NN 列（开关值）。

        Args:
            row: 一行数据字典。

        Returns:
            {JET列名: 指令值} 字典。
        """
        has_massflow_columns = all(column in row for column in MASSFLOW_COLUMNS)
        commands: dict[str, float] = {}
        for jet_column, massflow_column in zip(JET_COLUMNS, MASSFLOW_COLUMNS):
            if has_massflow_columns:
                commands[jet_column] = self._float_field(row, massflow_column)
            else:
                commands[jet_column] = self._float_field(row, jet_column)
        return commands

    def _window_duration(self, row: dict[str, Any]) -> float:
        """从一行数据中提取时间窗口的持续时间。

        优先使用 t_end - t_start 计算持续时间，
        如果这些列不存在则使用 spec 中的默认 window_duration。

        Args:
            row: 一行数据字典。

        Returns:
            持续时间（秒）。
        """
        if "t_start" in row and "t_end" in row:
            duration = round(
                self._float_field(row, "t_end") - self._float_field(row, "t_start"),
                12,
            )
            if duration <= 0.0:
                raise ValueError("actuation schedule row has non-positive t_end - t_start")
            return duration
        return float(self.spec.window_duration)

    @staticmethod
    def _read_schedule_rows(path: Path) -> list[dict[str, str]]:
        """读取 CSV 激励计划文件。

        Args:
            path: CSV 文件路径。

        Returns:
            字典列表，每个字典对应一行。
        """
        if not path.exists():
            raise FileNotFoundError(f"actuation schedule not found: {path}")
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        if not rows:
            raise ValueError(f"actuation schedule is empty: {path}")
        return rows

    @staticmethod
    def _window_id(row: dict[str, Any], fallback: int) -> int:
        """提取窗口 ID，若不存在则使用行索引作为后备。"""
        value = row.get("window_id", fallback)
        try:
            return int(float(value))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid window_id {value!r}") from exc

    @staticmethod
    def _float_field(row: dict[str, Any], field_name: str) -> float:
        """安全地将字段值转为浮点数。"""
        try:
            return float(row.get(field_name, 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid numeric field {field_name}={row.get(field_name)!r}") from exc
