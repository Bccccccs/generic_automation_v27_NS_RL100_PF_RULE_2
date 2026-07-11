#!/usr/bin/env bash
# ==============================================================================
# run_ccm_from_action.sh — 选择一种激励模式 → 生成计划 → 启动 STAR-CCM+ 仿真。
#
# 工作流程：
#   1. 列出 6 种激励模式供用户选择（输入 1-6）
#   2. 从 configs/ccm_runtime.yaml 读取 CCM 配置（.sim 路径、核心数等）
#   3. 生成激励计划并通过 run_starccm CLI 启动 STAR-CCM+
#   4. 运行结束后打包 CCM 结果为标准 case 目录
#
# 前置条件：
#   - STAR-CCM+ 已安装且许可证可用
#   - configs/ccm_runtime.yaml 已正确配置
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

# 辅助函数：从 configs/ccm_runtime.yaml 读取 CCM 配置
# 使用嵌入式 Python 脚本解析 YAML，支持点分键路径（如 "ccm.sim_path"）
ccm_config_value() {
  "${PYTHON_BIN}" - "$1" "$2" <<'PY'
from pathlib import Path
import sys
import yaml

key = sys.argv[1]
allow_empty = sys.argv[2] == "allow-empty"
path = Path("configs/ccm_runtime.yaml")
if not path.exists():
    raise SystemExit("CCM 配置文件不存在：configs/ccm_runtime.yaml")
data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
value = data or {}
for part in key.split("."):
    value = value.get(part) if isinstance(value, dict) else None
if value is None or (value == "" and not allow_empty):
    raise SystemExit(f"CCM 配置缺失或为空：{key}")
if value is None:
    value = ""
print(value)
PY
}

# 6 种激励模式的名称列表
ACTION_NAMES=(
  "no_jet_reference"
  "pulse_singlejet"
  "step_singlejet"
  "chirp_keyjets"
  "prbs_demo"
  "pilot_sparse24"
)

# 带中文说明的选项标签
ACTION_LABELS=(
  "1) no_jet_reference  - 无喷气参考段"
  "2) pulse_singlejet   - 单喷气脉冲"
  "3) step_singlejet    - 单喷气阶跃"
  "4) chirp_keyjets     - 关键喷气区扫频"
  "5) prbs_demo         - PRBS 伪随机开关"
  "6) pilot_sparse24    - 稀疏随机分组"
)

# 用户交互：选择要运行的激励模式
echo "请选择要生成并启动 CCM 的动作，输入数字 1-6："
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

sim_path="$(ccm_config_value ccm.sim_path required)"
num_cores="$(ccm_config_value ccm.num_cores required)"
region_name="$(ccm_config_value ccm.region required)"
starccm_path="$(ccm_config_value ccm.starccm_path required)"
podkey="$(ccm_config_value ccm.podkey allow-empty)"

if [[ -z "${sim_path}" ]]; then
  echo ".sim 路径不能为空。"
  exit 1
fi

if [[ ! -f "${sim_path}" ]]; then
  echo ".sim 文件不存在：${sim_path}"
  exit 1
fi

action_name="${ACTION_NAMES[$((choice - 1))]}"
config_path="configs/actions/${action_name}.yaml"
output_dir="runs/starccm_${action_name}"

echo
echo "CCM config: sim=${sim_path}, starccm=${starccm_path}, np=${num_cores}, region=${region_name}"
echo "Generating schedule and launching CCM: ${action_name} -> ${output_dir}"
cmd=(
  "${PYTHON_BIN}" -m flow_control.cli.run_starccm
  --actuation-config "${config_path}" \
  --sim "${sim_path}" \
  --out "${output_dir}" \
  --starccm-path "${starccm_path}" \
  --np "${num_cores}" \
  --region "${region_name}"
)

if [[ -n "${podkey}" ]]; then
  cmd+=(--podkey "${podkey}")
fi

"${cmd[@]}"

echo
echo "Done. CCM outputs:"
echo "${output_dir}/input/actuation_schedule.csv"
echo "${output_dir}/FlowControlRunMacro.java"
echo "${output_dir}/starccm_runtime_plan.json"
echo "${output_dir}/starccm_flow_control.log"
echo "${output_dir}/flow_control_timeseries.csv"
echo "${output_dir}/timeseries.csv"
echo "${output_dir}/quality_report.json"
