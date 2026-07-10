# Config conventions

Shared run-level parameters live in `configs/system.yaml`. Use
`system.random_seed` there as the default reproducibility seed:

```yaml
system:
  random_seed: 20260618
```

Flow-control actuation configs read this value unless `actuation.random_seed`
is set. Mock dynamic configs read this value unless
`mock_dynamic24x6.random_seed` is set. Prefer the shared file for normal runs
so schedules and mock outputs can be reproduced from one visible system
parameter.

For batch dataset generation, use `system.random_seed` as the single source of
truth and derive each case seed from that value. Schedule generation and mock
dynamics should receive the same per-case seed.

The six flow-control actuation example configs live under `configs/actions/`.

Set `FLOW_CONTROL_SYSTEM_CONFIG=/path/to/system.yaml` to use another shared
system config without editing individual experiment configs.
