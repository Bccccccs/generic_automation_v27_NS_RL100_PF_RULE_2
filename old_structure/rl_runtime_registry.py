from __future__ import annotations

from typing import Any

RL_RUNTIME_PARAMETER_NAMES: tuple[str, ...] = (
    "pressure_relaxation_factor",
    "pressure_relaxation_initial_value",
    "pressure_relaxation_end_iteration",
    "pressure_amg_cycle",
    "velocity_amg_cycle",
)

RL_RUNTIME_PARAMETER_SET: frozenset[str] = frozenset(RL_RUNTIME_PARAMETER_NAMES)


def rl_runtime_parameter_snapshot(values: dict[str, Any]) -> dict[str, Any]:
    return {
        key: values[key]
        for key in RL_RUNTIME_PARAMETER_NAMES
        if key in values
    }
