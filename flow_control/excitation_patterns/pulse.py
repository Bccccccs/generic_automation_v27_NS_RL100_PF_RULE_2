"""Single-jet pulse schedule."""

from __future__ import annotations

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    jet_id = config.jet_ids[0] if config.jet_ids else 3
    pulse_windows = config.pulse_windows or (1,)
    switches = empty_switches(config.total_windows, config.n_jets)
    idx = jet_index(jet_id, config.n_jets)
    errors: list[str] = []
    for window_id in pulse_windows:
        if not 0 <= window_id < config.total_windows:
            errors.append(f"pulse window {window_id} outside total_windows={config.total_windows}")
            continue
        switches[window_id][idx] = 1
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {"jet_id": jet_id, "pulse_windows": list(pulse_windows)}, errors
