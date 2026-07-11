#!/usr/bin/env bash
# ==============================================================================
# run_one_action.sh — 选择一种激励模式，然后选择运行方式（Mock 或 CCM）。
#
# 这是推荐的"快速开始"脚本：只需选择模式编号和运行方式，
# 即可生成激励计划并运行模拟。
#
# 两种运行方式：
#   1. Mock 模式 — 使用 MockDynamicPlant24x6（无需 CCM 许可证）
#   2. CCM 模式 — 使用真实的 STAR-CCM+ 仿真
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

# 6 种激励模式的名称列表
ACTION_NAMES=(
  "no_jet_reference"
  "pulse_singlejet"
  "step_singlejet"
  "chirp_keyjets"
  "prbs_demo"
  "pilot_sparse24"
)

# 带中文说明的选项标签（供用户选择）
ACTION_LABELS=(
  "1) no_jet_reference  - 无喷气参考段"
  "2) pulse_singlejet   - 单喷气脉冲"
  "3) step_singlejet    - 单喷气阶跃"
  "4) chirp_keyjets     - 关键喷气区扫频"
  "5) prbs_demo         - PRBS 伪随机开关"
  "6) pilot_sparse24    - 稀疏随机分组"
)

# 运行方式选项
MODE_LABELS=(
  "1) mock  — 使用 MockDynamicPlant24x6（免 CCM 许可，适合算法开发）"
  "2) ccm   — 使用 STAR-CCM+（需要 CCM 许可和已配置的 .sim 文件）"
)

# 用户交互第 1 步：选择激励模式
echo "选择要生成的激励模式，输入数字 1-6："
printf '%s\n' "${ACTION_LABELS[@]}"
printf "模式编号: "
read -r action_choice

case "${action_choice}" in
  1|2|3|4|5|6) ;;
  *)
    echo "无效输入：${action_choice}。请输入 1-6。"
    exit 1
    ;;
esac

action_name="${ACTION_NAMES[$((action_choice - 1))]}"

# 用户交互第 2 步：选择运行方式
echo
echo "选择运行方式，输入数字 1 或 2："
printf '%s\n' "${MODE_LABELS[@]}"
printf "方式编号: "
read -r mode_choice

case "${mode_choice}" in
  1)
    # Mock 模式：生成计划 + 运行 MockDynamicPlant24x6
    output_dir="runs/mock_${action_name}"
    echo
    echo "Generating schedule and running mock: ${action_name} -> ${output_dir}"
    "${PYTHON_BIN}" -m flow_control.cli.run_mock_dynamic24x6 \
      --actuation-config "configs/actions/${action_name}.yaml" \
      --config configs/mock_dynamic24x6.yaml \
      --out "${output_dir}"
    echo
    echo "Mock outputs in: ${output_dir}"
    ;;
  2)
    # CCM 模式：生成计划 + 启动 STAR-CCM+ 仿真
    # 注意：需要 configs/ccm_runtime.yaml 有效配置
    output_dir="runs/starccm_${action_name}"
    echo
    echo "Generating schedule and launching CCM: ${action_name} -> ${output_dir}"
    echo "（确保 configs/ccm_runtime.yaml 配置正确）"
    "${PYTHON_BIN}" -m flow_control.cli.run_starccm \
      --actuation-config "configs/actions/${action_name}.yaml" \
      --sim "$(python3 -c "import yaml; d=yaml.safe_load(open('configs/ccm_runtime.yaml')); print(d['ccm']['sim_path'])" 2>/dev/null || echo '请输入.sim路径')" \
      --out "${output_dir}"
    echo
    echo "CCM outputs in: ${output_dir}"
    ;;
  *)
    echo "无效输入：${mode_choice}。请输入 1 或 2。"
    exit 1
    ;;
esac
