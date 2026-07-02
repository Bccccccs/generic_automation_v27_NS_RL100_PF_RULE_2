"""Translate sparse-jet flow-control windows into STAR-CCM+ runtime commands."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from starccm_control import DEFAULT_STARCCM_SPEC, StarCCMControlSpec
from starccm_runtime import (
    ReadReports,
    RunTimeWindow,
    SetBoundaryProfile,
    SetReportBinding,
    StarCCMCommand,
    StarCCMCommandPlan,
)


class FlowControlStarCCMTranslator:
    """Translate jet commands/load needs into shared runtime commands."""

    def __init__(self, spec: StarCCMControlSpec = DEFAULT_STARCCM_SPEC) -> None:
        self.spec = spec.require_valid()

    def translate_window(
        self,
        jet_commands: Mapping[str, Any] | Sequence[float],
        *,
        window_id: int,
        duration: float | None = None,
        time_step: float | None = None,
    ) -> StarCCMCommandPlan:
        normalized = self._normalize_commands(jet_commands)
        commands: list[StarCCMCommand] = []
        commands.extend(self._load_bindings())
        for jet in self.spec.jets:
            commands.append(
                SetBoundaryProfile(
                    boundary_name=jet.boundary_name,
                    profile_name=jet.profile_name,
                    value=normalized[jet.column],
                    column=jet.column,
                )
            )
        commands.append(
            RunTimeWindow(
                duration=float(duration if duration is not None else self.spec.window_duration),
                time_step=time_step,
            )
        )
        commands.append(ReadReports(report_names=self.spec.load_report_names))
        return StarCCMCommandPlan(
            source="flow_control",
            commands=tuple(commands),
            metadata={
                "window_id": int(window_id),
                "active_jets": [
                    column for column, value in normalized.items() if float(value) != 0.0
                ],
            },
        )

    def _normalize_commands(self, jet_commands: Mapping[str, Any] | Sequence[float]) -> dict[str, float]:
        if isinstance(jet_commands, Mapping):
            return {
                jet.column: float(jet_commands.get(jet.column, 0.0))
                for jet in self.spec.jets
            }
        if len(jet_commands) != len(self.spec.jets):
            raise ValueError(f"expected {len(self.spec.jets)} jet command values")
        return {
            jet.column: float(jet_commands[idx])
            for idx, jet in enumerate(self.spec.jets)
        }

    def _load_bindings(self) -> tuple[SetReportBinding, ...]:
        return tuple(
            SetReportBinding(
                report_name=point.report_name,
                part_name=point.part_name,
                direction=point.direction,
                column=point.column,
            )
            for point in self.spec.load_points
        )
