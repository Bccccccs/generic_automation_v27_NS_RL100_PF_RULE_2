"""数据结构和 Case IO Schema —— 稀疏喷气流动控制实验的标准数据契约。

定义了完整的 case 存储格式和验证规则，是整个工作流的数据层核心。

Case 目录结构（runs/<case_id>/）：
  case_manifest.yaml       — case 元数据（几何、网格、流动参数）
  timeseries.csv           — 传感器时序数据（6 区域 Fz + 全局量 + 喷气状态）
  actuation_schedule.csv   — 喷气激励指令（每窗口的开关和幅值）
  pressure_sensors.csv     — 压力传感器数据（可选）
  quality_report.json      — 预计算的质量评估报告
  input/                   — 后端输入的激励计划（副本）
  figures/                 — 自动生成的诊断图
  logs/                    — 求解器/运行时日志
  flow_snapshots/          — 流场快照（可选）

核心类：
  CaseSchema     — case 存储/加载/验证的严格契约
  ControlAction  — 单个喷气控制指令（不可变）
  ScheduleStep   — 单个步的控制命令集合
  Schedule       — 完整实验计划
  PlantObservation — plant 观测值
  ExperimentConfig — 顶层实验配置
"""

from __future__ import annotations

import csv
import json
import logging
import math
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from starccm.control.control_spec import (
    GLOBAL_OUTPUT_COLUMNS,
    JET_COLUMNS,
    LOAD_COLUMNS,
)

# 标准列名定义
GLOBAL_COLUMNS = GLOBAL_OUTPUT_COLUMNS
# timeseries.csv 必选列：时间、窗口号、24 路 JET 开关、6 区域载荷、5 全局量
TIMESERIES_REQUIRED_COLUMNS = (
    "physical_time",
    "window_id",
    *JET_COLUMNS,
    *LOAD_COLUMNS,
    *GLOBAL_COLUMNS,
)
# pressure_sensors.csv 必选列（可选文件）
PRESSURE_SENSOR_REQUIRED_COLUMNS = (
    "physical_time",
    "window_id",
    "sensor_id",
    "pressure",
)
# case_manifest.yaml 必选字段
MANIFEST_REQUIRED_FIELDS = (
    "geometry_version",  # 几何版本
    "mesh_version",      # 网格版本
    "flow_velocity",     # 来流速度 (m/s)
    "gap",               # 间隙 (m)
    "time_step",         # 求解器时间步长 (s)
    "jet_amplitude",     # 喷气幅值 (kg/s)
    "window_duration",   # 控制窗口持续时间 (s)
    "random_seed",       # 随机种子
    "git_commit",        # Git commit hash
    "created_time",      # 创建时间 (ISO 8601)
)
# 每个 case 目录下必须包含的子目录
CASE_DIRECTORIES = ("input", "figures", "logs", "flow_snapshots")


def _is_nan_like(value: Any) -> bool:
    """判断值是否为 NaN 类（None, 空字符串, "nan", math.nan）。"""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().lower() == "nan"
    try:
        return bool(math.isnan(value))
    except (TypeError, ValueError):
        return False


