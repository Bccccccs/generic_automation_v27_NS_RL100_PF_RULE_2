"""Single-jet step schedule."""

from __future__ import annotations

from .common import ActuationConfig, ScheduleTable, empty_switches, jet_index, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    jet_id = config.jet_ids[0] if config.jet_ids else 3
    start = config.step_start_window
    end = config.step_end_window if config.step_end_window is not None else config.total_windows
    switches = empty_switches(config.total_windows, config.n_jets)
    errors: list[str] = []
    if start < 0 or start >= config.total_windows:
        errors.append(f"step_start_window {start} outside total_windows={config.total_windows}")
    if end <= start or end > config.total_windows:
        errors.append(f"step_end_window {end} must be in ({start}, {config.total_windows}]")
    idx = jet_index(jet_id, config.n_jets)
    for window_id in range(max(start, 0), min(end, config.total_windows)):
        switches[window_id][idx] = 1
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {"jet_id": jet_id, "step_start_window": start, "step_end_window": end}, errors
