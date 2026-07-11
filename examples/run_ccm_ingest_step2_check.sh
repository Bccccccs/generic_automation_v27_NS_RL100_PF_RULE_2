#!/usr/bin/env bash
# ==============================================================================
# run_ccm_ingest_step2_check.sh — CCM 结果导入 Step 2：质量检查。
#
# 3 步 CCM 导入工作流的第 2 步：
#   对 Step 1 生成的标准 case 目录执行质量检查，生成 quality_report.json。
#
# 检查内容：
#   - 必选列完整性
#   - 时间序列单调性
#   - NaN/缺失值检测
#   - 喷气开关与质量流量一致性（jet case）
#   - 指令 vs 实际质量流量分离（jet case）
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

# 自动发现所有包含 timeseries.csv 的目录（已执行 Step 1 的目录）
CASE_DIRS=()
while IFS= read -r case_dir; do
  CASE_DIRS+=("${case_dir}")
done < <(
  find runs -mindepth 2 -maxdepth 2 -type f -name timeseries.csv \
    -print \
    | sed 's#/timeseries.csv$##' \
    | sort
)

if [[ "${#CASE_DIRS[@]}" -eq 0 ]]; then
  echo "未找到可检查目录。需要目录中包含 timeseries.csv。"
  exit 1
fi

echo "当前可检查目录路径："
printf '%s\n' "${CASE_DIRS[@]}"

echo
echo "请输入要执行的目录路径："
printf "目录: "
read -r case_dir

if [[ -z "${case_dir}" ]]; then
  echo "目录不能为空。"
  exit 1
fi

# 自动补全 runs/ 前缀
if [[ "${case_dir}" != runs/* ]]; then
  case_dir="runs/${case_dir}"
fi

# 执行质量检查（使用 CCM 检查模式）
"${PYTHON_BIN}" -m flow_control.star_ingest.step2_check_case \
  --case-dir "${case_dir}" \
  --check-mode ccm

echo
echo "Step 2 done. Next:"
echo "bash examples/run_ccm_ingest_step3_figures.sh"
