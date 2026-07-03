"""Key-jet chirp schedule."""

from __future__ import annotations

import math

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    jet_ids = config.jet_ids or (3, 7, 14, 18)
    switches = empty_switches(config.total_windows, config.n_jets)
    massflows = [[0.0] * config.n_jets for _ in range(config.total_windows)]
    errors: list[str] = []
    total_duration = max(config.total_windows * config.window_duration, config.window_duration)

    for window_id in range(config.total_windows):
        t_mid = (window_id + 0.5) * config.window_duration
        progress = t_mid / total_duration
        frequency = (
            config.chirp_start_frequency_hz
            + (config.chirp_end_frequency_hz - config.chirp_start_frequency_hz) * progress
        )
        envelope = 0.5 * (1.0 + math.sin(2.0 * math.pi * frequency * t_mid))
        value = config.mass_flow_rate * envelope
        is_on = value > 1e-12
        for jet_id in jet_ids:
            idx = jet_index(jet_id, config.n_jets)
            switches[window_id][idx] = 1 if is_on else 0
            massflows[window_id][idx] = value if is_on else 0.0

    table = ScheduleTable(switches=switches, massflows=massflows)
    extra = {
        "jet_ids": list(jet_ids),
        "chirp_start_frequency_hz": config.chirp_start_frequency_hz,
        "chirp_end_frequency_hz": config.chirp_end_frequency_hz,
        "note": "mass-flow amplitude follows a sinusoidal chirp envelope",
    }
    return table, extra, errors
