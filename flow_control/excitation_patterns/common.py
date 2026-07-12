"""激励模式公共数据模型与 IO 工具。

定义了一套统一的 ScheduleTable 数据结构和激励模式生成/验证/输出流程。
所有 6 种激励模式都遵循相同的接口规范。

数据流动：
  YAML 配置 → ActuationConfig → generate_pattern_table() → ScheduleTable
    → write_pattern_outputs() → actuation_schedule.csv + 诊断 SVG 图
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import yaml

from flow_control.data_schema import JET_COLUMNS

# 标准质量流量列名：cmd_massflow_01 到 cmd_massflow_24
MASSFLOW_COLUMNS = tuple(f"cmd_massflow_{idx:02d}" for idx in range(1, 25))
# 当前支持的 6 种激励模式
SUPPORTED_ACTUATION_MODES = {
    "no_jet_reference",       # 全关参考模式
    "pulse_singlejet",        # 单喷口脉冲
    "step_singlejet",         # 单喷口阶跃
    "chirp_keyjets",          # 关键喷口频率扫描
    "prbs_demo",              # 伪随机二进制序列
    "sparse_random_groups",   # 稀疏随机分组
}


@dataclass(frozen=True)
class ActuationConfig:
    """喷气激励计划生成的配置参数。

    涵盖了所有 6 种激励模式的参数；不同模式使用其中相关的参数子集。
    不可变（frozen）以保证配置在生成过程中的一致性。
    """

    # --- 基本参数 ---
    n_jets: int = 24               # 喷口总数
    mode: str = "sparse_random_groups"  # 激励模式名称
    mass_flow_rate: float = 1.0    # 单喷口质量流量幅值 (kg/s)
    command_amplitude: float | None = None  # 命令幅值（与 mass_flow_rate 同义）
    window_duration: float = 0.1   # 每个时间窗口的持续时间（秒）
    time_step: float = 0.1         # ROM/Mock/CCM 输出响应采样时间步（秒）
    random_seed: int = 20260618    # 随机种子，保证可复现性
    output_dir: Path = Path("runs/schedule_examples/sparse24")  # 输出目录
    total_windows: int = 10        # 总窗口数（对 sparse_groups 按 n_excitation + n_reference 计算）

    # --- 单喷口模式参数 ---
    jet_ids: tuple[int, ...] = ()          # 指定 JET 编号列表
    pulse_windows: tuple[int, ...] = ()    # 脉冲发生的窗口索引
    step_start_window: int = 1             # 阶跃起始窗口
    step_end_window: int | None = None     # 阶跃结束窗口

    # --- sparse_random_groups 专用参数 ---
    n_active_per_window: int = 3       # 每窗口激活喷口数
    n_excitation_windows: int = 72     # 激励窗口数（激活喷口）
    n_reference_windows: int = 8       # 参考窗口数（全关）
    max_consecutive_on: int = 2        # 同一喷口最大连续激活窗口数
    equal_activation_count: bool = True     # 每个喷口激活次数是否必须相等
    max_generation_attempts: int = 300      # 最大尝试次数（搜索有效组合）
    max_active_jets: int | None = None      # 每窗口最大激活喷口数
    max_total_mass_flow: float | None = None  # 每窗口最大总质量流量

    # --- chirp 专用参数 ---
    chirp_start_frequency_hz: float = 1.0   # 起始频率
    chirp_end_frequency_hz: float = 8.0     # 终止频率

    # --- PRBS 专用参数 ---
    prbs_switch_probability: float = 0.35   # 每个喷口每一步的切换概率

    def __post_init__(self) -> None:
        """初始化后处理：统一 command_amplitude 和 mass_flow_rate 两个字段。"""
        if self.command_amplitude is not None:
            object.__setattr__(self, "mass_flow_rate", float(self.command_amplitude))
        else:
            object.__setattr__(self, "command_amplitude", float(self.mass_flow_rate))
        if not isinstance(self.output_dir, Path):
            object.__setattr__(self, "output_dir", Path(self.output_dir))
        if self.time_step <= 0.0:
            object.__setattr__(self, "time_step", float(self.window_duration))

    @property
    def jet_names(self) -> list[str]:
        """返回喷口名称列表，如 ['JET_01', 'JET_02', ...]"""
        return [f"JET_{idx:02d}" for idx in range(1, self.n_jets + 1)]

    @property
    def massflow_names(self) -> list[str]:
        """返回质量流量列名列表，如 ['cmd_massflow_01', 'cmd_massflow_02', ...]"""
        return [f"cmd_massflow_{idx:02d}" for idx in range(1, self.n_jets + 1)]

    @property
    def sparse_total_windows(self) -> int:
        """稀疏模式总窗口数 = 激励窗口 + 参考窗口"""
        return self.n_excitation_windows + self.n_reference_windows

    @property
    def expected_count_per_jet(self) -> int:
        """每个喷口在激励阶段被激活的总期望次数。

        计算公式：
          expected_count = n_excitation_windows * n_active_per_window / n_jets
        这个值必须是整数，否则无法实现等激活次数约束。
        """
        total_activations = self.n_excitation_windows * self.n_active_per_window
        if total_activations % self.n_jets != 0:
            raise ValueError(
                "n_excitation_windows * n_active_per_window must be divisible by n_jets"
            )
        return total_activations // self.n_jets

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ActuationConfig":
        """从 YAML 解析的嵌套字典创建配置。

        期望的 YAML 结构：
          system:
            random_seed: 20260618
          actuation:
            mode: sparse_random_groups
            n_jets: 24
            ...
          output:
            run_dir: runs/schedule_examples/sparse24

        Args:
            data: 从 YAML 解析的配置字典。

        Returns:
            创建的 ActuationConfig 实例。
        """
        system = data.get("system", {})
        actuation = data.get("actuation", {})
        output = data.get("output", {})
        if not actuation:
            raise ValueError("config must contain an 'actuation' section")

        mode = str(actuation.get("mode", "sparse_random_groups")).strip()
        if mode not in SUPPORTED_ACTUATION_MODES:
            raise ValueError(
                f"unsupported actuation mode {mode!r}; expected one of "
                f"{sorted(SUPPORTED_ACTUATION_MODES)}"
            )

        total_windows = int(
            actuation.get(
                "total_windows",
                int(actuation.get("n_excitation_windows", 72))
                + int(actuation.get("n_reference_windows", 8))
                if mode == "sparse_random_groups"
                else 10,
            )
        )
        jet_ids = actuation.get("jet_ids", actuation.get("key_jets", []))
        pulse_windows = actuation.get("pulse_windows", [])
        return cls(
            n_jets=int(actuation.get("n_jets", 24)),
            mode=mode,
            mass_flow_rate=float(
                actuation.get("mass_flow_rate", actuation.get("command_amplitude", 1.0))
            ),
            window_duration=float(actuation.get("window_duration", 0.1)),
            time_step=float(actuation.get("time_step", actuation.get("window_duration", 0.1))),
            random_seed=int(actuation.get("random_seed", system.get("random_seed", 20260618))),
            output_dir=Path(output.get("run_dir", f"runs/schedule_examples/{mode}")),
            total_windows=total_windows,
            jet_ids=tuple(int(value) for value in jet_ids),
            pulse_windows=tuple(int(value) for value in pulse_windows),
            step_start_window=int(actuation.get("step_start_window", 1)),
            step_end_window=(
                int(actuation["step_end_window"])
                if "step_end_window" in actuation
                else None
            ),
            n_active_per_window=int(actuation.get("n_active_per_window", 3)),
            n_excitation_windows=int(actuation.get("n_excitation_windows", 72)),
            n_reference_windows=int(actuation.get("n_reference_windows", 8)),
            max_consecutive_on=int(actuation.get("max_consecutive_on", 2)),
            equal_activation_count=bool(actuation.get("equal_activation_count", True)),
            max_generation_attempts=int(actuation.get("max_generation_attempts", 300)),
            max_active_jets=(
                int(actuation["max_active_jets"])
                if "max_active_jets" in actuation
                else None
            ),
            max_total_mass_flow=(
                float(actuation["max_total_mass_flow"])
                if "max_total_mass_flow" in actuation
                else None
            ),
            chirp_start_frequency_hz=float(actuation.get("chirp_start_frequency_hz", 1.0)),
            chirp_end_frequency_hz=float(actuation.get("chirp_end_frequency_hz", 8.0)),
            prbs_switch_probability=float(actuation.get("prbs_switch_probability", 0.35)),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ActuationConfig":
        """从 YAML 文件中直接加载配置。"""
        with Path(path).open("r", encoding="utf-8") as handle:
            return cls.from_mapping(yaml.safe_load(handle) or {})


@dataclass(frozen=True)
class ScheduleTable:
    """统一的激励计划表：包含开关状态和指令质量流量。

    switches:   JET_NN 的 0/1 开关矩阵，[n_windows × n_jets]
    massflows:  cmd_massflow_NN 的质量流量值矩阵，[n_windows × n_jets]
    """

    switches: list[list[int]]
    massflows: list[list[float]]

    @property
    def n_windows(self) -> int:
        return len(self.switches)

    @property
    def n_jets(self) -> int:
        return len(self.switches[0]) if self.switches else 0


def table_from_switches(
    switches: list[list[int]],
    *,
    mass_flow_rate: float,
    massflows: list[list[float]] | None = None,
) -> ScheduleTable:
    """从开关矩阵创建 ScheduleTable。

    如果未提供 massflows，则按照 mass_flow_rate * switches 生成质量流量矩阵。

    Args:
        switches: 0/1 开关矩阵。
        mass_flow_rate: 单喷口质量流量幅值。
        massflows: 可选的质量流量值矩阵。

    Returns:
        ScheduleTable 实例。
    """
    if massflows is None:
        massflows = [
            [float(value) * mass_flow_rate for value in row]
            for row in switches
        ]
    return ScheduleTable(switches=switches, massflows=massflows)


def empty_switches(total_windows: int, n_jets: int) -> list[list[int]]:
    """创建全零的开关矩阵（所有喷口关闭）。"""
    return [[0] * n_jets for _ in range(total_windows)]


def jet_index(jet_id: int, n_jets: int) -> int:
    """将 1-based 的 JET 编号转换为 0-based 的列索引。"""
    if not 1 <= jet_id <= n_jets:
        raise ValueError(f"jet_id must be in [1, {n_jets}], got {jet_id}")
    return jet_id - 1


def rows_from_table(config: ActuationConfig, table: ScheduleTable) -> list[dict[str, Any]]:
    """将 ScheduleTable 转换为 CSV 行的字典列表。

    每行包含：physical_time, window_id, t_start, t_end,
    JET_01..JET_NN 的 0/1 开关值，cmd_massflow_01..cmd_massflow_NN 的质量流量值。
    """
    rows: list[dict[str, Any]] = []
    for window_id, (switch_row, massflow_row) in enumerate(zip(table.switches, table.massflows)):
        start = round(window_id * config.window_duration, 12)
        end = round(start + config.window_duration, 12)
        record: dict[str, Any] = {
            "physical_time": start,
            "window_id": window_id,
            "t_start": start,
            "t_end": end,
        }
        for idx, column in enumerate(config.jet_names):
            record[column] = int(switch_row[idx]) if idx < len(switch_row) else 0
        for idx, column in enumerate(config.massflow_names):
            record[column] = float(massflow_row[idx]) if idx < len(massflow_row) else 0.0
        rows.append(record)
    return rows


def write_schedule_csv(config: ActuationConfig, table: ScheduleTable) -> None:
    """将激励计划写入 actuation_schedule.csv。"""
    config.output_dir.mkdir(parents=True, exist_ok=True)
    columns = ["physical_time", "window_id", "t_start", "t_end", *config.jet_names, *config.massflow_names]
    with (config.output_dir / "actuation_schedule.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows_from_table(config, table):
            writer.writerow(row)


def write_config_summary(
    config: ActuationConfig,
    table: ScheduleTable,
    validation_errors: list[str],
    extra: dict[str, Any] | None = None,
) -> None:
    """写入激励计划配置摘要（YAML）和验证报告（JSON）。"""
    active_counts = [sum(row[idx] for row in table.switches) for idx in range(table.n_jets)]
    total_massflows = [sum(row) for row in table.massflows]
    summary = {
        "mode": config.mode,
        "random_seed": config.random_seed,
        "n_jets": config.n_jets,
        "total_windows": table.n_windows,
        "window_duration_seconds": config.window_duration,
        "time_step_seconds": config.time_step,
        "mass_flow_rate": config.mass_flow_rate,
        "max_active_jets_observed": max((sum(row) for row in table.switches), default=0),
        "max_total_mass_flow_observed": max(total_massflows, default=0.0),
        "activation_counts": {
            config.jet_names[idx]: count for idx, count in enumerate(active_counts)
        },
        "outputs": {
            "schedule": "actuation_schedule.csv",
            "heatmap": "actuation_heatmap.svg",
            "total_mass_flow": "total_mass_flow.csv",
            "total_mass_flow_curve": "total_mass_flow_curve.svg",
            "validation_report": "validation_report.json",
        },
        "validation": {
            "passed": not validation_errors,
            "errors": validation_errors,
        },
        "notes": {
            "time_columns": "physical_time, t_start, and t_end are seconds, not solver iterations",
            "switch_columns": "JET_01..JET_24 are 0/1 valve states",
            "massflow_columns": "cmd_massflow_01..cmd_massflow_24 are commanded mass flow values",
        },
    }
    if extra:
        summary["extra"] = extra
    with (config.output_dir / "config_summary.yaml").open("w", encoding="utf-8") as handle:
        yaml.safe_dump(summary, handle, sort_keys=False, allow_unicode=True)
    with (config.output_dir / "validation_report.json").open("w", encoding="utf-8") as handle:
        json.dump(summary["validation"], handle, indent=2, ensure_ascii=False)


def write_total_mass_flow_csv(config: ActuationConfig, table: ScheduleTable) -> None:
    """写入总质量流量 CSV（每窗口的活跃喷口数和总质量流量）。"""
    with (config.output_dir / "total_mass_flow.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["physical_time", "window_id", "t_start", "t_end", "active_jets", "total_mass_flow"])
        for window_id, (switch_row, massflow_row) in enumerate(zip(table.switches, table.massflows)):
            start = round(window_id * config.window_duration, 12)
            end = round(start + config.window_duration, 12)
            writer.writerow(
                [
                    start,
                    window_id,
                    start,
                    end,
                    sum(switch_row),
                    sum(massflow_row),
                ]
            )


def write_heatmap_svg(config: ActuationConfig, table: ScheduleTable) -> None:
    """生成激励计划的热图 SVG（横轴=时间窗口，纵轴=喷口，蓝色=激活）。"""
    cell = 10
    label_width = 64
    label_height = 28
    title = f"{config.mode} switch heatmap"
    width = max(label_width + table.n_windows * cell + 16, 18 + len(title) * 8)
    height = label_height + table.n_jets * cell + 30
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="8" y="18" font-family="Arial" font-size="12" fill="#1f2328">{title}</text>',
    ]
    for jet_idx, jet_name in enumerate(config.jet_names):
        y = label_height + jet_idx * cell
        lines.append(f'<text x="6" y="{y + 8}" font-family="Arial" font-size="8" fill="#333">{jet_name}</text>')
        for window_idx in range(table.n_windows):
            x = label_width + window_idx * cell
            value = table.switches[window_idx][jet_idx]
            fill = "#1f77b4" if value else "#f1f3f5"
            lines.append(f'<rect x="{x}" y="{y}" width="{cell - 1}" height="{cell - 1}" fill="{fill}"/>')
    lines.append("</svg>")
    (config.output_dir / "actuation_heatmap.svg").write_text("\n".join(lines), encoding="utf-8")


def write_total_mass_flow_svg(config: ActuationConfig, table: ScheduleTable) -> None:
    """生成总质量流量曲线 SVG。"""
    points = [
        (window_id, sum(row))
        for window_id, row in enumerate(table.massflows)
    ]
    _write_line_svg(
        config.output_dir / "total_mass_flow_curve.svg",
        title=f"{config.mode} total commanded mass flow",
        points=points,
        y_min=0.0,
    )


def _write_line_svg(path: Path, title: str, points: list[tuple[float, float]], y_min: float = 0.0) -> None:
    """通用的单线图 SVG 写入函数。"""
    width = 860
    height = 300
    left = 58
    top = 34
    plot_w = 776
    plot_h = 212
    x_values = [point[0] for point in points] or [0.0]
    y_values = [point[1] for point in points] or [0.0]
    x0 = min(x_values)
    x1 = max(x_values) if max(x_values) != x0 else x0 + 1.0
    y0 = min(y_min, min(y_values))
    y1 = max(y_values) if max(y_values) != y0 else y0 + 1.0
    pad = (y1 - y0) * 0.08
    y1 += pad

    def sx(value: float) -> float:
        return left + (value - x0) * plot_w / (x1 - x0)

    def sy(value: float) -> float:
        return top + (y1 - value) * plot_h / (y1 - y0)

    polyline = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in points)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="12" y="20" font-family="Arial" font-size="14" fill="#1f2328">{title}</text>',
        f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>',
        f'<polyline fill="none" stroke="#1864ab" stroke-width="2" points="{polyline}"/>',
        "</svg>",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def validate_table(config: ActuationConfig, table: ScheduleTable) -> list[str]:
    """验证生成的激励计划表是否符合约束。

    检查项：
      - 窗口数和喷口数与配置一致
      - switches 和 massflows 行数一致
      - 每窗口活跃喷口不超过 max_active_jets
      - 总质量流量不超过 max_total_mass_flow
      - 开关值只能是 0 或 1
      - JET=0 时 massflow=0，JET=1 时 massflow>0

    Returns:
        错误字符串列表，空列表表示通过验证。
    """
    errors: list[str] = []
    if table.n_windows <= 0:
        errors.append("schedule must contain at least one window")
    if table.n_jets != config.n_jets:
        errors.append(f"expected {config.n_jets} jet columns, got {table.n_jets}")
    if len(table.massflows) != len(table.switches):
        errors.append("switch and mass-flow rows must have the same length")
    for window_id, (switch_row, massflow_row) in enumerate(zip(table.switches, table.massflows)):
        if len(switch_row) != config.n_jets or len(massflow_row) != config.n_jets:
            errors.append(f"window {window_id} must contain {config.n_jets} switch and mass-flow values")
            continue
        active = sum(int(value) for value in switch_row)
        total_mass_flow = sum(float(value) for value in massflow_row)
        if config.max_active_jets is not None and active > config.max_active_jets:
            errors.append(f"window {window_id} has {active} active jets, max is {config.max_active_jets}")
        if config.max_total_mass_flow is not None and total_mass_flow > config.max_total_mass_flow + 1e-12:
            errors.append(
                f"window {window_id} total mass flow {total_mass_flow} exceeds {config.max_total_mass_flow}"
            )
        for idx, (switch_value, massflow_value) in enumerate(zip(switch_row, massflow_row), start=1):
            if switch_value not in (0, 1):
                errors.append(f"window {window_id} JET_{idx:02d} must be 0 or 1")
            if massflow_value < 0.0 or math.isnan(massflow_value):
                errors.append(f"window {window_id} cmd_massflow_{idx:02d} must be non-negative")
            if switch_value == 0 and abs(massflow_value) > 1e-12:
                errors.append(f"window {window_id} JET_{idx:02d}=0 requires cmd_massflow_{idx:02d}=0")
            if switch_value == 1 and massflow_value <= 0.0:
                errors.append(f"window {window_id} JET_{idx:02d}=1 requires cmd_massflow_{idx:02d}>0")
    return errors


def write_pattern_outputs(
    config: ActuationConfig,
    table: ScheduleTable,
    *,
    validation_errors: list[str] | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """完成输出：写入 CSV、SVG 热图、验证报告。

    这是激励模式生成的统一出口。如果验证失败，会抛出 RuntimeError。

    Args:
        config: 激励配置。
        table: 生成的激励计划表。
        validation_errors: 生成器返回的模式特有验证错误。
        extra: 附加元数据（如模式特定的参数）。
    """
    errors = validate_table(config, table)
    if validation_errors:
        errors.extend(validation_errors)
    # 逐一写入所有输出文件
    write_schedule_csv(config, table)
    write_total_mass_flow_csv(config, table)
    write_heatmap_svg(config, table)
    write_total_mass_flow_svg(config, table)
    write_config_summary(config, table, errors, extra=extra)
    if errors:
        raise RuntimeError(f"generated schedule failed validation: {errors}")


def generate_pattern_table(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, Any], list[str]]:
    """根据配置的模式调用对应的生成器函数。

    这是一个工厂函数，根据 config.mode 分发到各模式生成器。

    Returns:
        (ScheduleTable, extra_metadata, validation_errors) 三元组。
    """
    generators: dict[str, Callable[[ActuationConfig], tuple[ScheduleTable, dict[str, Any], list[str]]]] = {}
    # 延迟导入，避免循环依赖
    from .chirp import generate as generate_chirp
    from .prbs import generate as generate_prbs
    from .pulse import generate as generate_pulse
    from .reference import generate as generate_reference
    from .sparse_groups import generate as generate_sparse
    from .step import generate as generate_step

    generators.update(
        {
            "no_jet_reference": generate_reference,
            "pulse_singlejet": generate_pulse,
            "step_singlejet": generate_step,
            "chirp_keyjets": generate_chirp,
            "prbs_demo": generate_prbs,
            "sparse_random_groups": generate_sparse,
        }
    )
    return generators[config.mode](config)
