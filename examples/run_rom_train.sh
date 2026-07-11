#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PROJECT_ROOT}/.venv/bin/python"

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="python3"
fi

cd "${PROJECT_ROOT}"

TRAIN_DIR="runs/arx/training"
MODEL_DIR="runs/arx/model"

echo "Generating ROM training dataset -> ${TRAIN_DIR}"
"${PYTHON_BIN}" -m flow_control.rom.generate_arx_dataset \
  --actuation-config configs/actions/pilot_sparse24.yaml \
  --mock-config configs/mock_dynamic24x6.yaml \
  --out "${TRAIN_DIR}" \
  --count 100 \
  --overwrite

echo
echo "Training ARX ROM -> ${MODEL_DIR}"
"${PYTHON_BIN}" -m flow_control.cli.train_rom \
  --dataset-dir "${TRAIN_DIR}" \
  --out "${MODEL_DIR}" \
  --input-lags 2 \
  --output-lags 3 \
  --ridge-alpha 1.0

echo
echo "Done. ROM model:"
echo "${MODEL_DIR}/arx_model.json"
echo "${MODEL_DIR}/training_summary.json"
