"""Declarative STAR-CCM+ runtime commands shared by project lines."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal


class StarCCMCommand:
    """Base class for serializable STAR-CCM+ runtime commands."""

    kind: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SetBoundaryProfile(StarCCMCommand):
    """Set a STAR-CCM+ boundary profile or actuator value."""

    boundary_name: str
    profile_name: str
    value: float
    units: str = ""
    column: str = ""
    kind: Literal["set_boundary_profile"] = "set_boundary_profile"


@dataclass(frozen=True)
class SetSolverParameter(StarCCMCommand):
    """Set a solver or numerical control parameter."""

    parameter_name: str
    value: float | int | str | bool
    kind: Literal["set_solver_parameter"] = "set_solver_parameter"


@dataclass(frozen=True)
class SetReportBinding(StarCCMCommand):
    """Ensure a STAR-CCM+ report is bound to a part and direction."""

    report_name: str
    part_name: str
    report_type: str = "ForceReport"
    direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    column: str = ""
    kind: Literal["set_report_binding"] = "set_report_binding"


@dataclass(frozen=True)
class RunIterations(StarCCMCommand):
    """Advance STAR-CCM+ by a fixed number of solver iterations."""

    iterations: int
    kind: Literal["run_iterations"] = "run_iterations"


@dataclass(frozen=True)
class RunTimeWindow(StarCCMCommand):
    """Advance STAR-CCM+ over one physical/control window."""

    duration: float
    time_step: float | None = None
    kind: Literal["run_time_window"] = "run_time_window"


@dataclass(frozen=True)
class ReadReports(StarCCMCommand):
    """Read a fixed list of STAR-CCM+ reports after runtime advancement."""

    report_names: tuple[str, ...]
    kind: Literal["read_reports"] = "read_reports"


@dataclass(frozen=True)
class StarCCMCommandPlan:
    """Ordered command list plus metadata for a STAR-CCM+ runtime execution."""

    source: str
    commands: tuple[StarCCMCommand, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "metadata": dict(self.metadata),
            "commands": [command.to_dict() for command in self.commands],
        }

    def write_json(self, path: str | Path) -> None:
        import json

        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
