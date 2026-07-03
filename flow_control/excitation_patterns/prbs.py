"""Pseudo-random binary switching schedule."""

from __future__ import annotations

import random

from .common import ActuationConfig, ScheduleTable, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    rng = random.Random(config.random_seed)
    max_active = config.max_active_jets or config.n_active_per_window
    if config.max_total_mass_flow is not None:
        max_active = min(max_active, int(config.max_total_mass_flow // config.mass_flow_rate))
    max_active = max(0, min(max_active, config.n_jets))

    switches: list[list[int]] = []
    state = [0] * config.n_jets
    for _ in range(config.total_windows):
        for jet_idx in range(config.n_jets):
            if rng.random() < config.prbs_switch_probability:
                state[jet_idx] = 1 - state[jet_idx]
        active = [idx for idx, value in enumerate(state) if value]
        if len(active) > max_active:
            keep = set(rng.sample(active, max_active))
            state = [1 if idx in keep else 0 for idx in range(config.n_jets)]
        switches.append(state[:])

    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    extra = {
        "random_seed": config.random_seed,
        "max_active_jets": max_active,
        "prbs_switch_probability": config.prbs_switch_probability,
    }
    return table, extra, []
