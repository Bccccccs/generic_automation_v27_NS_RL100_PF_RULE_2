# generic_automation

STAR-CCM+ automation for single cases, simple parameter sweeps, and online parameter control.

## Available configs

This repository keeps two supported configuration files:

- `config.yaml`: generic default configuration
- `config_rl_build_amg_match_mesh.yaml`: RL configuration aligned as closely as possible with `build_AMG.java` and `build_base_3.java`

## Run a single case

```bash
python run_case.py --config config.yaml
```

This now starts an embedded RL monitor automatically when
`ai_optimization.enabled: true`. Use `--no-monitor` if you want a pure solver run
or if another monitor process will attach externally.

Or run the build-matched RL case:

```bash
python run_case.py --config config_rl_build_amg_match_mesh.yaml
```

For online RL-controlled runs that should react to native STAR-CCM+ iteration output:

```bash
python run_monitor_only.py --config config.yaml
```

Or:

```bash
python run_monitor_only.py --config config_rl_build_amg_match_mesh.yaml
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
python run_case.py --config config.yaml --run-mode solve_only --input-sim /path/to/mesh_ready.sim
```

Relative `input_sim` and `template_sim` values from config files are resolved
relative to the config file directory. CLI `--input-sim` overrides are
normalized to absolute paths from the current shell.

To smoke-test the RL monitor plus profiling outputs without launching STAR-CCM+:

```bash
python smoke_test_rl_profiling.py
```

## Run a sweep

```bash
python run_sweep.py --config config.yaml --cases cases/cases.csv
```

## Minimal config

Update `config.yaml` before running:

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