def _normalize_tabular(data: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """将多种表格数据格式统一标准化为 (列名列表, 行字典列表)。

    支持的输入格式（按优先级）：
      1. DataFrame-like 对象（有 to_dict 和 columns 属性，如 pandas DataFrame）
      2. dict of lists（如 {"col1": [1,2], "col2": [3,4]}）
      3. 可迭代对象（如 [{"col1": 1}, {"col1": 2}]）

    Args:
        data: 原始表格数据。

    Returns:
        (columns, rows) 元组。
    """
    # 格式 1：DataFrame-like（pandas DataFrame 等）
    if hasattr(data, "to_dict") and hasattr(data, "columns"):
        columns = [str(column) for column in data.columns]
        rows = [
            {str(key): value for key, value in row.items()}
            for row in data.to_dict(orient="records")
        ]
        return columns, rows

    # 格式 2：dict of lists（如 {"a": [1,2], "b": [3,4]}）
    if isinstance(data, dict):
        columns = [str(column) for column in data.keys()]
        lengths = {len(values) for values in data.values()}
        if len(lengths) > 1:
            raise ValueError("tabular mapping values must all have the same length")
        row_count = next(iter(lengths), 0)
        rows = [
            {column: data[column][row_idx] for column in columns}
            for row_idx in range(row_count)
        ]
        return columns, rows

    # 格式 3：行字典的可迭代对象（如 CSV DictReader）
    if isinstance(data, Iterable) and not isinstance(data, (str, bytes)):
        rows = [{str(key): value for key, value in row.items()} for row in data]
        columns: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for column in row:
                if column not in seen:
                    columns.append(column)
                    seen.add(column)
        return columns, rows

    raise TypeError("tabular data must be a DataFrame-like object, mapping, or iterable of mappings")


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    """从 CSV 文件读取所有行，返回字典列表。"""
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv_rows(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    """将行数据写入 CSV 文件（自动创建父目录）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _current_git_commit() -> str:
    """获取当前 Git HEAD 的 commit hash，用于 case 溯源。"""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _utc_now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 格式字符串。"""
    return datetime.now(timezone.utc).isoformat()


class CaseSchema:
    """Case 存储的严格契约 —— 被 CFD、Mock Plant 和 RL 运行共享。

    这个类定义了所有 case 共享的目录结构、文件格式和验证规则。
    任何数据来源（真实仿真、mock 模拟、算法输出）都必须遵循此契约。
    """

    # runs 根目录，可在运行时被覆写（如 write_mock_dynamic_case 中的临时替换）
    runs_root = Path("runs")
    timeseries_required_columns = TIMESERIES_REQUIRED_COLUMNS
    pressure_sensor_required_columns = PRESSURE_SENSOR_REQUIRED_COLUMNS
    manifest_required_fields = MANIFEST_REQUIRED_FIELDS
    case_directories = CASE_DIRECTORIES

    @classmethod
    def validate_timeseries(cls, df: Any) -> list[str]:
        """验证 timeseries 数据的 Schema 符合性。

        检查内容：
          1. 所有必选列是否存在
          2. 包含 24 路 JET 列
          3. 至少有一行数据
          4. 没有 NaN/None/空值
          5. window_id 连续递增（步长 1）

        Args:
            df: 待验证的表格数据。

        Returns:
            错误字符串列表，空列表表示验证通过。
        """

        errors: list[str] = []
        columns, rows = _normalize_tabular(df)
        column_set = set(columns)

        missing_columns = [
            column for column in cls.timeseries_required_columns if column not in column_set
        ]
        if missing_columns:
            errors.append(f"timeseries.csv missing required columns: {', '.join(missing_columns)}")

        missing_jets = [column for column in JET_COLUMNS if column not in column_set]
        if missing_jets:
            errors.append(f"timeseries.csv missing jet columns: {', '.join(missing_jets)}")

        if not rows:
            errors.append("timeseries.csv must contain at least one row")
            return errors

        for row_idx, row in enumerate(rows):
            for column in columns:
                if _is_nan_like(row.get(column)):
                    errors.append(f"timeseries.csv contains missing/NaN value at row {row_idx}, column {column}")
                    break

        if "window_id" in column_set:
            try:
                window_ids = [int(row["window_id"]) for row in rows]
            except (TypeError, ValueError):
                errors.append("window_id must be integer-like for every row")
            else:
                if any(right < left for left, right in zip(window_ids, window_ids[1:])):
                    errors.append("window_id must be non-decreasing in row order")
                unique_window_ids: list[int] = []
                for window_id in window_ids:
                    if not unique_window_ids or unique_window_ids[-1] != window_id:
                        unique_window_ids.append(window_id)
                expected = list(range(unique_window_ids[0], unique_window_ids[0] + len(unique_window_ids)))
                if unique_window_ids != expected:
                    errors.append(
                        "window_id groups must be consecutive with step 1 in row order "
                        f"(expected {expected[0]}..{expected[-1]}, got {unique_window_ids[0]}..{unique_window_ids[-1]})"
                    )

        return errors

    @classmethod
    def validate_manifest(cls, yaml_dict: dict[str, Any]) -> list[str]:
        """验证 case_manifest.yaml 的字段完整性。

        检查所有 MANIFEST_REQUIRED_FIELDS 都存在且不为 NaN 类值。

        Args:
            yaml_dict: 从 YAML 解析出的 manifest 字典。

        Returns:
            错误列表，空表示通过。
        """
        errors: list[str] = []
        missing_fields = [
            field_name
            for field_name in cls.manifest_required_fields
            if field_name not in yaml_dict or _is_nan_like(yaml_dict[field_name])
        ]
        if missing_fields:
            errors.append(f"case_manifest.yaml missing required fields: {', '.join(missing_fields)}")
        return errors

    @classmethod
    def validate_pressure_sensors(cls, df: Any) -> list[str]:
        """验证可选的 pressure_sensors.csv 数据。

        检查列完整性、NaN 值、window_id 和 pressure 字段是否为数值。

        Args:
            df: 压力传感器表格数据。

        Returns:
            错误列表，空表示通过。
        """

        errors: list[str] = []
        columns, rows = _normalize_tabular(df)
        column_set = set(columns)

        missing_columns = [
            column
            for column in cls.pressure_sensor_required_columns
            if column not in column_set
        ]
        if missing_columns:
            errors.append(f"pressure_sensors.csv missing required columns: {', '.join(missing_columns)}")

        if not rows:
            errors.append("pressure_sensors.csv must contain at least one row when provided")
            return errors

        for row_idx, row in enumerate(rows):
            for column in cls.pressure_sensor_required_columns:
                if _is_nan_like(row.get(column)):
                    errors.append(
                        f"pressure_sensors.csv contains missing/NaN value at row {row_idx}, column {column}"
                    )
                    break
            try:
                int(row.get("window_id"))
            except (TypeError, ValueError):
                errors.append(f"pressure_sensors.csv row {row_idx} has non-integer window_id")
            try:
                float(row.get("physical_time"))
                float(row.get("pressure"))
            except (TypeError, ValueError):
                errors.append(f"pressure_sensors.csv row {row_idx} has non-numeric time or pressure")

        return errors

    @classmethod
    def build_run_directory(cls, case_id: str) -> Path:
        """创建并返回 case 的标准运行目录（含所有子目录）。

        目录结构：runs/<case_id>/ + input/figures/logs/flow_snapshots/

        Args:
            case_id: case 名称（不能包含路径分隔符）。

        Returns:
            创建的目录 Path。
        """
        if not str(case_id).strip():
            raise ValueError("case_id must be non-empty")
        if Path(str(case_id)).name != str(case_id):
            raise ValueError("case_id must be a plain directory name without path separators")

        run_dir = cls.runs_root / str(case_id)
        for directory_name in cls.case_directories:
            (run_dir / directory_name).mkdir(parents=True, exist_ok=True)
        return run_dir

    @classmethod
    def write_case(cls, case_data: dict[str, Any]) -> dict[str, Any]:
        """验证并写入完整的 case 包到 ``runs/<case_id>/`` 目录。

        写入过程：
          1. 创建 case 目录（含子目录）
          2. 设置 manifest 的 git_commit 和 created_time
          3. 标准化 timeseries 数据、校验列顺序
          4. 验证 manifest、timeseries、目录结构
          5. 处理 actuation_schedule（可从 timeseries 自动生成）
          6. 处理可选的 pressure_sensors
          7. 构建 quality_report
          8. 逐一写入所有文件

        Args:
            case_data: case 数据字典，包含 case_id, manifest, timeseries,
                      actuation_schedule（可选）, pressure_sensors（可选）。

        Returns:
            包含 case_id、run_dir、files 路径字典、quality_report 的结果字典。

        Raises:
            ValueError: 数据验证失败时抛出。
        """
        case_id = str(case_data.get("case_id", "")).strip()
        run_dir = cls.build_run_directory(case_id)
        logger = cls._case_logger(run_dir)
        logger.info("starting case write for %s", case_id)

        manifest = dict(case_data.get("manifest") or {})
        manifest.setdefault("git_commit", _current_git_commit())
        manifest.setdefault("created_time", _utc_now_iso())

        timeseries = case_data.get("timeseries")
        if timeseries is None:
            raise ValueError("case_data must include 'timeseries'")
        ts_columns, ts_rows = _normalize_tabular(timeseries)
        ts_columns = cls._ordered_columns(ts_columns, cls.timeseries_required_columns)

        validation_errors = []
        validation_errors.extend(cls.validate_manifest(manifest))
        validation_errors.extend(cls.validate_timeseries(ts_rows))
        validation_errors.extend(cls._validate_directory(run_dir))
        if validation_errors:
            logger.error("case validation failed: %s", validation_errors)
            raise ValueError("; ".join(validation_errors))

        schedule = case_data.get("actuation_schedule")
        if schedule is None:
            # 如果未提供单独的激励计划，从 timeseries 中提取喷气状态
            schedule_columns, schedule_rows = cls._actuation_schedule_from_timeseries(ts_rows)
        else:
            schedule_columns, schedule_rows = _normalize_tabular(schedule)

        pressure_sensors = case_data.get("pressure_sensors")
        pressure_sensor_rows: list[dict[str, Any]] | None = None
        pressure_sensor_columns: list[str] | None = None
        if pressure_sensors is not None:
            pressure_sensor_columns, pressure_sensor_rows = _normalize_tabular(pressure_sensors)
            pressure_sensor_columns = cls._ordered_columns(
                pressure_sensor_columns,
                cls.pressure_sensor_required_columns,
            )
            pressure_errors = cls.validate_pressure_sensors(pressure_sensor_rows)
            if pressure_errors:
                logger.error("pressure sensor validation failed: %s", pressure_errors)
                raise ValueError("; ".join(pressure_errors))

        quality_report = cls._build_quality_report(ts_rows)
        quality_report.update(dict(case_data.get("quality_report") or {}))
        quality_report.setdefault("run_success_flag", len(validation_errors) == 0)

        # --- 写入所有文件 ---
        with (run_dir / "case_manifest.yaml").open("w", encoding="utf-8") as handle:
            yaml.safe_dump(manifest, handle, sort_keys=False, allow_unicode=True)
        _write_csv_rows(run_dir / "timeseries.csv", ts_columns, ts_rows)
        _write_csv_rows(run_dir / "actuation_schedule.csv", schedule_columns, schedule_rows)
        _write_csv_rows(run_dir / "input" / "actuation_schedule.csv", schedule_columns, schedule_rows)
        if pressure_sensor_rows is not None and pressure_sensor_columns is not None:
            _write_csv_rows(run_dir / "pressure_sensors.csv", pressure_sensor_columns, pressure_sensor_rows)
        with (run_dir / "quality_report.json").open("w", encoding="utf-8") as handle:
            json.dump(quality_report, handle, indent=2, ensure_ascii=False)

        logger.info("case write completed for %s", case_id)
        return {
            "case_id": case_id,
            "run_dir": run_dir,
            "files": {
                "manifest": run_dir / "case_manifest.yaml",
                "input_actuation_schedule": run_dir / "input" / "actuation_schedule.csv",
                "actuation_schedule": run_dir / "actuation_schedule.csv",
                "timeseries": run_dir / "timeseries.csv",
                "pressure_sensors": run_dir / "pressure_sensors.csv",
                "flow_snapshots": run_dir / "flow_snapshots",
                "quality_report": run_dir / "quality_report.json",
                "log": run_dir / "logs" / "case_io.log",
            },
            "quality_report": quality_report,
        }

    @classmethod
    def load_case(cls, case_id: str) -> dict[str, Any]:
        """加载完整的 case 包并重新执行严格验证。

        从 runs/<case_id>/ 目录加载 manifest、timeseries、schedule 和
        quality_report 数据，然后重新运行验证以确保数据完整性。

        Args:
            case_id: case 名称。

        Returns:
            包含 case 所有数据的字典。

        Raises:
            FileNotFoundError: case 文件缺失。
            ValueError: 数据验证失败。
        """
        run_dir = cls.runs_root / str(case_id)
        required_paths = {
            "manifest": run_dir / "case_manifest.yaml",
            "actuation_schedule": run_dir / "actuation_schedule.csv",
            "timeseries": run_dir / "timeseries.csv",
            "quality_report": run_dir / "quality_report.json",
        }
        missing = [name for name, path in required_paths.items() if not path.exists()]
        if missing:
            raise FileNotFoundError(f"case {case_id} missing files/directories: {', '.join(missing)}")

        with required_paths["manifest"].open("r", encoding="utf-8") as handle:
            manifest = yaml.safe_load(handle) or {}
        timeseries = _read_csv_rows(required_paths["timeseries"])
        actuation_schedule = _read_csv_rows(required_paths["actuation_schedule"])
        pressure_sensor_path = run_dir / "pressure_sensors.csv"
        pressure_sensors = _read_csv_rows(pressure_sensor_path) if pressure_sensor_path.exists() else []
        with required_paths["quality_report"].open("r", encoding="utf-8") as handle:
            quality_report = json.load(handle)

        validation_errors = []
        validation_errors.extend(cls.validate_manifest(manifest))
        validation_errors.extend(cls.validate_timeseries(timeseries))
        if pressure_sensors:
            validation_errors.extend(cls.validate_pressure_sensors(pressure_sensors))
        validation_errors.extend(cls._validate_directory(run_dir))
        if validation_errors:
            raise ValueError("; ".join(validation_errors))

        return {
            "case_id": str(case_id),
            "run_dir": run_dir,
            "manifest": manifest,
            "actuation_schedule": actuation_schedule,
            "timeseries": timeseries,
            "pressure_sensors": pressure_sensors,
            "flow_snapshots_dir": run_dir / "flow_snapshots",
            "quality_report": quality_report,
        }

    @classmethod
    def _ordered_columns(cls, columns: list[str], required: tuple[str, ...]) -> list[str]:
        """重排列顺序：必需的列在前，额外列在后。"""
        extras = [column for column in columns if column not in required]
        return list(required) + extras

    @classmethod
    def _validate_directory(cls, run_dir: Path) -> list[str]:
        """验证 case 目录结构是否完整。"""
        errors = []
        for directory_name in cls.case_directories:
            path = run_dir / directory_name
            if not path.exists() or not path.is_dir():
                errors.append(f"run directory missing {directory_name}/")
        return errors

    @classmethod
    def _actuation_schedule_from_timeseries(
        cls, rows: list[dict[str, Any]]
    ) -> tuple[list[str], list[dict[str, Any]]]:
        """从 timeseries 数据中提取激励计划（当未提供单独的计划时使用）。"""
        columns = ["physical_time", "window_id", *JET_COLUMNS]
        schedule_rows = [
            {column: row[column] for column in columns}
            for row in rows
        ]
        return columns, schedule_rows

    @classmethod
    def _build_quality_report(cls, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """构建质量报告：计算稳定性分数、喷气激活统计、相关性和数据完整性。"""
        solver_statuses = [str(row["solver_status"]).lower() for row in rows]
        success_count = sum(status in {"ok", "success", "converged", "stable", "1", "true"} for status in solver_statuses)
        violation_count = len(rows) - success_count

        # 各喷口的激活统计
        jet_activation_stats = {}
        for jet in JET_COLUMNS:
            values = [float(row[jet]) for row in rows]
            active = [value for value in values if value != 0.0]
            jet_activation_stats[jet] = {
                "activation_count": len(active),
                "activation_fraction": len(active) / len(values),
                "mean_command": sum(values) / len(values),
                "max_command": max(values),
                "min_command": min(values),
            }

        # 各载荷列之间的相关性分析
        numeric_columns = [
            column for column in (*LOAD_COLUMNS, "Fz_Total", "Drag_Total", "Pitch_Moment", "Roll_Moment", "Jet_Reaction_Z")
        ]
        correlations = cls._pairwise_abs_correlations(rows, numeric_columns)
        if correlations:
            strongest_pair, max_abs_offdiag = max(correlations, key=lambda item: item[1])
            mean_abs_offdiag = sum(value for _, value in correlations) / len(correlations)
        else:
            strongest_pair = ("", "")
            mean_abs_offdiag = 0.0
            max_abs_offdiag = 0.0

        # 数据完整性检查
        missing_count = sum(
            1
            for row in rows
            for value in row.values()
            if _is_nan_like(value)
        )
        total_cells = sum(len(row) for row in rows)

        return {
            "stability_score": success_count / len(rows),          # 求解器稳定性
            "constraint_violation_count": violation_count,          # 违规次数
            "jet_activation_stats": jet_activation_stats,           # 各喷口统计
            "correlation_matrix_summary": {                         # 载荷相关性
                "columns": numeric_columns,
                "mean_abs_offdiag": mean_abs_offdiag,
                "max_abs_offdiag": max_abs_offdiag,
                "strongest_pair": list(strongest_pair),
            },
            "data_completeness": {                                  # 数据完整性
                "missing_count": missing_count,
                "total_cells": total_cells,
                "complete": missing_count == 0,
            },
            "run_success_flag": violation_count == 0 and missing_count == 0,  # 成功标志
        }

    @classmethod
    def _pairwise_abs_correlations(
        cls,
        rows: list[dict[str, Any]],
        numeric_columns: list[str],
    ) -> list[tuple[tuple[str, str], float]]:
        """计算所有数值列对之间的皮尔逊相关系数绝对值。"""
        correlations: list[tuple[tuple[str, str], float]] = []
        if len(rows) < 2:
            return correlations

        series = {
            column: [float(row[column]) for row in rows]
            for column in numeric_columns
        }
        for left_idx, left in enumerate(numeric_columns):
            for right in numeric_columns[left_idx + 1 :]:
                correlations.append(((left, right), abs(cls._pearson(series[left], series[right]))))
        return correlations

    @staticmethod
    def _pearson(left: list[float], right: list[float]) -> float:
        """计算两组数据的皮尔逊线性相关系数。"""
        left_mean = sum(left) / len(left)
        right_mean = sum(right) / len(right)
        left_centered = [value - left_mean for value in left]
        right_centered = [value - right_mean for value in right]
        numerator = sum(left_value * right_value for left_value, right_value in zip(left_centered, right_centered))
        left_denom = math.sqrt(sum(value * value for value in left_centered))
        right_denom = math.sqrt(sum(value * value for value in right_centered))
        if left_denom == 0.0 or right_denom == 0.0:
            return 0.0
        return numerator / (left_denom * right_denom)

    @classmethod
    def _case_logger(cls, run_dir: Path) -> logging.Logger:
        """获取或创建 case 特定的日志记录器，日志输出到 runs/<case_id>/logs/case_io.log。"""
        logger = logging.getLogger(f"flow_control.case_schema.{run_dir.name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False
        log_path = run_dir / "logs" / "case_io.log"
        if not any(
            isinstance(handler, logging.FileHandler)
            and Path(handler.baseFilename) == log_path
            for handler in logger.handlers
        ):
            handler = logging.FileHandler(log_path, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            logger.addHandler(handler)
        return logger


@dataclass(frozen=True)
class ControlAction:
    """单个喷气控制指令（不可变）。

    Attributes:
        jet_id: 喷口标识符。
        enabled: 是否启用喷气。
        mass_flow_rate: 质量流量 (kg/s)。
        duty_cycle: 占空比 (0~1)。
        frequency_hz: 喷气频率 (Hz)。
    """

    jet_id: str
    enabled: bool
    mass_flow_rate: float
    duty_cycle: float
    frequency_hz: float


@dataclass(frozen=True)
class ScheduleStep:
    """在一次求解器/控制迭代中应用的控制指令集合（不可变）。

    Attributes:
        step_id: 步骤编号。
        iteration: 关联的求解器迭代步。
        duration_iterations: 持续时间（迭代步数）。
        actions: 该步骤中所有喷口的控制指令。
    """

    step_id: int
    iteration: int
    duration_iterations: int
    actions: tuple[ControlAction, ...]


@dataclass(frozen=True)
class Schedule:
    """一次实验的完整控制计划（不可变）。

    Attributes:
        name: 计划名称。
        steps: 计划包含的一系列控制步骤。
    """

    name: str
    steps: tuple[ScheduleStep, ...]


@dataclass(frozen=True)
class PlantObservation:
    """Mock Plant 或将来真实适配器发出的最小观测值（不可变）。

    Attributes:
        iteration: 观测发生的迭代步。
        drag: 阻力值。
        pressure_loss: 压力损失。
        stable: 系统是否稳定。
        notes: 附加说明文本。
    """

    iteration: int
    drag: float
    pressure_loss: float
    stable: bool
    notes: str = ""


@dataclass(frozen=True)
class ExperimentConfig:
    """第一个 Maglev 稀疏喷气流控制实验的顶层配置（不可变）。

    Attributes:
        project_name: 项目名称。
        case_name: 实验 case 名称。
        max_iterations: 最大迭代步数。
        control_interval_iterations: 控制间隔（迭代步数）。
        jet_ids: 喷口 ID 列表。
        default_mass_flow_rate: 默认质量流量 (kg/s)。
        default_duty_cycle: 默认占空比。
        default_frequency_hz: 默认喷气频率 (Hz)。
        output_dir: 输出目录。
    """

    project_name: str
    case_name: str
    max_iterations: int
    control_interval_iterations: int
    jet_ids: tuple[str, ...]
    default_mass_flow_rate: float
    default_duty_cycle: float
    default_frequency_hz: float
    output_dir: Path = field(default=Path("runs"))

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ExperimentConfig":
        """从配置字典（通常从 YAML 解析）创建 ExperimentConfig。

        期望的 YAML 结构：
          experiment:
            project_name: maglev_sparse_jet_9w
            case_name: baseline
            max_iterations: 1000
          control:
            interval_iterations: 50
            jets:
              - id: "JET_01"
              - id: "JET_02"
            defaults:
              mass_flow_rate: 0.0
              duty_cycle: 0.0
              frequency_hz: 0.0
          output:
            run_dir: runs
        """
        experiment = data.get("experiment", {})
        control = data.get("control", {})
        jets = control.get("jets", [])
        defaults = control.get("defaults", {})
        output = data.get("output", {})

        return cls(
            project_name=str(experiment.get("project_name", "maglev_sparse_jet_9w")),
            case_name=str(experiment.get("case_name", "baseline")),
            max_iterations=int(experiment.get("max_iterations", 1000)),
            control_interval_iterations=int(control.get("interval_iterations", 50)),
            jet_ids=tuple(str(jet["id"]) for jet in jets),
            default_mass_flow_rate=float(defaults.get("mass_flow_rate", 0.0)),
            default_duty_cycle=float(defaults.get("duty_cycle", 0.0)),
            default_frequency_hz=float(defaults.get("frequency_hz", 0.0)),
            output_dir=Path(output.get("run_dir", "runs")),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        """从 YAML 文件加载实验配置。"""
        with Path(path).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return cls.from_mapping(data)
