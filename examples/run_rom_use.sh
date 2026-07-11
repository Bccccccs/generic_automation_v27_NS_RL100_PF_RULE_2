#!/usr/bin/env bash
# ==============================================================================
# run_rom_use.sh — 使用已训练的 ARX 模型对新 case 进行递推预测。
#
# 工作流程：
#   1. 加载 runs/arx/model/arx_model.json（由 run_rom_train.sh 生成）
#   2. 自动发现 runs/ 下所有包含 timeseries.csv 的可用 case 目录
#   3. 用户选择要预测的 case
#   4. 前 max_lag 行作为历史预热，之后进行递推预测
#   5. 预测结果写入 runs/arx/use_<case_name>/
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

# 模型文件路径（由 run_rom_train.sh 生成）
MODEL_PATH="runs/arx/model/arx_model.json"

# 检查模型是否存在，若不存在则提示先训练
if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "模型不存在：${MODEL_PATH}"
  echo "请先运行：bash examples/run_rom_train.sh"
  exit 1
fi

# 自动发现所有包含 timeseries.csv 的 case 目录
# 排除模型训练/验证/预测目录本身
CASE_DIRS=()
while IFS= read -r case_dir; do
  CASE_DIRS+=("${case_dir}")
done < <(
  find runs -type f -name timeseries.csv \
    -not -path "runs/arx/model/*" \
    -not -path "runs/arx/validation/*" \
    -not -path "runs/arx/use_*/*" \
    -print \
    | sed 's#/timeseries.csv$##' \
    | sort
)

if [[ "${#CASE_DIRS[@]}" -eq 0 ]]; then
  echo "未找到可用 case 目录。需要目录中包含 timeseries.csv。"
  exit 1
fi

# 列出所有可用 case 目录供用户选择
echo "当前可用于 ROM 的目录："
for idx in "${!CASE_DIRS[@]}"; do
  printf "%d) %s\n" "$((idx + 1))" "${CASE_DIRS[$idx]}"
done

echo
echo "请输入目录编号，或直接输入目录路径："
printf "目录: "
read -r selection

if [[ -z "${selection}" ]]; then
  echo "目录不能为空。"
  exit 1
fi

# 支持编号选择和直接路径输入
if [[ "${selection}" =~ ^[0-9]+$ ]]; then
  if (( selection < 1 || selection > ${#CASE_DIRS[@]} )); then
    echo "无效编号：${selection}"
    exit 1
  fi
  case_dir="${CASE_DIRS[$((selection - 1))]}"
else
  case_dir="${selection}"
  if [[ "${case_dir}" != runs/* ]]; then
    case_dir="runs/${case_dir}"
  fi
fi

# 验证目录和 timeseries.csv 存在
if [[ ! -d "${case_dir}" ]]; then
  echo "目录不存在：${case_dir}"
  exit 1
fi

if [[ ! -f "${case_dir}/timeseries.csv" ]]; then
  echo "目录缺少 timeseries.csv：${case_dir}"
  exit 1
fi

# 生成安全的输出目录名（替换特殊字符）
safe_name="$(echo "${case_dir#runs/}" | tr '/ ' '__')"
out_dir="runs/arx/use_${safe_name}"

echo
echo "Using ARX ROM on ${case_dir} -> ${out_dir}"
# 运行 ARX 模型递推预测
"${PYTHON_BIN}" -m flow_control.cli.use_rom \
  --model "${MODEL_PATH}" \
  --case-dir "${case_dir}" \
  --out "${out_dir}"

echo
echo "Done. ROM prediction outputs:"
echo "${out_dir}/timeseries.csv"
echo "${out_dir}/quality_report.json"
