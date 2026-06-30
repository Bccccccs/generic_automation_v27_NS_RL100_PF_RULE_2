# Scripts

This directory contains command wrappers and operational launchers that are not
part of the core Python package.

## Entrypoints

`scripts/entrypoints/` keeps compatibility wrappers for the old root-level
commands:

- `run_case.py`
- `run_sweep.py`
- `run_monitor_only.py`
- `offline_replay.py`
- `force_param_update.py`

Prefer the unified root launcher for new usage:

```bash
python ga.py case --config configs/config.yaml
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
python ga.py monitor --config configs/config.yaml
python ga.py replay --help
python ga.py force-update --help
```

The wrappers are still useful when an external scheduler or older note expects
the historical script names.

## Pipelines

`scripts/pipelines/` contains multi-step operational shell launchers:

- `run_full_pipeline.sh`

Example:

```bash
bash scripts/pipelines/run_full_pipeline.sh configs/config.yaml
```
