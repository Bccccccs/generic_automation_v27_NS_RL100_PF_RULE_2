#!/usr/bin/env bash
# ==============================================================================
# run_rom_train.sh — ARX 降阶模型（ROM）训练完整流程。
#
# 工作流程（两步）：
#   1. 生成训练数据集 — 使用 MockDynamicPlant24x6 生成 100 个稀疏随机
#      分组 case（种子从系统配置的 random_seed 开始递增）
#   2. 训练 ARX 模型 — 使用全部 case 的所有可用时序行拟合模型
#
# 输出文件：
#   训练数据集：runs/arx/training/   （含 index.csv 和 100 个 case 目录）
#   模型输出：  runs/arx/model/
#     - arx_model.json           — 模型快照（系数 + 超参数）
#     - training_summary.json    — 训练摘要
#
# 模型超参数（如需调整可直接修改下方参数）：
#   --input-lags=2    输入滞后（含当前步 u[t]）
#   --output-lags=3   输出滞后（y[t-1], y[t-2], y[t-3]）
#   --ridge-alpha=1.0 岭回归正则化系数
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

# 输出目录定义
TRAIN_DIR="runs/arx/training"   # 训练数据集存放目录
MODEL_DIR="runs/arx/model"      # 训练好的模型输出目录

# Step 1: 生成训练数据集
# 使用 sparse_random_groups 模式生成 100 个不同种子的 case
echo "Generating ROM training dataset -> ${TRAIN_DIR}"
"${PYTHON_BIN}" -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out "${TRAIN_DIR}" \
  --count 100 \
  --overwrite

echo
# Step 2: 训练 ARX 模型
# 使用全部 100 个 case 的所有时序行拟合 ARX 模型
echo "Training ARX ROM -> ${MODEL_DIR}"
"${PYTHON_BIN}" -m flow_control.cli.train_rom \
  --dataset-dir "${TRAIN_DIR}" \
  --out "${MODEL_DIR}" \
  --input-lags 2 \
  --output-lags 3 \
  --ridge-alpha 1.0

echo
echo "Done. ROM model:"
echo "${MODEL_DIR}/arx_model.json"
echo "${MODEL_DIR}/training_summary.json"
