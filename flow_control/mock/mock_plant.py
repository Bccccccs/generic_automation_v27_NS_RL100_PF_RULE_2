"""由标准喷气流量表驱动的 B4 Mock 动态系统。

这个模块模拟的是 STAR-CCM+ 在喷气控制链路中的“输入输出接口行为”，
不是 CFD 物理替代品。核心链路是：

actuation_schedule.csv
    -> 读取 24 路 JET 开关和 cmd_massflow 质量流量
    -> 计算每个喷口的有效输入 effective_input = JET * cmd_massflow
    -> 通过 6x24 空间影响矩阵映射到 6 个载荷区域的目标响应
    -> 加入上下游传播延迟
    -> 经过一阶惯性系统得到 6 个 Fz 区域输出
    -> 派生总升力、阻力、俯仰/滚转力矩和喷气反作用力
    -> 写成标准 timeseries.csv，供后续质量检查和算法读取
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from flow_control.config import load_config_with_system_defaults
from flow_control.data_schema import CaseSchema
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.sampling import expand_schedule_rows, infer_time_step, infer_window_duration
from flow_control.star_ingest.case_data_loader import write_quality_report
from starccm.control.control_spec import JET_COLUMNS, LOAD_COLUMNS

ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))


@dataclass(frozen=True)
class MockDynamic24x6Config:
    """Mock 动态系统参数。

    这些参数只控制虚拟对象的响应形态，例如空间耦合强弱、上下游延迟、
    惯性时间常数和噪声水平。它们不代表真实物理标定值。
    """

    random_seed: int = 20260703
    # 空间耦合增益：本区域最强，同站异侧/相邻站/远站依次减弱。
    primary_gain: float = 5.0
    same_station_cross_side_gain: float = 0.9
    adjacent_station_gain: float = 0.65
    far_station_gain: float = 0.22
    # 延迟参数：上游喷气传到下游区域需要更久，同站几乎不延迟。
    upstream_delay_per_station: float = 0.08
    same_station_delay: float = 0.0
    downstream_delay: float = 0.02
    # 三个纵向站位的惯性时间常数；越大表示响应越慢。
    tau_s1: float = 0.10
    tau_s2: float = 0.14
    tau_s3: float = 0.18
    # 输出噪声；固定 random_seed 后结果可复现。
    fz_noise_std: float = 0.035
    drag_noise_std: float = 0.01
    moment_noise_std: float = 0.015
    # Response sampling time step. 0.0 falls back to the actuation window length.
    time_step: float = 0.0
    # 派生全局量的简化系数。
    drag_base: float = 0.0
    drag_massflow_gain: float = 0.35
    drag_lift_rms_gain: float = 0.08
    jet_reaction_gain: float = 1.0
    output_clip: float = 1.0e6
    # 24 个喷口按 4 个一组，对应 6 个区域：S1L/S1R/S2L/S2R/S3L/S3R。
    group_size: int = 4

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "MockDynamic24x6Config":
        system = data.get("system", {}) if isinstance(data, dict) else {}
        values = dict(data.get("mock_dynamic24x6", data))
        if "random_seed" not in values and isinstance(system, dict) and "random_seed" in system:
            values["random_seed"] = system["random_seed"]
        allowed = set(cls.__dataclass_fields__)
        kwargs = {key: values[key] for key in allowed if key in values}
        return cls(**kwargs)

    @property
    def tau_by_region(self) -> np.ndarray:
        return np.asarray(
            [self.tau_s1, self.tau_s1, self.tau_s2, self.tau_s2, self.tau_s3, self.tau_s3],
            dtype=float,
        )


class MockDynamicPlant24x6:
    """可复现的 24 输入、6 区域输出 Mock 动态系统。

    输入是 workflow 写出的 actuation_schedule.csv 行数据；输出是标准
    timeseries.csv 需要的 6 个区域载荷和全局派生量。
    """

    def __init__(self, config: MockDynamic24x6Config | None = None) -> None:
        self.config = config or MockDynamic24x6Config()
        self.rng = np.random.default_rng(self.config.random_seed)
        self.gain_matrix = self._build_gain_matrix()

    def simulate(self, schedule_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """把流量表逐行推进为 mock 响应。

        输入到输出的计算顺序：

        1. 从每一行读取 JET_01..JET_24，得到 24 路开关矩阵 switches。
        2. 从每一行读取 cmd_massflow_01..24，得到 24 路质量流量矩阵 massflows。
        3. 计算有效喷气输入 effective_input = switches * massflows。
           这样 JET=0 时即使质量流量误填也不会产生喷气作用。
        4. 将 effective_input 送入 _build_delayed_targets()：
           每个喷口先按空间影响矩阵分配到 6 个区域，再按上下游关系取历史时刻，
           得到“如果系统无惯性时”的目标载荷 targets。
        5. 用一阶惯性 state = state + alpha * (target - state) 平滑目标载荷，
           得到模拟的动态响应。
        6. 给 6 个 Fz 区域输出加少量固定种子的噪声。
        7. 根据 6 个区域输出和总质量流量派生 Fz_Total、Drag、Moment 等全局量。
        """
        if not schedule_rows:
            raise ValueError("actuation_schedule.csv must contain at least one row")

        sample_rows = expand_schedule_rows(schedule_rows, time_step=self.config.time_step)
        physical_time = np.asarray([float(row["physical_time"]) for row in sample_rows])
        window_id = np.asarray([int(float(row["window_id"])) for row in sample_rows])
        dt_values = _infer_dt_values(sample_rows, physical_time)

        # switches 是阀门是否打开；massflows 是每个喷口的指令质量流量。
        # 二者相乘后才是实际参与 mock 计算的 24 路有效喷气输入。
        switches = _rows_to_matrix(sample_rows, JET_COLUMNS)
        massflows = _rows_to_matrix(sample_rows, MASSFLOW_COLUMNS, fallback=switches)
        effective_input = switches * massflows

        # targets 是“空间耦合 + 延迟”之后的目标载荷，尚未经过惯性平滑。
        targets = self._build_delayed_targets(effective_input, dt_values)
        outputs = np.zeros((len(sample_rows), len(LOAD_COLUMNS)), dtype=float)
        state = np.zeros(len(LOAD_COLUMNS), dtype=float)

        for row_idx, target in enumerate(targets):
            dt = max(float(dt_values[row_idx]), 1.0e-12)
            # 一阶惯性响应：tau 越大 alpha 越小，输出越慢接近目标值。
            alpha = dt / (self.config.tau_by_region + dt)
            state = state + alpha * (target - state)
            state = np.clip(state, -self.config.output_clip, self.config.output_clip)
            noise = self.rng.normal(0.0, self.config.fz_noise_std, len(LOAD_COLUMNS))
            outputs[row_idx] = state + noise

        totals = self._derived_outputs(outputs, effective_input)
        rows = self._timeseries_rows(
            sample_rows,
            physical_time,
            window_id,
            switches,
            massflows,
            effective_input,
            outputs,
            totals,
        )
        return {
            "timeseries": rows,
            "schedule": schedule_rows,
            "sample_rows": sample_rows,
            "physical_time": physical_time,
            "inputs": effective_input,
            "outputs": outputs,
            "totals": totals,
            "spatial_nonuniformity": spatial_nonuniformity(outputs),
        }

    def _build_gain_matrix(self) -> np.ndarray:
        """构建 6x24 空间影响矩阵。

        行对应 6 个载荷输出区域：
        Fz_S1L, Fz_S1R, Fz_S2L, Fz_S2R, Fz_S3L, Fz_S3R。

        列对应 24 个喷口：
        JET_01..JET_24。

        映射规则：
        - 每 4 个喷口归属一个主区域，例如 JET_01-04 主影响 S1L。
        - 主区域增益最大。
        - 同一站位另一侧有较弱影响。
        - 相邻站位有更弱影响。
        - 远站位影响最弱。
        - 上游喷气对下游区域的影响稍微放大，用来让下游响应更明显。
        """
        cfg = self.config
        matrix = np.zeros((len(LOAD_COLUMNS), len(JET_COLUMNS)), dtype=float)
        for jet_idx in range(len(JET_COLUMNS)):
            # source_region 是喷口所属的 6 区域编号。
            # group_size=4 时：0..3 -> S1L, 4..7 -> S1R, ...。
            source_region = min(jet_idx // cfg.group_size, len(LOAD_COLUMNS) - 1)
            source_station = source_region // 2
            source_side = source_region % 2
            for target_region in range(len(LOAD_COLUMNS)):
                target_station = target_region // 2
                target_side = target_region % 2
                station_distance = abs(target_station - source_station)
                if target_region == source_region:
                    gain = cfg.primary_gain
                elif target_station == source_station and target_side != source_side:
                    gain = cfg.same_station_cross_side_gain
                elif station_distance == 1:
                    gain = cfg.adjacent_station_gain
                else:
                    gain = cfg.far_station_gain
                if target_station > source_station:
                    gain *= 1.25
                matrix[target_region, jet_idx] = gain
        return matrix

    def _build_delayed_targets(self, effective_input: np.ndarray, dt_values: np.ndarray) -> np.ndarray:
        """把 24 路有效喷气输入转换成 6 路目标载荷。

        这里完成两件事：

        1. 空间映射：
           gain_matrix[target_region, jet_idx] * effective_input[:, jet_idx]
           表示某个喷口对某个载荷区域的影响强度。

        2. 上下游延迟：
           如果喷口属于更上游站位，而目标区域在下游，则不使用当前时刻输入，
           而使用若干步之前的输入。这样上游喷气对下游载荷会晚一点出现。

        输出 targets[t, region] 表示第 t 个时间窗口、region 区域在无惯性情况下
        应该达到的目标载荷。
        """
        cfg = self.config
        row_count = effective_input.shape[0]
        targets = np.zeros((row_count, len(LOAD_COLUMNS)), dtype=float)
        nominal_dt = float(np.median(dt_values)) if len(dt_values) else 1.0
        nominal_dt = max(nominal_dt, 1.0e-12)

        for row_idx in range(row_count):
            for jet_idx in range(len(JET_COLUMNS)):
                source_region = min(jet_idx // cfg.group_size, len(LOAD_COLUMNS) - 1)
                source_station = source_region // 2
                for target_region in range(len(LOAD_COLUMNS)):
                    target_station = target_region // 2
                    station_delta = target_station - source_station
                    if station_delta > 0:
                        delay_seconds = cfg.upstream_delay_per_station * station_delta
                    elif station_delta == 0:
                        delay_seconds = cfg.same_station_delay
                    else:
                        delay_seconds = cfg.downstream_delay
                    lag = int(round(delay_seconds / nominal_dt))
                    source_idx = row_idx - max(lag, 0)
                    if source_idx >= 0:
                        # 当前目标载荷 = 历史有效喷气输入 * 空间影响增益。
                        targets[row_idx, target_region] += (
                            self.gain_matrix[target_region, jet_idx]
                            * effective_input[source_idx, jet_idx]
                        )
        return targets

    def _derived_outputs(self, outputs: np.ndarray, effective_input: np.ndarray) -> dict[str, np.ndarray]:
        """由 6 个区域载荷和 24 路输入派生全局输出。

        - Fz_Total：6 个区域 Fz 直接求和。
        - Drag_Total：用总质量流量和载荷 RMS 构造一个简化阻力指标。
        - Pitch_Moment：前后区域载荷乘以纵向力臂。
        - Roll_Moment：左右区域载荷乘以横向力臂。
        - Jet_Reaction_Z：总质量流量乘以喷气反作用系数。
        """
        cfg = self.config
        total_massflow = np.sum(effective_input, axis=1)
        fz_total = np.sum(outputs, axis=1)
        lift_rms = np.sqrt(np.mean(outputs * outputs, axis=1))
        drag = (
            cfg.drag_base
            + cfg.drag_massflow_gain * total_massflow
            + cfg.drag_lift_rms_gain * lift_rms
            + self.rng.normal(0.0, cfg.drag_noise_std, outputs.shape[0])
        )
        pitch_arms = np.asarray([-1.0, -1.0, 0.0, 0.0, 1.0, 1.0], dtype=float)
        roll_arms = np.asarray([-0.5, 0.5, -0.5, 0.5, -0.5, 0.5], dtype=float)
        pitch = outputs @ pitch_arms + self.rng.normal(0.0, cfg.moment_noise_std, outputs.shape[0])
        roll = outputs @ roll_arms + self.rng.normal(0.0, cfg.moment_noise_std, outputs.shape[0])
        reaction = cfg.jet_reaction_gain * total_massflow
        return {
            "Fz_Total": fz_total,
            "Drag_Total": drag,
            "Pitch_Moment": pitch,
            "Roll_Moment": roll,
            "Jet_Reaction_Z": reaction,
            "total_massflow": total_massflow,
        }

    def _timeseries_rows(
        self,
        sample_rows: list[dict[str, Any]],
        physical_time: np.ndarray,
        window_id: np.ndarray,
        switches: np.ndarray,
        massflows: np.ndarray,
        actual_massflows: np.ndarray,
        outputs: np.ndarray,
        totals: dict[str, np.ndarray],
    ) -> list[dict[str, Any]]:
        """把 mock 数组结果整理成 CaseSchema 标准 timeseries 行。

        注意：timeseries.csv 中保留 JET_01..JET_24 开关列，质量流量列仍然
        保存在 actuation_schedule.csv 中。这样输出格式和 STAR-CCM+ 结果表保持一致。
        """
        rows: list[dict[str, Any]] = []
        for row_idx in range(len(physical_time)):
            record: dict[str, Any] = {
                "physical_time": float(physical_time[row_idx]),
                "window_id": int(window_id[row_idx]),
            }
            for jet_idx, column in enumerate(JET_COLUMNS):
                record[column] = float(switches[row_idx, jet_idx])
            for jet_idx, column in enumerate(MASSFLOW_COLUMNS):
                record[column] = float(massflows[row_idx, jet_idx])
            for jet_idx, column in enumerate(ACTUAL_MASSFLOW_COLUMNS):
                record[column] = float(actual_massflows[row_idx, jet_idx])
            for output_idx, column in enumerate(LOAD_COLUMNS):
                record[column] = float(outputs[row_idx, output_idx])
            for column in (
                "Fz_Total",
                "Drag_Total",
                "Pitch_Moment",
                "Roll_Moment",
                "Jet_Reaction_Z",
            ):
                record[column] = float(totals[column][row_idx])
            record["solver_status"] = "success"
            record["case_stage"] = "mock_dynamic24x6"
            rows.append(record)
        return rows


def load_config(path: str | Path) -> dict[str, Any]:
    """读取 mock 参数 YAML。"""
    return load_config_with_system_defaults(path)


def read_actuation_schedule(path: str | Path) -> list[dict[str, Any]]:
    """读取 workflow 生成的标准 actuation_schedule.csv。

    这里仅检查 mock 必需的基础列：时间、窗口号和 JET_01..JET_24。
    cmd_massflow_01..24 如果存在，会在 simulate() 中作为质量流量使用；
    如果不存在，则退化为使用 JET 开关值作为输入幅值。
    """
    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"physical_time", "window_id", *JET_COLUMNS}
    missing = sorted(required - set(rows[0].keys())) if rows else sorted(required)
    if missing:
        raise ValueError(f"actuation_schedule.csv missing required columns: {', '.join(missing)}")
    return rows


def write_mock_dynamic_case(
    *,
    schedule_path: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    time_step: float | None = None,
) -> dict[str, Any]:
    """运行一次完整 mock case，并写出标准目录。

    这个函数是 mock 模块对 workflow 的主要入口：

    - 输入：workflow 生成的 actuation_schedule.csv 和 mock 参数 YAML。
    - 内部：调用 MockDynamicPlant24x6.simulate() 得到数组结果。
    - 输出：通过 CaseSchema.write_case() 写出 timeseries.csv、
      actuation_schedule.csv、case_manifest.yaml、quality_report.json。
    - 附加：写出 demo 图和 mock_dynamic24x6_summary.json。
    """
    raw_config = load_config(config_path)
    config = MockDynamic24x6Config.from_mapping(raw_config)
    if time_step is not None:
        config = replace(config, time_step=float(time_step))
    schedule_rows = read_actuation_schedule(schedule_path)
    result = MockDynamicPlant24x6(config).simulate(schedule_rows)
    run_dir = Path(output_dir)
    case_id = run_dir.name

    # manifest/quality_report 让 mock 输出看起来像一个标准 case，
    # 后续 B5 或算法模块不需要知道它来自真实 STAR 还是 mock。
    manifest = {
        "geometry_version": "mock-dynamic24x6",
        "mesh_version": "not-applicable",
        "flow_velocity": float(raw_config.get("flow_velocity", 0.0)),
        "gap": float(raw_config.get("gap", 0.0)),
        "time_step": _manifest_time_step(result["sample_rows"]),
        "jet_amplitude": _max_total_massflow(schedule_rows),
        "window_duration": _manifest_window_duration(schedule_rows),
        "random_seed": config.random_seed,
        "case_stage": "mock_dynamic24x6",
        "check_mode": "mock",
    }
    quality_report = {
        "run_success_flag": True,
        "case_stage": "mock_dynamic24x6",
        "source_schedule": str(schedule_path),
        "mock_config": str(config_path),
        "spatial_nonuniformity_max": float(np.max(result["spatial_nonuniformity"])),
        "total_massflow_max": float(np.max(result["totals"]["total_massflow"])),
    }

    old_root = CaseSchema.runs_root
    CaseSchema.runs_root = run_dir.parent
    try:
        schema_result = CaseSchema.write_case(
            {
                "case_id": case_id,
                "manifest": manifest,
                "timeseries": result["timeseries"],
                "actuation_schedule": result["schedule"],
                "quality_report": quality_report,
            }
        )
    finally:
        CaseSchema.runs_root = old_root

    write_plots(schema_result["run_dir"], result)
    (schema_result["run_dir"] / "config_used.yaml").write_text(
        yaml.safe_dump(raw_config, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    schema_result["quality_report"] = write_quality_report(
        schema_result["run_dir"],
        check_mode="mock",
    )
    _write_demo_summary(schema_result["run_dir"], schema_result, schema_result["quality_report"])
    return schema_result


def spatial_nonuniformity(outputs: np.ndarray) -> np.ndarray:
    """计算 6 个区域载荷的不均匀度：std / abs(mean)。"""
    mean_abs = np.abs(np.mean(outputs, axis=1))
    return np.std(outputs, axis=1) / (mean_abs + 1.0e-12)


def write_plots(run_dir: Path, result: dict[str, Any]) -> None:
    """写出 B4 验收要求的图。

    图的输入均来自 simulate() 返回的中间结果：
    - inputs：24 路有效喷气输入热图。
    - outputs：6 个区域 Fz 时程。
    - totals["Fz_Total"]：总升力时程。
    - spatial_nonuniformity：空间不均匀度。
    - totals["total_massflow"]：总质量流量。
    """
    figures = run_dir / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    time_values = result["physical_time"]
    write_input_heatmap_svg(figures / "input_heatmap.svg", result["inputs"])
    write_multi_series_svg(
        figures / "fz_regions.svg",
        time_values,
        result["outputs"],
        list(LOAD_COLUMNS),
        "6 regional Fz time series",
    )
    write_single_series_svg(
        figures / "fz_total.svg",
        time_values,
        result["totals"]["Fz_Total"],
        "Fz_Total time series",
    )
    write_single_series_svg(
        figures / "spatial_nonuniformity.svg",
        time_values,
        result["spatial_nonuniformity"],
        "Spatial nonuniformity",
    )
    write_single_series_svg(
        figures / "total_massflow.svg",
        time_values,
        result["totals"]["total_massflow"],
        "Total mass flow",
    )


def write_input_heatmap_svg(path: Path, inputs: np.ndarray) -> None:
    """把 T x 24 的有效喷气输入画成热图。"""
    cell_w = 10
    cell_h = 10
    left = 64
    top = 26
    title = "Jet input heatmap"
    width = max(left + inputs.shape[0] * cell_w + 18, 240)
    height = top + inputs.shape[1] * cell_h + 28
    max_value = float(np.max(inputs)) if inputs.size and np.max(inputs) > 0 else 1.0
    lines = _svg_header(width, height, title)
    for jet_idx in range(inputs.shape[1]):
        y = top + jet_idx * cell_h
        lines.append(_svg_text(6, y + 8, f"JET_{jet_idx + 1:02d}", 8, "#2b2f33"))
        for time_idx in range(inputs.shape[0]):
            x = left + time_idx * cell_w
            amount = max(0.0, min(1.0, float(inputs[time_idx, jet_idx]) / max_value))
            lines.append(
                f'<rect x="{x}" y="{y}" width="{cell_w - 1}" height="{cell_h - 1}" '
                f'fill="{_blend("#f4f7fb", "#1864ab", amount)}"/>'
            )
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_multi_series_svg(
    path: Path,
    time_values: np.ndarray,
    series: np.ndarray,
    labels: list[str],
    title: str,
) -> None:
    """写多条曲线的 SVG，例如 6 个区域 Fz。"""
    colors = ["#0b7285", "#e67700", "#2b8a3e", "#c92a2a", "#5f3dc4", "#087f5b"]
    lines = _series_svg_base(time_values, series, title)
    left, top, plot_w, plot_h, min_y, max_y = _plot_bounds(series)
    for idx, label in enumerate(labels):
        points = _polyline_points(time_values, series[:, idx], left, top, plot_w, plot_h, min_y, max_y)
        color = colors[idx % len(colors)]
        lines.append(f'<polyline fill="none" stroke="{color}" stroke-width="1.8" points="{" ".join(points)}"/>')
        lines.append(_svg_text(left + 8 + idx * 74, 348, label, 10, color))
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def write_single_series_svg(path: Path, time_values: np.ndarray, values: np.ndarray, title: str) -> None:
    """写单条曲线的 SVG，例如总升力或总质量流量。"""
    series = np.asarray(values, dtype=float).reshape(-1, 1)
    lines = _series_svg_base(time_values, series, title)
    left, top, plot_w, plot_h, min_y, max_y = _plot_bounds(series)
    points = _polyline_points(time_values, values, left, top, plot_w, plot_h, min_y, max_y)
    lines.append(f'<polyline fill="none" stroke="#0b7285" stroke-width="2.0" points="{" ".join(points)}"/>')
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _series_svg_base(time_values: np.ndarray, series: np.ndarray, title: str) -> list[str]:
    width = 920
    height = 360
    left, top, plot_w, plot_h, min_y, max_y = _plot_bounds(series)
    lines = _svg_header(width, height, title)
    lines.append(f'<rect x="{left}" y="{top}" width="{plot_w}" height="{plot_h}" fill="#fbfcfe" stroke="#d0d7de"/>')
    for idx in range(5):
        y = top + idx * plot_h / 4
        lines.append(f'<line x1="{left}" y1="{y:.2f}" x2="{left + plot_w}" y2="{y:.2f}" stroke="#edf2f7"/>')
    lines.append(_svg_text(8, top + 12, f"{max_y:.3g}", 9, "#57606a"))
    lines.append(_svg_text(8, top + plot_h, f"{min_y:.3g}", 9, "#57606a"))
    if len(time_values):
        lines.append(_svg_text(left, 338, f"{float(time_values[0]):.3g}s", 9, "#57606a"))
        lines.append(_svg_text(left + plot_w - 40, 338, f"{float(time_values[-1]):.3g}s", 9, "#57606a"))
    return lines


def _plot_bounds(series: np.ndarray) -> tuple[int, int, int, int, float, float]:
    width = 920
    height = 360
    left = 54
    top = 28
    plot_w = width - left - 24
    plot_h = height - top - 52
    min_y = float(np.min(series)) if series.size else 0.0
    max_y = float(np.max(series)) if series.size else 1.0
    if math.isclose(min_y, max_y):
        min_y -= 0.5
        max_y += 0.5
    pad = max((max_y - min_y) * 0.12, 0.1)
    return left, top, plot_w, plot_h, min_y - pad, max_y + pad


def _polyline_points(
    time_values: np.ndarray,
    values: np.ndarray,
    left: int,
    top: int,
    plot_w: int,
    plot_h: int,
    min_y: float,
    max_y: float,
) -> list[str]:
    if len(values) == 0:
        return []
    t_min = float(time_values[0]) if len(time_values) else 0.0
    t_max = float(time_values[-1]) if len(time_values) else float(len(values) - 1)
    if math.isclose(t_min, t_max):
        t_max = t_min + 1.0
    points = []
    for idx, value in enumerate(values):
        t = float(time_values[idx]) if idx < len(time_values) else float(idx)
        x = left + (t - t_min) * plot_w / (t_max - t_min)
        y = top + (max_y - float(value)) * plot_h / (max_y - min_y)
        points.append(f"{x:.2f},{y:.2f}")
    return points


def _rows_to_matrix(
    rows: list[dict[str, Any]],
    columns: tuple[str, ...],
    *,
    fallback: np.ndarray | None = None,
) -> np.ndarray:
    """把 CSV 行数据中的一组列转换成二维矩阵。

    rows 是时间方向，columns 是通道方向。用于把 JET_* 和 cmd_massflow_*
    从字典列表转换成数值矩阵，方便后续向量化计算。
    """
    matrix = np.zeros((len(rows), len(columns)), dtype=float)
    for row_idx, row in enumerate(rows):
        for col_idx, column in enumerate(columns):
            if column in row and row[column] not in {None, ""}:
                matrix[row_idx, col_idx] = float(row[column])
            elif fallback is not None:
                matrix[row_idx, col_idx] = fallback[row_idx, col_idx]
    return matrix


def _infer_dt_values(rows: list[dict[str, Any]], physical_time: np.ndarray) -> np.ndarray:
    """从 t_start/t_end 或 physical_time 推断每个窗口的时间步长。"""
    values = np.zeros(len(rows), dtype=float)
    for idx, row in enumerate(rows):
        if "t_start" in row and "t_end" in row:
            values[idx] = float(row["t_end"]) - float(row["t_start"])
        elif idx + 1 < len(physical_time):
            values[idx] = physical_time[idx + 1] - physical_time[idx]
        elif idx > 0:
            values[idx] = physical_time[idx] - physical_time[idx - 1]
        else:
            values[idx] = 1.0
    return np.maximum(values, 1.0e-12)


def _manifest_time_step(rows: list[dict[str, Any]]) -> float:
    if not rows:
        return 0.0
    inferred = infer_time_step(rows)
    if inferred > 0.0:
        return inferred
    if "t_start" in rows[0] and "t_end" in rows[0]:
        return float(rows[0]["t_end"]) - float(rows[0]["t_start"])
    return 1.0


def _manifest_window_duration(rows: list[dict[str, Any]]) -> float:
    return infer_window_duration(rows)


def _max_total_massflow(rows: list[dict[str, Any]]) -> float:
    max_value = 0.0
    for row in rows:
        total = sum(float(row.get(column, 0.0) or 0.0) for column in MASSFLOW_COLUMNS)
        max_value = max(max_value, total)
    return max_value


def _write_demo_summary(run_dir: Path, schema_result: dict[str, Any], quality_report: dict[str, Any]) -> None:
    summary = {
        "case_id": schema_result["case_id"],
        "run_dir": str(schema_result["run_dir"]),
        "outputs": {
            "timeseries": "timeseries.csv",
            "actuation_schedule": "actuation_schedule.csv",
            "case_manifest": "case_manifest.yaml",
            "quality_report": "quality_report.json",
            "input_heatmap": "figures/input_heatmap.svg",
            "fz_regions": "figures/fz_regions.svg",
            "fz_total": "figures/fz_total.svg",
            "spatial_nonuniformity": "figures/spatial_nonuniformity.svg",
            "total_massflow": "figures/total_massflow.svg",
        },
        "quality_report": quality_report,
    }
    with (run_dir / "mock_dynamic24x6_summary.json").open("w", encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2, ensure_ascii=False)


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        _svg_text(8, 16, title, 13, "#1f2328"),
    ]


def _svg_text(x: float, y: float, text: str, size: int, fill: str) -> str:
    return f'<text x="{x}" y="{y}" font-family="Arial, sans-serif" font-size="{size}" fill="{fill}">{text}</text>'


def _blend(left_hex: str, right_hex: str, amount: float) -> str:
    amount = min(max(amount, 0.0), 1.0)
    left = np.array([int(left_hex[idx : idx + 2], 16) for idx in (1, 3, 5)])
    right = np.array([int(right_hex[idx : idx + 2], 16) for idx in (1, 3, 5)])
    rgb = np.round(left * (1.0 - amount) + right * amount).astype(int)
    return f"#{rgb[0]:02x}{rgb[1]:02x}{rgb[2]:02x}"
