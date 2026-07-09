# B03 Constrained Sparse Random Schedule Generator

B03 implements the first version of a constrained sparse random actuation
schedule generator for the sparse-jet flow-control workflow.

This stage does not run CFD and does not train a controller. Its purpose is to
prepare a reproducible, balanced, sparse jet excitation input sequence for later
simulation or experiment stages.

## Goal

Generate an 80-window actuation schedule for 24 jet zones:

- 72 excitation windows
- 8 no-jet reference windows
- exactly 3 active jets in every excitation window
- exactly 9 appearances for each jet across the 72 excitation windows
- no repeated 3-jet combination
- no jet active for more than 2 consecutive windows
- fixed per-open-jet mass flow command
- configurable window duration `T_w`
- saved random seed for reproducibility

The equal activation count follows from:

```text
72 excitation windows * 3 active jets per window = 216 activations
216 activations / 24 jets = 9 activations per jet
```

## Branch

- `feature/b03-sparse-random-schedule-generator`

## Main Files

- `configs/actions/pilot_sparse24.yaml`: B03 actuation configuration.
- `flow_control/workflow/schedule_generator.py`: constrained random generator, diagnostics, and CLI.
- `tests/test_actuation_schedule_generator.py`: regression tests for constraints and outputs.

## Configuration

The B03 config is:

```yaml
actuation:
  n_jets: 24
  n_active_per_window: 3
  n_excitation_windows: 72
  n_reference_windows: 8
  mass_flow_rate: 1.0
  window_duration: 1.0
  max_consecutive_on: 2
  equal_activation_count: true
  random_seed: 20260618

output:
  run_dir: runs/pilot_sparse24
```

`window_duration` is intentionally configurable and is not hard-coded in the
generator.

## Workflow

```text
Read config
  -> generate 72 constrained random excitation windows
  -> append 8 no-jet reference windows
  -> build the 24 x 80 jet on/off matrix
  -> write schedule and diagnostics
  -> run automatic validation checks
```

For each excitation window, the generator chooses 3 active jets while enforcing:

- balanced remaining activation counts
- no duplicate jet combinations
- maximum consecutive-on limit

The final 8 reference windows are all-zero windows.

## Run Command

From the repository root:

```bash
.venv/bin/python -m flow_control.workflow.schedule_generator --config configs/actions/pilot_sparse24.yaml
```

If the active environment exposes `python`, the shorter form also works:

```bash
python -m flow_control.workflow.schedule_generator --config configs/actions/pilot_sparse24.yaml
```

## Outputs

The default output directory is:

```text
runs/pilot_sparse24/
```

Generated files:

- `actuation_schedule.csv`: main window-by-window jet command table.
- `actuation_heatmap.svg`: 24 x 80 jet timing heatmap.
- `activation_counts.csv`: activation count per jet.
- `pairwise_cooccurrence.csv`: pairwise co-occurrence count for every jet pair.
- `input_correlation_matrix.csv`: correlation matrix of jet input sequences.
- `mass_flow.csv`: total command mass flow per window.
- `config_summary.yaml`: random seed, config summary, output list, and validation status.
- `validation_report.json`: compact machine-readable validation report.

The main CSV format is:

```text
physical_time, window_id, t_start, t_end, JET_01, JET_02, ..., JET_24
```

Jet values use:

```text
0 = off, with strict zero mass flow
mass_flow_rate = on, using the configured per-jet mass flow
```

`physical_time`, `t_start`, and `t_end` are physical time in seconds. They are
not STAR-CCM+ nonlinear iteration numbers; a single physical control window may
contain many solver iterations internally.

## Automatic Validation

The generator checks:

- every excitation window has exactly 3 active jets
- every reference window has 0 active jets
- every jet appears exactly 9 times across the excitation windows
- no 3-jet combination is repeated
- no jet exceeds the consecutive-on limit
- the same random seed reproduces the exact same schedule
- a changed random seed produces a different schedule

A successful `validation_report.json` looks like:

```json
{
  "passed": true,
  "errors": [],
  "same_seed_reproduces": true,
  "different_seed_changes_sequence": true
}
```

## Tests

Run only the B03 tests:

```bash
.venv/bin/python -m pytest -q tests/test_actuation_schedule_generator.py
```

Run all tests:

```bash
.venv/bin/python -m pytest -q
```

## PPT Figures

Recommended figures for reporting this stage:

- workflow diagram: config -> constrained generator -> schedule matrix -> diagnostics -> validation
- `actuation_heatmap.svg`: the 24 x 80 jet timing heatmap
- bar chart from `activation_counts.csv`: every jet should have height 9
- heatmap from `pairwise_cooccurrence.csv` or `input_correlation_matrix.csv`
- validation summary from `validation_report.json`

## Stage Summary

B03 turns a small YAML configuration into a constrained, reproducible sparse jet
actuation sequence. It also produces the diagnostics needed to verify that the
input is balanced, non-duplicated, sparse, and ready for downstream simulation.
