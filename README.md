# generic_automation / maglev_sparse_jet_9w

STAR-CCM+ automation for single cases, simple parameter sweeps, and online parameter control.

## B02 sparse-jet project branch

This workspace now includes an isolated Week 1 B02 prototype branch:

- Git branch: `maglev_sparse_jet_9w`
- New flow-control module: `flow_control/`
- First config: `configs/maglev_sparse_jet_9w.yaml`
- Mock example: `examples/run_mock_flow_control.py`
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
- `scripts/`: compatibility entrypoint wrappers and operational shell pipelines.
- `configs/`: YAML configuration files.
- `cases/`: sweep input CSV files.
- `docs/`: project notes, environment setup, audit reports, and restructure notes.
- `examples/`: small runnable examples for new workflows.
- `tests/`: smoke tests and future regression tests.
- `runs/`: local run outputs; generated contents are ignored by Git.
- `article/`: reference papers and external research material.
- `logs/`: archived local launcher/SLURM logs.
- `results/` and `results_validation/`: generated or historical case outputs.
- `archive/legacy/`: archived legacy project snapshots.

The root-level `ga.py` is the preferred unified launcher. Historical script names
are kept under `scripts/entrypoints/` as compatibility wrappers.

## Available configs

This repository keeps two supported configuration files:

- `configs/config.yaml`: generic default configuration
- `configs/config_rl_build_amg_match_mesh.yaml`: RL configuration aligned as closely as possible with `build_AMG.java` and `build_base_3.java`
- `configs/maglev_sparse_jet_9w.yaml`: first sparse-jet flow-control prototype configuration

## Run the B02 mock flow-control workflow

```bash
python examples/run_mock_flow_control.py
```

Or generate the first schedule directly:

```bash
python -m flow_control.schedule_generator --config configs/maglev_sparse_jet_9w.yaml
```

## Run a single case

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

Update `configs/config.yaml` before running:

```yaml
adapter: starccm
starccm_path: /opt/STAR-CCM+/star/bin/starccm+
template_sim: /path/to/template.sim
num_cores: 128
```

## Required case fields

| Field | Unit | Notes |
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
