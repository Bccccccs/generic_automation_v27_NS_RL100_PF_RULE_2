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

if [[ "${case_dir}" != runs/* ]]; then
  case_dir="runs/${case_dir}"
fi

"${PYTHON_BIN}" -m flow_control.star_ingest.step2_check_case \
  --case-dir "${case_dir}" \
  --check-mode ccm

echo
echo "Step 2 done. Next:"
echo "bash examples/run_ccm_ingest_step3_figures.sh"
