#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

echo "runs 下现有目录："
find runs -mindepth 1 -maxdepth 1 -type d | sort
echo
echo "请输入要运行 mock 的目录，例如 runs/pulse_singlejet 或 pulse_singlejet："
printf "目录: "
read -r selected_dir

if [[ -z "${selected_dir}" ]]; then
  echo "目录不能为空。"
  exit 1
fi

if [[ "${selected_dir}" != runs/* ]]; then
  selected_dir="runs/${selected_dir}"
fi

if [[ ! -d "${selected_dir}" ]]; then
  echo "目录不存在：${selected_dir}"
  exit 1
fi

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
"${PYTHON_BIN}" -m flow_control.cli.run_mock_dynamic24x6 \
  --schedule "${schedule_path}" \
  --config configs/mock_dynamic24x6.yaml \
  --out "${selected_dir}"

echo
echo "Done. Mock outputs generated in:"
echo "${selected_dir}"
