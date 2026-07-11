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

for action_name in "${ACTION_NAMES[@]}"; do
  config_path="configs/actions/${action_name}.yaml"
  output_dir="runs/${action_name}"

  echo "Generating ${action_name} -> ${output_dir}"
  "${PYTHON_BIN}" -m flow_control.generator.schedule_generator \
    --config "${config_path}" \
    --output-dir "${output_dir}"
done

echo
echo "All actions generated under runs/<action_name>/input/"
