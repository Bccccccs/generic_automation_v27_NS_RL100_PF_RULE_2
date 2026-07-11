#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

VALID_DIR="runs/arx/vaild"
MODEL_PATH="runs/arx/model/arx_model.json"
VALIDATION_OUT="runs/arx/validation"

if [[ ! -f "${MODEL_PATH}" ]]; then
  echo "模型不存在：${MODEL_PATH}"
  echo "请先运行：bash examples/run_rom_train.sh"
  exit 1
fi

echo "Generating ROM validation dataset -> ${VALID_DIR}"
"${PYTHON_BIN}" -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out "${VALID_DIR}" \
  --count 10 \
  --start-seed 20260718 \
  --overwrite

echo
echo "Validating ARX ROM -> ${VALIDATION_OUT}"
"${PYTHON_BIN}" -m flow_control.cli.validate_rom \
  --model "${MODEL_PATH}" \
  --dataset-dir "${VALID_DIR}" \
  --out "${VALIDATION_OUT}"

echo
echo "Done. Validation outputs:"
echo "${VALIDATION_OUT}/metrics.json"
echo "${VALIDATION_OUT}/prediction_timeseries.csv"
echo "${VALIDATION_OUT}/prediction_6_load_cells.svg"
echo "${VALIDATION_OUT}/error_6_load_cells.svg"
echo "${VALIDATION_OUT}/rmse_bar.svg"
