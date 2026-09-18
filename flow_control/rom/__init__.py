"""降阶模型（ROM）包 —— 喷气-载荷响应的 ARX 线性建模。

核心模型：
  ARXModel — 自回归带外生输入模型，多输入多输出（MIMO）。

工作流组件：
  - training:    在单个 case 或数据集上训练 ARX 模型
  - inference:   用已训练的模型对新 case 进行递推预测
  - validation:  在验证数据上评估模型性能（RMSE、相关系数等）
  - identifier:  数据加载、指标计算、SVG 绘图工具
  - generate_arx_dataset: 使用 Mock Plant 批量生成训练/验证数据集

数学公式：
  y[t] = c + A_1*y[t-1] + ... + A_na*y[t-na]
           + B_0*u[t] + ... + B_nb*u[t-nb+1]
"""

from .arx_model import ARXModel
from .identifier import (
    MASSFLOW_COLUMNS,
    REGIONAL_OUTPUT_COLUMNS,
    ROM_INPUT_COLUMNS,
    ROM_OUTPUT_COLUMNS,
    compute_metrics,
    load_case_table,
    matrix_from_rows,
    merge_schedule_columns,
    read_csv_rows,
    require_columns,
    time_values_from_rows,
    write_error_svg,
    write_json,
    write_prediction_csv,
    write_prediction_svg,
    write_rmse_bar_svg,
    write_single_jet_response_summary,
)
from .training import ARXDatasetTrainingResult, ARXTrainingResult, train_arx_rom_from_case, train_arx_rom_from_dataset
from .inference import ARXUseResult, use_arx_rom_on_case, use_arx_rom_on_schedule
from .validation import ARXValidationResult, validate_arx_rom

__all__ = [
    "ARXModel",
    "ARXDatasetTrainingResult",
    "ARXTrainingResult",
    "ARXUseResult",
    "ARXValidationResult",
    "MASSFLOW_COLUMNS",
    "REGIONAL_OUTPUT_COLUMNS",
    "ROM_INPUT_COLUMNS",
    "ROM_OUTPUT_COLUMNS",
    "compute_metrics",
    "load_case_table",
    "matrix_from_rows",
    "merge_schedule_columns",
    "read_csv_rows",
    "require_columns",
    "time_values_from_rows",
    "train_arx_rom_from_case",
    "train_arx_rom_from_dataset",
    "use_arx_rom_on_case",
    "use_arx_rom_on_schedule",
    "validate_arx_rom",
    "write_error_svg",
    "write_json",
    "write_prediction_csv",
    "write_prediction_svg",
    "write_rmse_bar_svg",
    "write_single_jet_response_summary",
]
