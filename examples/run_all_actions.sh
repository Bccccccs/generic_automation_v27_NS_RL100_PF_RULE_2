#!/usr/bin/env bash
# ==============================================================================
# run_all_actions.sh — 一键生成所有 6 种激励模式的激励计划 CSV。
#
# 功能：依次为所有激励模式生成 actuation_schedule.csv、
#       激活热图 SVG、总质量流量曲线等文件到 runs/<模式名>/input/ 下。
#
# 生成的模式：
#   1. no_jet_reference  — 无喷气参考段
#   2. pulse_singlejet   — 单喷气脉冲
#   3. step_singlejet    — 单喷气阶跃
#   4. chirp_keyjets     — 关键喷气区扫频
#   5. prbs_demo         — PRBS 伪随机开关
#   6. pilot_sparse24    — 稀疏随机分组
# ==============================================================================
set -euo pipefail

# 获取脚本所在目录和项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

# 如果虚拟环境的 Python 不可用，回退到系统 python3
if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

# 6 种激励模式的名称列表
ACTION_NAMES=(
  "no_jet_reference"
  "pulse_singlejet"
  "step_singlejet"
  "chirp_keyjets"
  "prbs_demo"
  "pilot_sparse24"
)

# 遍历所有模式，逐个生成激励计划
for action_name in "${ACTION_NAMES[@]}"; do
  config_path="configs/actions/${action_name}.yaml"
  output_dir="runs/${action_name}"

  echo "Generating ${action_name} -> ${output_dir}"
  "${PYTHON_BIN}" -m flow_control.generator.schedule_generator \
    --config "${config_path}" \
    --output-dir "${output_dir}"
done

echo
echo "All actions generated under runs/<action_name>/input/"
