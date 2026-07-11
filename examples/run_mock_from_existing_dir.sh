#!/usr/bin/env bash
# ==============================================================================
# run_mock_from_existing_dir.sh — 从已有激励计划目录运行 Mock 模拟。
#
# 适用场景：
#   已有激励计划 CSV（通过 run_all_actions.sh 或手动生成），
#   只需要运行 MockDynamicPlant24x6 获取模拟结果。
#
# 工作流程：
#   1. 扫描 runs/ 下列出已有目录供用户选择
#   2. 自动查找目录中的 actuation_schedule.csv
#   3. 运行 MockDynamicPlant24x6 模拟
#   4. 输出写入所选目录（不创建新目录）
#
# 前置条件：
#   所选目录必须包含 input/actuation_schedule.csv 或 actuation_schedule.csv
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

# 列出 runs/ 下现有目录供用户选择
echo "runs 下现有目录："
find runs -mindepth 1 -maxdepth 1 -type d | sort
echo
echo "请输入要运行 mock 的目录，例如 runs/pulse_singlejet 或 pulse_singlejet："
printf "目录: "
read -r selected_dir

# 输入验证
if [[ -z "${selected_dir}" ]]; then
  echo "目录不能为空。"
  exit 1
fi

# 自动补全 runs/ 前缀
if [[ "${selected_dir}" != runs/* ]]; then
  selected_dir="runs/${selected_dir}"
fi

# 验证目录存在
if [[ ! -d "${selected_dir}" ]]; then
  echo "目录不存在：${selected_dir}"
  exit 1
fi

# 自动查找激励计划 CSV
if [[ -f "${selected_dir}/input/actuation_schedule.csv" ]]; then
  schedule_path="${selected_dir}/input/actuation_schedule.csv"
elif [[ -f "${selected_dir}/actuation_schedule.csv" ]]; then
  schedule_path="${selected_dir}/actuation_schedule.csv"
else
  echo "未找到动作表：${selected_dir}/input/actuation_schedule.csv 或 ${selected_dir}/actuation_schedule.csv"
  exit 1
fi

echo
echo "Running mock from existing schedule:"
echo "${schedule_path}"
# 使用已有激励计划运行 MockDynamicPlant24x6
"${PYTHON_BIN}" -m flow_control.cli.run_mock_dynamic24x6 \
  --schedule "${schedule_path}" \
  --config configs/mock_dynamic24x6.yaml \
  --out "${selected_dir}"

echo
echo "Done. Mock outputs generated in:"
echo "${selected_dir}"
