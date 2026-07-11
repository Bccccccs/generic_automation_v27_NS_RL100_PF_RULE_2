#!/usr/bin/env bash
# ==============================================================================
# run_rom_validate.sh — ARX 降阶模型验证流程。
#
# 工作流程（两步）：
#   1. 生成验证数据集 — 使用 MockDynamicPlant24x6 生成 10 个与训练
#      集不同种子（起始种子 20260718）的 sparse_random_groups case
#   2. 验证模型 — 对验证集的每个 case 执行递推预测，计算 RMSE 等指标
#
# 验证输出（runs/arx/validation/）：
#   - metrics.json                      — 各输出列的 RMSE/NRMSE/相关系数等
#   - prediction_timeseries.csv         — 预测与真实值对比表
#   - prediction_6_load_cells.svg       — 6 区域载荷预测 vs 真实对比图
#   - error_6_load_cells.svg            — 预测误差图
#   - rmse_bar.svg                      — RMSE 柱状图
#
# 前置条件：
#   模型文件 runs/arx/model/arx_model.json 必须存在（先运行 run_rom_train.sh）
# ==============================================================================
set -euo pipefail

# 获取脚本所在目录和项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

# 目录和路径定义
VALID_DIR="runs/arx/vaild"             # 验证数据集目录
MODEL_PATH="runs/arx/model/arx_model.json"    # 训练好的模型
VALIDATION_OUT="runs/arx/validation"    # 验证结果输出目录

# 检查模型是否存在
if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "模型不存在：${MODEL_PATH}"
  echo "请先运行：bash examples/run_rom_train.sh"
  exit 1
fi

# Step 1: 生成验证数据集
# 使用与训练不同的种子起始值（20260718），确保验证集的独立性
echo "Generating ROM validation dataset -> ${VALID_DIR}"
"${PYTHON_BIN}" -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out "${VALID_DIR}" \
  --count 10 \
  --start-seed 20260718 \
  --overwrite

echo
# Step 2: 验证 ARX 模型
# 对验证集所有 case 进行递推预测并计算性能指标
echo "Validating ARX ROM -> ${VALIDATION_OUT}"
"${PYTHON_BIN}" -m flow_control.cli.validate_rom \
  --model "${MODEL_PATH}" \
  --dataset-dir "${VALID_DIR}" \
  --out "${VALIDATION_OUT}"

echo
echo "Done. Validation outputs:"
echo "${VALIDATION_OUT}/metrics.json"
echo "${VALIDATION_OUT}/prediction_timeseries.csv"
echo "${VALIDATION_OUT}/prediction_6_load_cells.svg"
echo "${VALIDATION_OUT}/error_6_load_cells.svg"
echo "${VALIDATION_OUT}/rmse_bar.svg"
