"""Use a trained ARX ROM to produce a standard prediction case.

使用已训练的 ARX 降阶模型（ROM）对指定案例进行预测推理。
本模块加载训练好的模型参数，对案例数据执行递归多步预测，
并将预测结果按照标准案例目录结构输出，包含 time series、调度表和质量报告。

预测流程：
1. 加载训练好的 ARX 模型（JSON 格式）
2. 加载目标案例的输入序列和初始输出历史
3. 前 max_lag 行作为 warmup 历史（直接复制真实值）
4. 从 max_lag 行开始，执行递归 ARX 预测
5. 将结果写入标准 CaseSchema 格式的案例目录
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from flow_control.data_schema import CaseSchema
from flow_control.excitation_patterns.common import MASSFLOW_COLUMNS
from flow_control.star_ingest.case_data_loader import write_quality_report
from starccm.control.control_spec import GLOBAL_OUTPUT_COLUMNS, JET_COLUMNS

from .arx_model import ARXModel
from .identifier import (
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    load_case_table,
    matrix_from_rows,
    read_csv_rows,
)

# 实际质量流量列名：actual_massflow_01 到 actual_massflow_24
# 用于预测结果中记录实际达到的质量流量（= 喷射器状态 * 命令质量流量）
ACTUAL_MASSFLOW_COLUMNS = tuple(f"actual_massflow_{idx:02d}" for idx in range(1, 25))


# ARX 模型推理结果的数据类（冻结 = 不可变）
# 记录预测产出的文件路径和统计信息
@dataclass(frozen=True)
class ARXUseResult:
    """Artifacts produced by using an existing ARX model."""

    out_dir: Path                    # 输出案例目录
    prediction_case_dir: Path        # 预测案例目录（与 out_dir 相同）
    prediction_timeseries_path: Path # 预测的 time series CSV 路径
    quality_report_path: Path        # 质量报告 JSON 路径
    source_rows: int                 # 源数据行数
    predicted_rows: int              # 预测行数（剔除 warmup 行）
    warmup_rows: int                 # warmup 行数（= model.max_lag）
    run_success_flag: bool           # 运行成功标志


# 在指定案例上运行训练好的 ARX 模型，生成预测案例目录
# 核心流程：加载模型 -> 加载案例数据 -> 递归预测 -> 写入标准案例格式
def use_arx_rom_on_case(
    *,
    model_path: str | Path,    # 训练好的模型 JSON 文件路径
    case_dir: str | Path,      # 源案例目录（提供输入序列和初始输出历史）
    out_dir: str | Path,       # 输出目录（预测案例写入位置）
) -> ARXUseResult:
    """Run a trained ARX model on an explicit case and write a prediction case.

    The source case supplies the input sequence and the initial output history.
    Rows before ``model.max_lag`` are copied as warmup history. Rows at and
    after ``model.max_lag`` are recursive ARX predictions.
    """

    model = ARXModel.from_dict(_read_json(model_path))
    rows = load_case_table(case_dir)
    if len(rows) <= model.max_lag:
        raise ValueError(f"case is too short for max_lag={model.max_lag}: {case_dir}")

    # 提取输入矩阵和观测输出矩阵
    inputs = matrix_from_rows(rows, ROM_INPUT_COLUMNS)
    observed_outputs = matrix_from_rows(rows, ROM_OUTPUT_COLUMNS)
    # 递归预测：从 start_index = max_lag 开始，
    # 之前的行为 warmup 阶段（使用观测值作为历史），此后使用自身预测值作为历史
    prediction = model.predict_recursive(
        inputs,
        observed_outputs,
        start_index=model.max_lag,
    )
    # 组装预测案例行数据（warmup 行 + 预测行）
    prediction_rows = _prediction_case_rows(
        source_rows=rows,
        observed_outputs=observed_outputs,
        prediction=prediction,
        warmup_rows=model.max_lag,
    )
    schedule_rows = _read_schedule_rows(case_dir)

    # 通过 CaseSchema 将预测结果写入标准案例目录结构
    output_path = Path(out_dir)
    old_root = CaseSchema.runs_root
    CaseSchema.runs_root = output_path.parent
    try:
        schema_result = CaseSchema.write_case(
            {
                "case_id": output_path.name,
                "manifest": {
                    "geometry_version": "arx-rom",       # ARX ROM 不涉及几何
                    "mesh_version": "not-applicable",     # 不涉及网格
                    "flow_velocity": 0.0,                 # 不涉及流场参数
                    "gap": 0.0,
                    "time_step": _infer_time_step(rows),  # 从数据推断时间步长
                    "jet_amplitude": _max_total_massflow(schedule_rows),  # 喷射器最大总质量流量
                    "window_duration": _infer_time_step(rows),
                    "random_seed": 0,
                    "case_stage": "arx_model_use",
                    "check_mode": "arx_use",
                    "source_case_dir": str(case_dir),     # 记录源案例路径
                    "source_model": str(model_path),       # 记录源模型路径
                    "validation_mode": "full_case",
                },
                "timeseries": prediction_rows,
                "actuation_schedule": schedule_rows,
                "quality_report": {
                    "case_stage": "arx_model_use",
                    "source_case_dir": str(case_dir),
                    "source_model": str(model_path),
                    "warmup_rows": model.max_lag,
                    "predicted_rows": len(prediction_rows) - model.max_lag,
                },
            }
        )
    finally:
        # 恢复全局 CaseSchema.runs_root，避免影响其他操作
        CaseSchema.runs_root = old_root

    quality_report = write_quality_report(schema_result["run_dir"], check_mode="arx_use")
    return ARXUseResult(
        out_dir=Path(schema_result["run_dir"]),
        prediction_case_dir=Path(schema_result["run_dir"]),
        prediction_timeseries_path=Path(schema_result["files"]["timeseries"]),
        quality_report_path=Path(schema_result["files"]["quality_report"]),
        source_rows=len(rows),
        predicted_rows=len(prediction_rows) - model.max_lag,
        warmup_rows=model.max_lag,
        run_success_flag=bool(quality_report.get("run_success_flag", False)),
    )


# 组装预测案例的每一行数据
# warmup 行（前 max_lag 行）使用观测输出值，之后的预测行使用 ARX 模型递归预测值
def _prediction_case_rows(
    *,
    source_rows: list[dict[str, str]],    # 源案例的时间序列行数据
    observed_outputs: np.ndarray,         # 观测到的输出矩阵
    prediction: np.ndarray,               # ARX 模型递归预测输出矩阵
    warmup_rows: int,                     # warmup 行数（= model.max_lag）
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row_idx, source in enumerate(source_rows):
        # 基础字段：时间和窗口 ID
        record: dict[str, Any] = {
            "physical_time": float(source.get("physical_time", row_idx)),
            "window_id": int(float(source.get("window_id", row_idx))),
        }
        # 复制喷射器状态列
        for column in JET_COLUMNS:
            record[column] = float(source.get(column, 0.0) or 0.0)
        # 复制质量流量命令列
        for column in MASSFLOW_COLUMNS:
            record[column] = float(source.get(column, 0.0) or 0.0)
        # 计算实际质量流量 = 喷射器状态 * 命令质量流量（每个喷射器独立计算）
        for jet_column, cmd_column, actual_column in zip(
            JET_COLUMNS,
            MASSFLOW_COLUMNS,
            ACTUAL_MASSFLOW_COLUMNS,
        ):
            actual_default = float(record[jet_column]) * float(record[cmd_column])
            record[actual_column] = float(source.get(actual_column, actual_default) or 0.0)

        # 输出值选择：warmup 阶段用观测值，之后用预测值
        output_values = (
            observed_outputs[row_idx]
            if row_idx < warmup_rows
            else prediction[row_idx - warmup_rows]
        )
        for out_idx, column in enumerate(ROM_OUTPUT_COLUMNS):
            record[column] = float(output_values[out_idx])
        # 补全全局输出列（如 solver_status）
        for column in GLOBAL_OUTPUT_COLUMNS:
            if column not in record:
                record[column] = source.get(column, "success" if column == "solver_status" else 0.0)
        record["solver_status"] = "success"               # ARX 预测总是 "成功"
        record["case_stage"] = "arx_warmup" if row_idx < warmup_rows else "arx_prediction"
        rows.append(record)
    return rows


# 读取案例的 actuation_schedule.csv 文件；若不存在则从 load_case_table 中组装调度行
def _read_schedule_rows(case_dir: str | Path) -> list[dict[str, str]]:
    path = Path(case_dir) / "actuation_schedule.csv"
    if path.exists():
        return read_csv_rows(path)
    # 回退方案：从 timeseries 数据中提取喷射器状态和质量流量列组成调度表
    return [
        {
            "physical_time": row.get("physical_time", ""),
            "window_id": row.get("window_id", idx),
            **{column: row.get(column, 0.0) for column in JET_COLUMNS},
            **{column: row.get(column, 0.0) for column in MASSFLOW_COLUMNS},
        }
        for idx, row in enumerate(load_case_table(case_dir))
    ]


# 从时间序列数据中推断时间步长 dt = 第2行时间 - 第1行时间
def _infer_time_step(rows: list[dict[str, str]]) -> float:
    if len(rows) < 2:
        return 0.0
    return float(rows[1].get("physical_time", 0.0)) - float(rows[0].get("physical_time", 0.0))


# 计算所有行中 24 个质量流量命令之和的最大值，用于 manifest 中的 jet_amplitude
def _max_total_massflow(rows: list[dict[str, str]]) -> float:
    max_value = 0.0
    for row in rows:
        total = sum(float(row.get(column, 0.0) or 0.0) for column in MASSFLOW_COLUMNS)
        max_value = max(max_value, total)
    return max_value


# 读取 JSON 文件并解析为字典（加载训练好的 ARX 模型参数）
def _read_json(path: str | Path) -> dict[str, Any]:
    import json

    return json.loads(Path(path).read_text(encoding="utf-8"))
