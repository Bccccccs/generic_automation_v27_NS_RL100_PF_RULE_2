#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

ACTION_NAMES=(
  "no_jet_reference"
  "pulse_singlejet"
  "step_singlejet"
  "chirp_keyjets"
  "prbs_demo"
  "pilot_sparse24"
)

ACTION_LABELS=(
  "1) no_jet_reference  - 无喷气参考段"
  "2) pulse_singlejet   - 单喷气脉冲"
  "3) step_singlejet    - 单喷气阶跃"
  "4) chirp_keyjets     - 关键喷气区扫频"
  "5) prbs_demo         - PRBS 伪随机开关"
  "6) pilot_sparse24    - 稀疏随机分组"
)

echo "请选择要生成并运行 mock 的动作，输入数字 1-6："
printf '%s\n' "${ACTION_LABELS[@]}"
printf "动作编号: "
read -r choice

case "${choice}" in
  1|2|3|4|5|6) ;;
  *)
    echo "无效输入：${choice}。请输入 1-6。"
    exit 1
    ;;
esac

action_name="${ACTION_NAMES[$((choice - 1))]}"
config_path="configs/actions/${action_name}.yaml"
output_dir="runs/mock_${action_name}"

echo
echo "Generating schedule and running mock: ${action_name} -> ${output_dir}"
"${PYTHON_BIN}" -m flow_control.cli.run_mock_dynamic24x6 \
  --actuation-config "${config_path}" \
  --config configs/mock_dynamic24x6.yaml \
  --out "${output_dir}"

echo
echo "Done. Mock outputs:"
echo "${output_dir}/input/actuation_schedule.csv"
echo "${output_dir}/timeseries.csv"
echo "${output_dir}/quality_report.json"
