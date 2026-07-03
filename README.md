# generic_automation / maglev_sparse_jet_9w

STAR-CCM+ automation for single cases, simple parameter sweeps, online solver
parameter control, and a local sparse-jet flow-control prototype.

## Quick start from a fresh clone

The local flow-control examples and tests do not require STAR-CCM+.

```bash
git clone <repo-url>
cd generic_automation_v27_NS_RL100_PF_RULE_2

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

python -m pytest
```

Run the constrained 24-jet, 80-window actuation generator:

```bash
python -m flow_control.schedule_generator \
  --config configs/pilot_sparse24.yaml \
  --output-dir runs/pilot_sparse24_readme
```

Run the 24-input, 6-output mock plant rollout and write a standard case bundle:

```bash
python -m flow_control.run_mock_demo \
  --config configs/pilot_sparse24.yaml \
  --output-dir runs/b04_mock_plant_readme
```

Expected local outputs include `actuation_schedule.csv`, `timeseries.csv`,
`quality_report.json`, `figures/`, `logs/`, and `flow_snapshots/` under the
chosen `runs/...` directory.

## B02 sparse-jet project branch

This workspace now includes an isolated Week 1 B02 prototype branch:

- Git branch: `maglev_sparse_jet_9w`
- New flow-control module: `flow_control/`
- First baseline config: `configs/maglev_sparse_jet_9w.yaml`
- Constrained 24x80 actuation config: `configs/pilot_sparse24.yaml`
- Legacy mock example: `examples/run_mock_flow_control.py`
- 24-input/6-output mock plant demo: `python -m flow_control.run_mock_demo`
- Runtime output target: `runs/`

The existing solver optimization code under `generic_automation/` is preserved and
kept separate from the new `flow_control/` prototype.

## Project layout

- `generic_automation/`: Python package for the core automation code.
- `generic_automation/cli/`: CLI implementations used by root wrapper scripts.
- `generic_automation/core/`: configuration parsing, case model, runtime metadata, value utilities.
- `generic_automation/adapters/`: simulation backend adapter layer.
- `generic_automation/starccm/`: STAR-CCM+ macro generation, log parsing, result collection, solver profiling.
- `generic_automation/monitor/`: online monitor, parameter write-back, observation/action output records.
- `generic_automation/rl/`: RL controller, action space, state construction, reward, safety rules.
- `flow_control/`: first sparse-jet schedule generation, validation, mock plant, schema, and analysis modules.
- `starccm_control/`: shared STAR-CCM+ jet/load naming contract and result mapper.
- `starccm_runtime/`: runtime translators and helpers for STAR-CCM+ flow-control integration.
- `scripts/`: compatibility entrypoint wrappers and operational shell pipelines.
- `configs/`: YAML configuration files.
- `cases/`: sweep input CSV files.
- `docs/`: project notes, environment setup, audit reports, and restructure notes.
- `examples/`: small runnable examples for new workflows.
- `tests/`: smoke tests and future regression tests.
- `runs/`: local run outputs; generated contents are ignored by Git.
- `logs/`: archived local launcher/SLURM logs.
- `results/` and `results_validation/`: generated or historical case outputs.
- `archive/legacy/`: archived legacy project snapshots.

The root-level `ga.py` is the preferred unified launcher. Historical script names
are kept under `scripts/entrypoints/` as compatibility wrappers.

## Available configs

This repository keeps these supported configuration files:

- `configs/config.yaml`: generic default configuration
- `configs/config_rl_build_amg_match_mesh.yaml`: RL configuration aligned as closely as possible with `build_AMG.java` and `build_base_3.java`
- `configs/maglev_sparse_jet_9w.yaml`: first sparse-jet flow-control prototype configuration
- `configs/pilot_sparse24.yaml`: constrained 24-jet, 80-window actuation and mock-plant configuration

## Run tests

Use `python -m pytest` from the repository root so Python sees the local packages
exactly as the examples do:

```bash
python -m pytest
```

## Run local flow-control workflows

Generate the current constrained sparse actuation schedule:

```bash
python -m flow_control.schedule_generator \
  --config configs/pilot_sparse24.yaml \
  --output-dir runs/pilot_sparse24
```

This schedule uses 24 jet columns, 72 excitation windows, 8 reference windows,
3 active jets per excitation window, 9 activations per jet, unique excitation
combinations, and the configured consecutive-on limit.

Run the current virtual CFD/mock plant workflow:

