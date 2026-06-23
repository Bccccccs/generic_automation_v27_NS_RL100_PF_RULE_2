#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_PATH="${1:-$SCRIPT_DIR/configs/config.yaml}"
PYTHON_BIN="${PYTHON_BIN:-$HOME/miniconda3/bin/python}"
SBATCH_BIN="${SBATCH_BIN:-sbatch}"
MONITOR_SCRIPT="$SCRIPT_DIR/run_monitor_only.py"
SBATCH_JOB_NAME="${SBATCH_JOB_NAME:-starccm+}"
SBATCH_PARTITION="${SBATCH_PARTITION:-xahcnormal}"
SBATCH_NODES="${SBATCH_NODES:-2}"
SBATCH_TASKS_PER_NODE="${SBATCH_TASKS_PER_NODE:-64}"
SQUEUE_BIN="${SQUEUE_BIN:-squeue}"

if [ ! -f "$CONFIG_PATH" ]; then
  echo "[Launcher] ERROR: config not found: $CONFIG_PATH" >&2
  exit 1
fi

if [ ! -x "$PYTHON_BIN" ]; then
  echo "[Launcher] ERROR: python not executable: $PYTHON_BIN" >&2
  exit 1
fi

cd "$SCRIPT_DIR"

CASE_DIR="$("$PYTHON_BIN" - <<'PY' "$CONFIG_PATH"
from pathlib import Path
import sys
import yaml

config_path = Path(sys.argv[1]).resolve()
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
result_root = Path(str(cfg.get("result_root", "results")))
if not result_root.is_absolute():
    result_root = (config_path.parent / result_root).resolve()
case_name = str(cfg.get("case_name", "case"))
print((result_root / case_name).resolve())
PY
)"

MONITOR_ENABLED="$("$PYTHON_BIN" - <<'PY' "$CONFIG_PATH"
from pathlib import Path
import sys
import yaml

config_path = Path(sys.argv[1]).resolve()
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
ai_cfg = cfg.get("ai_optimization") or {}
profiling_enabled = ai_cfg.get("profiling_enabled", True)
if isinstance(profiling_enabled, str):
    profiling_enabled = profiling_enabled.strip().lower() in {"1", "true", "yes", "on"}
print("true" if profiling_enabled else "false")
PY
)"

if [ "$MONITOR_ENABLED" = "true" ] && [ ! -f "$MONITOR_SCRIPT" ]; then
  echo "[Launcher] ERROR: monitor script not found: $MONITOR_SCRIPT" >&2
  exit 1
fi

DONE_FLAG="$CASE_DIR/sim_done.flag"

echo "[Launcher] config:   $CONFIG_PATH"
echo "[Launcher] case_dir: $CASE_DIR"
echo "[Launcher] profiling: enabled=$MONITOR_ENABLED"
echo "[Launcher] sbatch:   job=$SBATCH_JOB_NAME partition=$SBATCH_PARTITION nodes=$SBATCH_NODES ntasks-per-node=$SBATCH_TASKS_PER_NODE"
echo

if [ -e "$DONE_FLAG" ]; then
  echo "[Launcher] ERROR: existing sim_done.flag detected: $DONE_FLAG" >&2
  echo "[Launcher] Remove the old flag or use a fresh case_name before launching the full pipeline." >&2
  exit 1
fi

SBATCH_OUT="$(
  "$SBATCH_BIN" \
    --job-name="$SBATCH_JOB_NAME" \
    --partition="$SBATCH_PARTITION" \
    --nodes="$SBATCH_NODES" \
    --ntasks-per-node="$SBATCH_TASKS_PER_NODE" \
    --export=ALL,CONFIG_PATH="$CONFIG_PATH",SCRIPT_DIR="$SCRIPT_DIR",PYTHON_BIN="$PYTHON_BIN" \
    <<'SBATCH_EOF'
#!/bin/bash
set -euo pipefail

cd "$SCRIPT_DIR"

source ~/apprepo/starccmplus/17.06.007-none/scripts/env.sh

srun hostname | sort | uniq -c | awk '{print $2}' > "$SCRIPT_DIR/logs/hostfile"

export STARCCM_NP="${NP:-${SLURM_NTASKS:-}}"
export STARCCM_MACHINEFILE="${HOSTFILE:-$SCRIPT_DIR/logs/hostfile}"
export STARCCM_MPIDRIVER=intel

sed -i "s/num_cores:.*/num_cores: ${SLURM_NTASKS}/g" "$CONFIG_PATH"

"$PYTHON_BIN" run_case.py --config "$CONFIG_PATH" --no-monitor

"$PYTHON_BIN" - <<'PYTHON_EOF'
import os
import pathlib
import yaml

config_path = pathlib.Path(os.environ["CONFIG_PATH"]).resolve()
cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
result_root = pathlib.Path(str(cfg.get("result_root", "results")))
if not result_root.is_absolute():
    result_root = (config_path.parent / result_root).resolve()
case_dir = result_root / str(cfg.get("case_name", "case"))
case_dir.mkdir(parents=True, exist_ok=True)
(case_dir / "sim_done.flag").touch()
print("[SLURM] sim_done.flag written to", case_dir, flush=True)
PYTHON_EOF
SBATCH_EOF
)"
echo "[Launcher] $SBATCH_OUT"
SBATCH_JOB_ID="$(printf '%s\n' "$SBATCH_OUT" | grep -Eo '[0-9]+' | tail -1 || true)"

if [ "$MONITOR_ENABLED" != "true" ]; then
  echo "[Launcher] ai_optimization.profiling_enabled=false, skipping login-node profiling monitor."
  if [ -n "$SBATCH_JOB_ID" ] && command -v "$SQUEUE_BIN" >/dev/null 2>&1; then
    echo "[Launcher] Waiting for SLURM job $SBATCH_JOB_ID to finish..."
    while true; do
      JOB_STATE="$("$SQUEUE_BIN" -h -j "$SBATCH_JOB_ID" 2>/dev/null || true)"
      if [ -z "$JOB_STATE" ]; then
        break
      fi
      sleep 30
    done
  fi

  if [ -e "$DONE_FLAG" ]; then
    rm -f \
      "$DONE_FLAG" \
      "$CASE_DIR/param_update.json" \
      "$CASE_DIR/pending_action.json" \
      "$CASE_DIR/param_update_ack.json"
    echo "[Launcher] Cleaned runtime coordination files from $CASE_DIR"
  fi
  exit 0
fi

echo "[Launcher] Starting login-node AI monitor..."
echo

"$PYTHON_BIN" "$MONITOR_SCRIPT" --config "$CONFIG_PATH" --case-dir "$CASE_DIR"
MONITOR_STATUS=$?

if [ "$MONITOR_STATUS" -eq 0 ] && [ -e "$DONE_FLAG" ]; then
  rm -f \
    "$DONE_FLAG" \
    "$CASE_DIR/param_update.json" \
    "$CASE_DIR/pending_action.json" \
    "$CASE_DIR/param_update_ack.json"
  echo "[Launcher] Cleaned runtime coordination files from $CASE_DIR"
fi

exit "$MONITOR_STATUS"
