#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

CASE_DIRS=()
while IFS= read -r case_dir; do
  CASE_DIRS+=("${case_dir}")
done < <(
  find runs -mindepth 2 -maxdepth 2 -type f -name flow_control_timeseries.csv \
    -print \
    | sed 's#/flow_control_timeseries.csv$##' \
    | sort
)

if [[ "${#CASE_DIRS[@]}" -eq 0 ]]; then
  echo "未找到 CCM 输出目录。需要目录中包含 flow_control_timeseries.csv。"
  exit 1
fi

echo "当前可生成标准 timeseries 的 CCM 目录路径："
printf '%s\n' "${CASE_DIRS[@]}"

echo
echo "请输入要执行的目录路径："
printf "目录: "
read -r case_dir

if [[ -z "${case_dir}" ]]; then
  echo "目录不能为空。"
  exit 1
fi

if [[ "${case_dir}" != runs/* ]]; then
  case_dir="runs/${case_dir}"
fi

if [[ ! -f "${case_dir}/flow_control_timeseries.csv" ]]; then
  echo "未找到 CCM 原始时序：${case_dir}/flow_control_timeseries.csv"
  exit 1
fi

if [[ -f "${case_dir}/input/actuation_schedule.csv" ]]; then
  schedule_path="${case_dir}/input/actuation_schedule.csv"
elif [[ -f "${case_dir}/actuation_schedule.csv" ]]; then
  schedule_path="${case_dir}/actuation_schedule.csv"
else
  echo "未找到动作表：${case_dir}/input/actuation_schedule.csv 或 ${case_dir}/actuation_schedule.csv"
  exit 1
fi

"${PYTHON_BIN}" - "${case_dir}" "${schedule_path}" <<'PY'
from pathlib import Path
import sys

from flow_control.star_ingest.ccm_package import package_ccm_run_case

case_dir = Path(sys.argv[1])
schedule_path = Path(sys.argv[2])
result = package_ccm_run_case(
    ccm_timeseries_path=case_dir / "flow_control_timeseries.csv",
    schedule_path=schedule_path,
    case_dir=case_dir,
    run_quality_check=False,
)
print(f"generated standard timeseries: {result['timeseries_path']}")
print(f"quality report placeholder: {result['quality_report_path']}")
PY

echo
echo "Step 1 done. Next:"
echo "bash examples/run_ccm_ingest_step2_check.sh"
