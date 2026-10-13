# B02 Project Scaffold

Week 1 task B02 establishes a separate sparse-jet flow-control workspace while
leaving the existing solver optimization modules intact.

## Branch

- `maglev_sparse_jet_9w`

## New module boundary

- `flow_control/data_schema.py`: shared config, schedule, action, and observation data structures.
- `flow_control/generator/schedule_generator.py`: deterministic six-pattern schedule generator.
- `flow_control/generator/schedule_validator.py`: generated CSV consistency checks.
- `flow_control/mock/mock_plant.py`: deterministic mock plant for local workflow tests.
- `
## First config

- `configs/maglev_sparse_jet_9w.yaml`

The first config uses nine sparse jets, a 900-iteration mock case, and a uniform
control interval of 50 iterations.

## Validation command

```bash
python -m pytest tests/test_flow_control_smoke.py
```
