"""Validation workflow for already trained ARX ROM snapshots.

已训练 ARX 降阶模型（ROM）快照的验证工作流模块。
本模块对训练好的 ARX 模型执行验证：在指定案例或数据集上运行递归预测，
计算各项指标（RMSE、NRMSE、相关系数等），并生成预测对比图、误差图和 RMSE 柱状图。

验证流程：
1. 加载训练好的 ARX 模型
2. 加载一个或多个验证案例数据
3. 对每个案例执行递归预测（前 max_lag 行用于初始化历史，后续行进行预测）
4. 拼接所有案例的预测结果和真实值
5. 计算验证指标并写入 JSON
6. 生成 CSV 预测表和 SVG 可视化图表
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .arx_model import ARXModel
from .identifier import (
    REGIONAL_OUTPUT_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    compute_metrics,
    load_case_table,
    matrix_from_rows,
    time_values_from_rows,
    write_error_svg,
    write_json,
    write_prediction_svg,
    write_rmse_bar_svg,
)


# ARX 验证结果的数据类（冻结 = 不可变）
# 记录验证产出的文件路径、统计信息和指标
@dataclass(frozen=True)
class ARXValidationResult:
    """Paths and metrics produced by validating an existing ARX ROM."""

    out_dir: Path                                     # 输出目录
    metrics_path: Path                                # 验证指标 JSON 路径
    prediction_csv_path: Path                         # 预测结果 CSV 路径（包含 true/pred/error 列）
    prediction_plot_path: Path                        # 预测对比图 SVG 路径
    error_plot_path: Path                             # 误差图 SVG 路径
    rmse_plot_path: Path                              # RMSE 柱状图 SVG 路径
    case_count: int                                   # 验证案例数
    validation_rows: int                              # 验证行数（剔除 warmup 行后的总行数）
    metrics: dict[str, dict[str, float]]              # 各输出列的指标字典


# 验证已训练 ARX ROM 模型的主函数
# 对单个案例或数据集中的所有案例执行递归预测，并计算验证指标
def validate_arx_rom(
    *,
    model_path: str | Path,                # 训练好的模型 JSON 文件路径
    out_dir: str | Path,                   # 验证输出目录
    dataset_dir: str | Path | None = None, # 数据集目录（与 case_dir 二选一）
    case_dir: str | Path | None = None,    # 单个案例目录（与 dataset_dir 二选一）
    case_start: int = 0,                   # 数据集起始案例索引
    case_count: int | None = None,         # 数据集案例数量（None = 全部）
) -> ARXValidationResult:
    """Validate a trained ARX ROM on one case or a case dataset."""

    # 确保且仅提供一种验证源
    if (dataset_dir is None) == (case_dir is None):
        raise ValueError("provide exactly one of dataset_dir or case_dir")

    model = _load_model(model_path)
    # 根据参数选择加载单个案例或数据集中的多个案例
    sequences = (
        [_load_rom_sequence(case_dir)]
        if case_dir is not None
        else _load_dataset_sequences(Path(dataset_dir), case_start=case_start, case_count=case_count)
    )
    if not sequences:
        raise ValueError("no validation cases selected")

    prediction_parts: list[np.ndarray] = []   # 各案例的预测结果矩阵
    truth_parts: list[np.ndarray] = []         # 各案例的真实值矩阵
    plot_time_parts: list[np.ndarray] = []     # 连续拼接的验证物理时间轴
    prediction_rows: list[dict[str, Any]] = [] # 用于输出 CSV 的行数据
    time_axis_offset = 0.0
    case_time_steps: dict[str, float] = {}
    # 对每个验证案例执行递归预测
    for sequence in sequences:
        inputs = sequence["inputs"]
        outputs = sequence["outputs"]
        case_time_step = _infer_sequence_time_step(sequence)
        case_time_steps[str(sequence["case_id"])] = case_time_step
        if len(inputs) <= model.max_lag:
            raise ValueError(f"case {sequence['case_id']} is too short for max_lag={model.max_lag}")
        # 递归预测：前 max_lag 行作为历史初始化，之后递归预测
        # 这是关键步骤——模型不依赖任何未来的真实观测值
        prediction = model.predict_recursive(inputs, outputs, start_index=model.max_lag)
        truth = outputs[model.max_lag :]       # 与预测对齐的真实值（剔除 warmup 行）
        prediction_parts.append(prediction)
        truth_parts.append(truth)
        plot_times = _validation_plot_times(
            sequence["time_values"][model.max_lag :],
            offset=time_axis_offset,
            fallback_dt=case_time_step,
        )
        plot_time_parts.append(plot_times)
        if len(plot_times):
            time_axis_offset = float(plot_times[-1]) + (case_time_step if case_time_step > 0.0 else 1.0)
        _extend_prediction_rows(
            prediction_rows,
            case_id=sequence["case_id"],
            time_values=sequence["time_values"][model.max_lag :],
            truth=truth,
            prediction=prediction,
        )

    # 拼接所有案例的预测和真实数据
    prediction_all = np.vstack(prediction_parts)
    truth_all = np.vstack(truth_parts)
    validation_time_axis = np.concatenate(plot_time_parts) if plot_time_parts else np.asarray([], dtype=float)
    metrics = compute_metrics(truth_all, prediction_all, ROM_OUTPUT_COLUMNS)

    # 设置输出路径
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    metrics_path = output_path / "metrics.json"
    prediction_csv_path = output_path / "prediction_timeseries.csv"
    prediction_plot_path = output_path / "prediction_6_load_cells.svg"
    error_plot_path = output_path / "error_6_load_cells.svg"
    rmse_plot_path = output_path / "rmse_bar.svg"

    # 写入验证指标 JSON，包含详细的验证策略描述和误差解释指南
    write_json(
        metrics_path,
        {
            "phase": "validation",
            "training_performed": False,
            "model_path": str(model_path),
            "dataset_dir": str(dataset_dir) if dataset_dir is not None else None,
            "case_dir": str(case_dir) if case_dir is not None else None,
            "case_count": len(sequences),
            "validation_rows": int(len(truth_all)),
            "warmup_rows_per_case": model.max_lag,
            "case_time_steps": case_time_steps,
            "case_ids": [sequence["case_id"] for sequence in sequences],
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
            "metrics": metrics,
            "validation_policy": (
                "all explicitly selected validation cases are evaluated; the first max_lag rows of each "
                "case initialize ARX history, then recursive prediction uses no measured outputs and no fitting"
            ),
            # 误差解释：为分析人员提供常见误差模式的可能原因
            "error_interpretation": {
                "delay": "Shifted peaks can indicate insufficient input/output lags or an unmodeled transport delay.",
                "noise": "Irregular high-frequency residuals are expected when the source data contains output noise.",
                "model_order": "Too few lags underfit slow dynamics; too many lags can be fragile for limited data.",
                "input_correlation": "Jets activated together can make individual input coefficients correlated.",
                "data_amount": "Short or weakly excited datasets limit identification quality.",
            },
        },
    )
    # 写入预测时间序列 CSV
    _write_prediction_csv(prediction_csv_path, prediction_rows)

    # 生成可视化图表（仅对区域载荷列作图）
    regional_count = len(REGIONAL_OUTPUT_COLUMNS)
    write_prediction_svg(
        prediction_plot_path,
        validation_time_axis,
        truth_all[:, :regional_count],
        prediction_all[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_error_svg(
        error_plot_path,
        validation_time_axis,
        truth_all[:, :regional_count],
        prediction_all[:, :regional_count],
        REGIONAL_OUTPUT_COLUMNS,
    )
    write_rmse_bar_svg(rmse_plot_path, metrics, REGIONAL_OUTPUT_COLUMNS)

    return ARXValidationResult(
        out_dir=output_path,
        metrics_path=metrics_path,
        prediction_csv_path=prediction_csv_path,
        prediction_plot_path=prediction_plot_path,
        error_plot_path=error_plot_path,
        rmse_plot_path=rmse_plot_path,
        case_count=len(sequences),
        validation_rows=int(len(truth_all)),
        metrics=metrics,
    )


# 从 JSON 文件中加载训练好的 ARXModel
def _load_model(path: str | Path) -> ARXModel:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return ARXModel.from_dict(payload)


# 从数据集索引中加载一批验证案例的序列数据
# 支持按起始索引和数量选择子集
def _load_dataset_sequences(
    dataset_dir: Path,            # 数据集目录
    *,
    case_start: int,              # 起始案例索引（从 0 开始计数）
    case_count: int | None,       # 加载的案例数量（None = 加载到末尾）
) -> list[dict[str, Any]]:
    records = _read_dataset_index(dataset_dir)
    # 根据 case_start 和 case_count 切片选择案例
    selected = records[case_start:] if case_count is None else records[case_start : case_start + case_count]
    return [_load_rom_sequence(record["case_dir"]) for record in selected]


# 读取数据集索引文件 index.csv，按 case_index 排序返回
# 注：此函数与 training.py 中的 _read_dataset_index 功能相似，但实现略有不同
# （training 版本使用 records 长度作为默认 case_index，validation 版本默认 0）
def _read_dataset_index(dataset_dir: Path) -> list[dict[str, Any]]:
    index_path = dataset_dir / "index.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"dataset index not found: {index_path}")
    with index_path.open("r", encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    for record in records:
        record["case_index"] = int(record.get("case_index", 0))
        record["case_dir"] = record.get("case_dir") or str(dataset_dir / record["case_id"])
    return sorted(records, key=lambda record: record["case_index"])


# 加载单个案例的 ROM 序列数据（含时间值）
# 与 training.py 的版本相比，额外返回 time_values 用于 CSV 输出中的时间轴
def _load_rom_sequence(case_dir: str | Path) -> dict[str, Any]:
    rows = load_case_table(case_dir)
    return {
        "case_id": Path(case_dir).name,                  # 案例 ID = 目录名
        "time_values": time_values_from_rows(rows),      # 时间值（用于输出 CSV）
        "inputs": matrix_from_rows(rows, ROM_INPUT_COLUMNS),    # 输入矩阵
        "outputs": matrix_from_rows(rows, ROM_OUTPUT_COLUMNS),  # 输出矩阵
    }


def _infer_sequence_time_step(sequence: dict[str, Any]) -> float:
    time_values = np.asarray(sequence.get("time_values", []), dtype=float)
    if len(time_values) < 2:
        return 0.0
    diffs = np.diff(time_values)
    positive = diffs[diffs > 1.0e-12]
    return float(np.median(positive)) if len(positive) else 0.0


def _validation_plot_times(
    time_values: np.ndarray,
    *,
    offset: float,
    fallback_dt: float,
) -> np.ndarray:
    if len(time_values) == 0:
        return np.asarray([], dtype=float)
    values = np.asarray(time_values, dtype=float)
    relative = values - values[0]
    if len(relative) > 1 and np.max(relative) > 0.0:
        return offset + relative
    dt = fallback_dt if fallback_dt > 0.0 else 1.0
    return offset + np.arange(len(values), dtype=float) * dt


# 将单个案例的预测结果行扩展到全局预测行列表中
# 每行包含 case_id、physical_time、各输出列的 true/pred/error 三个字段
def _extend_prediction_rows(
    rows: list[dict[str, Any]],       # 目标列表（会被原地修改）
    *,
    case_id: str,                     # 当前案例 ID
    time_values: np.ndarray,          # 时间值数组
    truth: np.ndarray,                # 真实值矩阵
    prediction: np.ndarray,           # 预测值矩阵
) -> None:
    for row_idx, time_value in enumerate(time_values):
        record: dict[str, Any] = {
            "case_id": case_id,
            "physical_time": float(time_value),
        }
        for col_idx, column in enumerate(ROM_OUTPUT_COLUMNS):
            record[f"{column}_true"] = float(truth[row_idx, col_idx])
            record[f"{column}_pred"] = float(prediction[row_idx, col_idx])
            record[f"{column}_error"] = float(prediction[row_idx, col_idx] - truth[row_idx, col_idx])
        rows.append(record)


# 将预测行数据写入 CSV 文件
# CSV 列结构：case_id, physical_time, (column_true, column_pred, column_error) 对于每个输出列
def _write_prediction_csv(path: str | Path, rows: list[dict[str, Any]]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["case_id", "physical_time"]
    for column in ROM_OUTPUT_COLUMNS:
        fieldnames.extend([f"{column}_true", f"{column}_pred", f"{column}_error"])
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