```bash
python -m flow_control.run_mock_demo \
  --config configs/pilot_sparse24.yaml \
  --output-dir runs/b04_mock_plant
```

The mock plant is fixed to 24 inputs and 6 outputs. The standard `timeseries.csv`
uses the same load/output column names expected from the STAR-CCM+ mapper:
`Fz_S1L`, `Fz_S1R`, `Fz_S2L`, `Fz_S2R`, `Fz_S3L`, `Fz_S3R`,
`Fz_Total`, `Drag_Total`, `Pitch_Moment`, `Roll_Moment`,
`Jet_Reaction_Z`, and `solver_status`.

Run the older B02 baseline mock workflow:

```bash
python examples/run_mock_flow_control.py
```

Or exercise the legacy schedule branch directly:

```bash
python -m flow_control.schedule_generator --config configs/maglev_sparse_jet_9w.yaml
```

## Run a single case

The commands in this section require a working STAR-CCM+ installation and valid
`.sim` paths in the selected config.

```bash
python ga.py case --config configs/config.yaml
```

This now starts an embedded RL monitor automatically when
`ai_optimization.enabled: true`. Use `--no-monitor` if you want a pure solver run
or if another monitor process will attach externally.

Or run the build-matched RL case:

```bash
python ga.py case --config configs/config_rl_build_amg_match_mesh.yaml
```

For online RL-controlled runs that should react to native STAR-CCM+ iteration output:

```bash
python ga.py monitor --config configs/config.yaml
```

Or:

```bash
python ga.py monitor --config configs/config_rl_build_amg_match_mesh.yaml
```

## Run modes

The case config now supports four execution modes through `case.run_mode`:

- `full_run`: template `.sim` -> boundary/setup -> mesh -> solver -> export
- `mesh_only`: template `.sim` -> boundary/setup -> mesh -> save `*_mesh_ready_<hash>.sim`
- `solve_only`: open `case.input_sim` (usually a mesh-ready `.sim`) -> solver/report/RL setup -> solve
- `resume`: open `case.input_sim` (usually a checkpoint `.sim`) -> refresh runtime controls -> continue solving

Example:

```yaml
case:
  run_mode: solve_only
  input_sim: /path/to/case_mesh_ready_abc123.sim
  checkpoint_interval: 250
```

You can also override the config from the CLI:

```bash
python ga.py case --config configs/config.yaml --run-mode solve_only --input-sim /path/to/mesh_ready.sim
```

Relative `input_sim` and `template_sim` values from config files are resolved
relative to the config file directory. CLI `--input-sim` overrides are
normalized to absolute paths from the current shell.

## Run a sweep

```bash
python ga.py sweep --config configs/config.yaml --cases cases/cases.csv
```

## Minimal config

Update `configs/config.yaml` before running STAR-CCM+ workflows:

```yaml
adapter: starccm
starccm_path: /opt/STAR-CCM+/star/bin/starccm+
template_sim: /path/to/template.sim
num_cores: 128
```

## Required case fields

| Field | Unit | Notes |
| --- | --- | --- |
| `inlet_velocity` | m/s | inlet speed |
| `inlet_temperature` | K | inlet temperature |
| `outlet_pressure` | Pa | outlet static pressure |
| `base_mesh_size` | m | base mesh size |


In reinforcement-learning mode, the default observation data are:

- `drag`
- `train_surface_pressure_max`

The RL controller currently only adjusts these safe solver parameters:

- `pressure_relaxation_factor`
- `pressure_relaxation_initial_value`
- `pressure_relaxation_end_iteration`
- `pressure_amg_cycle` (`0` = prefer V-cycle, `1` = prefer W-cycle)
- `velocity_amg_cycle` (`0` = prefer Flex-cycle, `1` = prefer V-cycle)


Example:

```yaml
case:
  report_names: [drag, total, train_surface_pressure_max]
  total_report_name: total
  pressure_relaxation_factor: 0.30
  pressure_relaxation_initial_value: 0.07
  pressure_relaxation_end_iteration: 10
  pressure_amg_cycle: 0
  velocity_amg_cycle: 0

ai_optimization:
  enabled: true
  controller: reinforcement_learning
  decision_interval_iterations: 30
  reinforcement_learning:
    intervention_enabled: true
    baseline_final_total: 1234.56
    allowed_parameters:
      - pressure_relaxation_factor
      - pressure_relaxation_initial_value
      - pressure_relaxation_end_iteration
      - pressure_amg_cycle
      - velocity_amg_cycle
    manual_rules:
      enabled: true
```
