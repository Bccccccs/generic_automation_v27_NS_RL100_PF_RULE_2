"""Facade for shared STAR-CCM+ control-layer operations.

This first version is intentionally declarative: it validates and exposes the
contract consumed by both automation lines. The Java macro integration can use
``macro_context()`` as the stable handoff object.
"""

from __future__ import annotations

from typing import Any, Mapping

from .control_spec import DEFAULT_STARCCM_SPEC, StarCCMControlSpec
from .result_mapper import StarCCMResultMapper


class StarCCMControlLayer:
    """Shared control-layer facade above the optimization and flow-control lines."""

    def __init__(self, spec: StarCCMControlSpec = DEFAULT_STARCCM_SPEC) -> None:
        self.spec = spec.require_valid()
        self.result_mapper = StarCCMResultMapper(self.spec)

    def macro_context(self) -> dict[str, Any]:
        return self.spec.to_macro_context()

    def map_timeseries_row(
        self,
        report_values: Mapping[str, Any],
        jet_commands: Mapping[str, Any],
        *,
        physical_time: float,
        window_id: int,
        solver_status: str = "success",
    ) -> dict[str, Any]:
        return self.result_mapper.map_row(
            report_values,
            jet_commands,
            physical_time=physical_time,
            window_id=window_id,
            solver_status=solver_status,
        )
