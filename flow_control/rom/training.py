"""Training-only workflows for the B06 ARX ROM.

Training and validation are deliberately separate.  Every usable row from the
explicitly supplied training case or dataset is used to fit the model.  This
module never selects a validation segment, computes validation metrics, or
writes prediction plots; those responsibilities belong to ``validation.py``.

B06 ARX 降阶模型（ROM）的纯训练工作流模块。
训练与验证严格分离：所有显式提供的训练案例中的可用行都用于拟合模型。
本模块不进行验证集划分、不计算验证指标、也不生成预测图——这些职能由 validation.py 承担。
设计思路：训练时尽可能多地使用数据以增强模型鲁棒性，验证时再用独立的 validation 流程评估。
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .arx_model import ARXModel
from .identifier import (
    MASSFLOW_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    load_case_table,
    matrix_from_rows,
    time_values_from_rows,
    write_json,
)


# ARX 训练结果的数据类（冻结 = 不可变）
# 记录训练产出的文件路径和统计信息
@dataclass(frozen=True)
class ARXTrainingResult:
    """Artifacts and row counts produced by a training-only ARX run."""

    out_dir: Path                    # 输出目录
    model_path: Path                 # 模型 JSON 文件路径（arx_model.json）
    training_summary_path: Path      # 训练摘要 JSON 文件路径（training_summary.json）
    train_cases: int                 # 参与训练的案例数
    source_rows: int                 # 原始数据行数（含滞后损失的行）
    fit_rows: int                    # 实际参与拟合的行数（剔除滞后边界后）
    case_ids: tuple[str, ...]        # 所有参与训练的案例 ID 列表


# ARX 数据集训练结果——继承 ARXTrainingResult，语义标记基于数据集索引的训练
@dataclass(frozen=True)
class ARXDatasetTrainingResult(ARXTrainingResult):
    """Training result for a dataset listed by ``index.csv``."""


# 从单个案例训练 ARX ROM 模型
# 加载案例数据 -> 拟合模型参数 -> 持久化到磁盘
def train_arx_rom_from_case(
    *,
    case_dir: str | Path,        # 案例目录（需包含 timeseries.csv 和 actuation_schedule.csv）
    out_dir: str | Path,         # 模型输出目录
    input_lags: int = 2,         # 输入滞后阶数
    output_lags: int = 3,        # 输出滞后阶数
    ridge_alpha: float = 1.0,   # Ridge 回归正则化系数（越大正则化越强）
) -> ARXTrainingResult:
    """Fit on the complete explicitly supplied case and persist the model.

    ``case_dir`` must contain ``timeseries.csv`` and an
    ``actuation_schedule.csv`` with the mass-flow command columns used by the
    ROM inputs.  No rows are reserved for validation.
    """

    sequence = _load_rom_sequence(case_dir)
    model, source_rows, fit_rows = _fit_sequences(
        [sequence],
        input_lags=input_lags,
        output_lags=output_lags,
        ridge_alpha=ridge_alpha,
    )
    result = _persist_training(
        model=model,
        out_dir=out_dir,
        source_kind="case",        # 标记来源为单个案例
        source_path=Path(case_dir),
        sequences=[sequence],
        source_rows=source_rows,
        fit_rows=fit_rows,
    )
    return ARXTrainingResult(**result)


# 从数据集（index.csv 索引的多个案例）训练 ARX ROM 模型
# 每个案例独立构建滞后特征，历史数据不会跨案例边界
def train_arx_rom_from_dataset(
    *,
    dataset_dir: str | Path,      # 数据集目录（需包含 index.csv）
    out_dir: str | Path,          # 模型输出目录
    input_lags: int = 2,          # 输入滞后阶数
    output_lags: int = 3,         # 输出滞后阶数
    ridge_alpha: float = 1.0,    # Ridge 正则化系数
) -> ARXDatasetTrainingResult:
    """Fit on every case listed in the supplied dataset ``index.csv``.

    Lagged features are built independently inside each case, so history never
    crosses case boundaries.  All listed cases are training cases; this
    function has no internal train/validation split.
    """

    dataset_path = Path(dataset_dir)
    case_records = _read_dataset_index(dataset_path)       # 读取索引获取案例列表
    sequences = [_load_rom_sequence(record["case_dir"]) for record in case_records]
    model, source_rows, fit_rows = _fit_sequences(
        sequences,
        input_lags=input_lags,
        output_lags=output_lags,
        ridge_alpha=ridge_alpha,
    )
    result = _persist_training(
        model=model,
        out_dir=out_dir,
        source_kind="dataset",     # 标记来源为数据集（多案例）
        source_path=dataset_path,
        sequences=sequences,
        source_rows=source_rows,
        fit_rows=fit_rows,
    )
    return ARXDatasetTrainingResult(**result)


# 核心拟合函数：对所有序列执行最小二乘（带 Ridge 正则化）求解 ARX 模型系数
# 每个序列的滞后特征独立构建，然后拼接所有序列数据统一求解
def _fit_sequences(
    sequences: list[dict[str, Any]],   # 每个元素含 case_id, inputs, outputs
    *,
    input_lags: int,    # 输入滞后阶数
    output_lags: int,   # 输出滞后阶数
    ridge_alpha: float, # Ridge 正则化系数
) -> tuple[ARXModel, int, int]:  # 返回 (模型, 原始行数, 拟合行数)
    if not sequences:
        raise ValueError("training set contains no cases")

    # 初始化 ARX 模型，include_current_input=True 表示当前时刻的输入也作为特征
    model = ARXModel(
        input_lags=input_lags,
        output_lags=output_lags,
        include_current_input=True,
        ridge_alpha=ridge_alpha,
    )
    model.input_names_ = list(ROM_INPUT_COLUMNS)
    model.output_names_ = list(ROM_OUTPUT_COLUMNS)

    x_parts: list[np.ndarray] = []  # 各序列的特征矩阵片段
    y_parts: list[np.ndarray] = []  # 各序列的目标矩阵片段
    source_rows = 0                  # 累计原始行数
    fit_rows = 0                     # 累计拟合行数（剔除滞后边界损失）
    for sequence in sequences:
        inputs = sequence["inputs"]
        outputs = sequence["outputs"]
        source_rows += len(inputs)
        # 检查序列长度是否足以构建滞后特征（至少 max_lag + 1 行）
        if len(inputs) <= model.max_lag:
            raise ValueError(
                f"training case {sequence['case_id']} is too short for max_lag={model.max_lag}"
            )
        # _design_matrix 构建设计矩阵：每行包含滞后输入/输出 + 当前输入
        # 返回的行数 = len(inputs) - max_lag（前 max_lag 行没有足够历史）
        x_case, y_case = model._design_matrix(inputs, outputs, model.max_lag, len(inputs))
        x_parts.append(x_case)
        y_parts.append(y_case)
        fit_rows += len(y_case)

    # 沿行方向拼接所有案例的设计矩阵和目标矩阵
    x_train = np.vstack(x_parts)
    y_train = np.vstack(y_parts)

    # Ridge 回归的正规方程：(X^T X + alpha*I) * beta = X^T y
    penalty = ridge_alpha * np.eye(x_train.shape[1], dtype=float)
    penalty[0, 0] = 0.0  # 不对偏置项（截距）施加正则化
    lhs = x_train.T @ x_train + penalty
    rhs = x_train.T @ y_train
    try:
        # 优先使用 Cholesky 分解求解（更快更稳定）
        model.coefficients_ = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        # 如果矩阵奇异，回退到伪逆求解
        model.coefficients_ = np.linalg.pinv(lhs) @ rhs
    return model, source_rows, fit_rows


# 将训练好的模型和训练摘要持久化到磁盘（JSON 格式）
# 写入 arx_model.json（模型参数 + 训练元数据）和 training_summary.json（训练统计）
def _persist_training(
    *,
    model: ARXModel,              # 训练好的 ARX 模型
    out_dir: str | Path,          # 输出目录
    source_kind: str,             # 来源类型："case" 或 "dataset"
    source_path: Path,            # 源数据路径
    sequences: list[dict[str, Any]],  # 所有训练序列
    source_rows: int,             # 原始数据行数
    fit_rows: int,                # 实际拟合行数
) -> dict[str, Any]:              # 返回结果字典，用于构造 ARXTrainingResult
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    model_path = output_path / "arx_model.json"
    training_summary_path = output_path / "training_summary.json"
    case_ids = tuple(str(sequence["case_id"]) for sequence in sequences)
    case_time_steps = {
        str(sequence["case_id"]): _infer_sequence_time_step(sequence)
        for sequence in sequences
    }

    # 将模型序列化为字典，附加训练数据元信息
    model_payload = model.to_dict()
    model_payload["training_data"] = {
        "source_kind": source_kind,
        "source_path": str(source_path),
        "case_ids": list(case_ids),
        "source_rows": source_rows,
        "fit_rows": fit_rows,
        "case_time_steps": case_time_steps,
        "fit_policy": "all usable rows from the explicitly supplied training set; no internal split",
    }
    write_json(model_path, model_payload)

    # 写入训练摘要 JSON，包含模型超参数、输入/输出列定义等
    write_json(
        training_summary_path,
        {
            "phase": "training",                     # 阶段标记
            "validation_performed": False,           # 纯训练，未做验证
            "source_kind": source_kind,
            "source_path": str(source_path),
            "case_count": len(sequences),
            "case_ids": list(case_ids),
            "source_rows": source_rows,
            "fit_rows": fit_rows,
            "case_time_steps": case_time_steps,
            "lag_context_rows_per_case": model.max_lag,  # 每个案例因滞后损失的行数
            "input_columns": list(ROM_INPUT_COLUMNS),
            "output_columns": list(ROM_OUTPUT_COLUMNS),
            "model": {
                "type": "ARX",
                "input_lags": model.input_lags,
                "output_lags": model.output_lags,
                "include_current_input": model.include_current_input,
                "ridge_alpha": model.ridge_alpha,
            },
            "fit_policy": "all usable rows from the explicitly supplied training set; no internal split",
        },
    )
    return {
        "out_dir": output_path,
        "model_path": model_path,
        "training_summary_path": training_summary_path,
        "train_cases": len(sequences),
        "source_rows": source_rows,
        "fit_rows": fit_rows,
        "case_ids": case_ids,
    }


# 读取数据集索引文件 index.csv，返回按 case_index 排序的案例记录列表
# index.csv 包含各案例的 case_id、case_index、可选的 case_dir 等字段
def _read_dataset_index(dataset_dir: Path) -> list[dict[str, Any]]:
    index_path = dataset_dir / "index.csv"
    if not index_path.exists():
        raise FileNotFoundError(f"dataset index not found: {index_path}")
    with index_path.open("r", encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    if not records:
        raise ValueError(f"dataset index contains no cases: {index_path}")
    for record in records:
        record["case_index"] = int(record.get("case_index", len(records)))
        # 如果记录中未提供 case_dir，默认使用 dataset_dir/case_id 作为案例目录
        record["case_dir"] = (
            str(dataset_dir / record["case_id"])
            if not record.get("case_dir")
            else record["case_dir"]
        )
    return sorted(records, key=lambda record: record["case_index"])


# 加载案例的 ROM 序列数据
# 返回包含 case_id、inputs 矩阵、outputs 矩阵的字典，供拟合和持久化使用
def _load_rom_sequence(case_dir: str | Path) -> dict[str, Any]:
    rows = load_case_table(case_dir)  # 加载数据表（合并 time series 和调度表）
    # 检查是否包含所有必需的质量流量命令列
    missing_massflow = [column for column in MASSFLOW_COLUMNS if column not in rows[0]]
    if missing_massflow:
        raise ValueError(
            f"{case_dir} is missing mass-flow command columns: "
            + ", ".join(missing_massflow[:4])
            + ("..." if len(missing_massflow) > 4 else "")
        )
    return {
        "case_id": Path(case_dir).name,                             # 案例 ID = 目录名
        "time_values": time_values_from_rows(rows),                 # 物理时间轴，用于记录 timestep
        "inputs": matrix_from_rows(rows, ROM_INPUT_COLUMNS),       # 输入矩阵 [N_steps x N_inputs]
        "outputs": matrix_from_rows(rows, ROM_OUTPUT_COLUMNS),     # 输出矩阵 [N_steps x N_outputs]
    }


def _infer_sequence_time_step(sequence: dict[str, Any]) -> float:
    time_values = sequence.get("time_values")
    if time_values is None or len(time_values) < 2:
        return 0.0
    diffs = np.diff(np.asarray(time_values, dtype=float))
    positive = diffs[diffs > 1.0e-12]
    return float(np.median(positive)) if len(positive) else 0.0
