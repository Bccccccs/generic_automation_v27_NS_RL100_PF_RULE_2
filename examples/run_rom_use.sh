#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

MODEL_PATH="runs/arx/model/arx_model.json"

if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "模型不存在：${MODEL_PATH}"
  echo "请先运行：bash examples/run_rom_train.sh"
  exit 1
fi

CASE_DIRS=()
while IFS= read -r case_dir; do
  CASE_DIRS+=("${case_dir}")
done < <(
  find runs -type f -name timeseries.csv \
    -not -path "runs/arx/model/*" \
    -not -path "runs/arx/validation/*" \
    -not -path "runs/arx/use_*/*" \
    -print \
    | sed 's#/timeseries.csv$##' \
    | sort
)

if [[ "${#CASE_DIRS[@]}" -eq 0 ]]; then
  echo "未找到可用 case 目录。需要目录中包含 timeseries.csv。"
  exit 1
fi

echo "当前可用于 ROM 的目录："
for idx in "${!CASE_DIRS[@]}"; do
  printf "%d) %s\n" "$((idx + 1))" "${CASE_DIRS[$idx]}"
done

echo
echo "请输入目录编号，或直接输入目录路径："
printf "目录: "
read -r selection

if [[ -z "${selection}" ]]; then
  echo "目录不能为空。"
  exit 1
fi

if [[ "${selection}" =~ ^[0-9]+$ ]]; then
  if (( selection < 1 || selection > ${#CASE_DIRS[@]} )); then
    echo "无效编号：${selection}"
    exit 1
  fi
  case_dir="${CASE_DIRS[$((selection - 1))]}"
else
  case_dir="${selection}"
  if [[ "${case_dir}" != runs/* ]]; then
    case_dir="runs/${case_dir}"
  fi
fi

if [[ ! -d "${case_dir}" ]]; then
  echo "目录不存在：${case_dir}"
  exit 1
fi

if [[ ! -f "${case_dir}/timeseries.csv" ]]; then
  echo "目录缺少 timeseries.csv：${case_dir}"
  exit 1
fi

safe_name="$(echo "${case_dir#runs/}" | tr '/ ' '__')"
out_dir="runs/arx/use_${safe_name}"

echo
echo "Using ARX ROM on ${case_dir} -> ${out_dir}"
"${PYTHON_BIN}" -m flow_control.cli.use_rom \
  --model "${MODEL_PATH}" \
  --case-dir "${case_dir}" \
  --out "${out_dir}"

echo
echo "Done. ROM prediction outputs:"
echo "${out_dir}/timeseries.csv"
echo "${out_dir}/quality_report.json"
