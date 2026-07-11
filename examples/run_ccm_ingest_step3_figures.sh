#!/usr/bin/env bash
# ==============================================================================
# run_ccm_ingest_step3_figures.sh — CCM 结果导入 Step 3：生成诊断图。
#
# 3 步 CCM 导入工作流的第 3 步：
#   根据 Step 2 验证通过的标准 case 数据生成诊断图。
#
# 生成的图：
#   - force_timeseries.png    — Fz 传感器力与全局量时程
#   - jet_schedule.png        — 喷气激活热图（jet case）
#   - massflow_check.png      — 指令 vs 实际质量流量对比（jet case）
#   - quality_summary.png     — 质量检查摘要卡片
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

# 自动发现所有包含 quality_report.json 的目录（已执行 Step 2 的目录）
CASE_DIRS=()
while IFS= read -r case_dir; do
  CASE_DIRS+=("${case_dir}")
done < <(
  find runs -mindepth 2 -maxdepth 2 -type f -name quality_report.json \
    -print \
    | sed 's#/quality_report.json$##' \
    | sort
)

if [[ "${#CASE_DIRS[@]}" -eq 0 ]]; then
  echo "未找到可生成图片目录。需要目录中包含 quality_report.json。"
  exit 1
fi

echo "当前可生成图片目录路径："
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

# 生成诊断图
"${PYTHON_BIN}" -m flow_control.star_ingest.step3_generate_figures \
  --case-dir "${case_dir}"

echo
echo "Step 3 done. Figures:"
echo "${case_dir}/figures"
