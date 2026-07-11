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

if [[ "${case_dir}" != runs/* ]]; then
  case_dir="runs/${case_dir}"
fi

"${PYTHON_BIN}" -m flow_control.star_ingest.step3_generate_figures \
  --case-dir "${case_dir}"

echo
echo "Step 3 done. Figures:"
echo "${case_dir}/figures"
