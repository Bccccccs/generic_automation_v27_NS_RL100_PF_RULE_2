"""Translate generic solver-automation cases into STAR-CCM+ runtime commands."""

from __future__ import annotations

from typing import Any

from generic_automation.core.adapter_base import Case
from starccm.runtime import (
    ReadReports,
    RunIterations,
    SetSolverParameter,
    StarCCMCommand,
    StarCCMCommandPlan,
)


SOLVER_PARAMETER_FIELDS = (
    "pressure_relaxation_factor",
    "pressure_relaxation_initial_value",
    "pressure_relaxation_start_iteration",
    "pressure_relaxation_end_iteration",
    "velocity_relaxation_initial_value",
    "velocity_relaxation_start_iteration",
    "velocity_relaxation_end_iteration",
    "pressure_amg_cycle",
    "velocity_amg_cycle",
    "amg_cycle",
    "amg_solver",
)


class GenericAutomationStarCCMTranslator:
    """Translate the original solver-optimization line to runtime commands."""

    def translate(self, case: Case, *, check_interval: int | None = None) -> StarCCMCommandPlan:
        commands: list[StarCCMCommand] = []
        for field_name in SOLVER_PARAMETER_FIELDS:
            value = getattr(case, field_name, None)
            if value in {None, ""}:
                continue
            commands.append(SetSolverParameter(parameter_name=field_name, value=value))

        run_mode = str(getattr(case, "run_mode", "full_run") or "full_run").strip().lower()
        if run_mode not in {"mesh_only"}:
            iterations = int(check_interval or getattr(case, "max_iterations", 0) or 0)
            if iterations > 0:
                commands.append(RunIterations(iterations=iterations))

        report_names = self._report_names(case)
        if report_names:
            commands.append(ReadReports(report_names=report_names))

        return StarCCMCommandPlan(
            source="generic_automation",
            commands=tuple(commands),
            metadata={
                "case_name": getattr(case, "case_name", ""),
                "run_mode": run_mode,
                "simulation_type": getattr(case, "simulation_type", ""),
            },
        )

    @staticmethod
    def _report_names(case: Case) -> tuple[str, ...]:
        names: list[str] = []
        for attr_name in (
            "drag_report_name",
            "total_report_name",
            "train_surface_pressure_report_name",
            "outlet_pressure_report_name",
        ):
            value = str(getattr(case, attr_name, "") or "").strip()
            if value and value not in names:
                names.append(value)
        for value in getattr(case, "report_names", []) or []:
            rendered = str(value).strip()
            if rendered and rendered not in names:
                names.append(rendered)
        return tuple(names)
