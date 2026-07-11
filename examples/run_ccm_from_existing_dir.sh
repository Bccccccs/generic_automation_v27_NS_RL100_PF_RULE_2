#!/usr/bin/env bash
# ==============================================================================
# run_ccm_from_existing_dir.sh — 从已有激励计划目录启动 STAR-CCM+ 仿真。
#
# 适用场景：
#   已通过 run_all_actions.sh 或其他方式生成了激励计划 CSV，
#   需要在此基础上启动 STAR-CCM+ 进行 CFD 仿真。
#
# 工作流程：
#   1. 扫描 runs/ 下列出已有目录供用户选择
#   2. 从 configs/ccm_runtime.yaml 读取 CCM 配置
#   3. 使用选中的目录中的 actuation_schedule.csv 启动 STAR-CCM+
#   4. 运行结果仍输出到所选目录
#
# 前置条件：
#   - 所选目录必须包含 input/actuation_schedule.csv 或 actuation_schedule.csv
#   - STAR-CCM+ 已安装且许可证可用
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

# 列出 runs/ 下现有目录供用户选择
echo "runs 下现有目录："
find runs -mindepth 1 -maxdepth 1 -type d | sort
echo
echo "请输入要启动 CCM 的目录，例如 runs/pulse_singlejet 或 pulse_singlejet："
printf "目录: "
read -r selected_dir

# 输入验证：目录不能为空
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

# 自动查找激励计划 CSV（优先 input/ 下的，再回退到根目录的）
if [[ -f "${selected_dir}/input/actuation_schedule.csv" ]]; then
  schedule_path="${selected_dir}/input/actuation_schedule.csv"
elif [[ -f "${selected_dir}/actuation_schedule.csv" ]]; then
  schedule_path="${selected_dir}/actuation_schedule.csv"
else
  echo "未找到动作表：${selected_dir}/input/actuation_schedule.csv 或 ${selected_dir}/actuation_schedule.csv"
  exit 1
fi

# 从 CCM 配置文件中读取必要参数
sim_path="$(ccm_config_value ccm.sim_path required)"
num_cores="$(ccm_config_value ccm.num_cores required)"
region_name="$(ccm_config_value ccm.region required)"
starccm_path="$(ccm_config_value ccm.starccm_path required)"
podkey="$(ccm_config_value ccm.podkey allow-empty)"

# 验证 .sim 文件存在
if [[ -z "${sim_path}" ]]; then
  echo ".sim 路径不能为空。"
  exit 1
fi

if [[ ! -f "${sim_path}" ]]; then
  echo ".sim 文件不存在：${sim_path}"
  exit 1
fi

# 显示配置并启动 STAR-CCM+
echo
echo "CCM config: sim=${sim_path}, starccm=${starccm_path}, np=${num_cores}, region=${region_name}"
echo "Launching CCM from existing schedule:"
echo "${schedule_path}"
cmd=(
  "${PYTHON_BIN}" -m flow_control.cli.run_starccm
  --schedule "${schedule_path}" \
  --sim "${sim_path}" \
  --out "${selected_dir}" \
  --starccm-path "${starccm_path}" \
  --np "${num_cores}" \
  --region "${region_name}"
)

# 如果配置了 podkey，追加到命令中
if [[ -n "${podkey}" ]]; then
  cmd+=(--podkey "${podkey}")
fi

"${cmd[@]}"

echo
echo "Done. CCM outputs generated in:"
echo "${selected_dir}"
echo "${selected_dir}/actuation_schedule.csv"
echo "${selected_dir}/FlowControlRunMacro.java"
echo "${selected_dir}/starccm_runtime_plan.json"
echo "${selected_dir}/starccm_flow_control.log"
echo "${selected_dir}/flow_control_timeseries.csv"
echo "${selected_dir}/timeseries.csv"
echo "${selected_dir}/quality_report.json"
