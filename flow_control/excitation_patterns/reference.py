"""No-jet reference schedule."""

from __future__ import annotations

from .common import ActuationConfig, ScheduleTable, empty_switches, table_from_switches


def generate(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, object], list[str]]:
    switches = empty_switches(config.total_windows, config.n_jets)
    table = table_from_switches(switches, mass_flow_rate=config.mass_flow_rate)
    return table, {"purpose": "baseline with all jet valves closed"}, []
