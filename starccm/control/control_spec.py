"""STAR-CCM+ control names shared by solver automation and flow control."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


JET_COLUMNS = tuple(f"JET_{idx:02d}" for idx in range(1, 25))
LOAD_COLUMNS = ("Fz_S1L", "Fz_S1R", "Fz_S2L", "Fz_S2R", "Fz_S3L", "Fz_S3R")
GLOBAL_OUTPUT_COLUMNS = (
    "Fz_Total",
    "Drag_Total",
    "Pitch_Moment",
    "Roll_Moment",
    "Jet_Reaction_Z",
    "solver_status",
)


@dataclass(frozen=True)
class JetActuator:
    """Fixed mapping from a control column to a STAR-CCM+ jet boundary/profile."""

    column: str
    boundary_name: str
    profile_name: str
    report_name: str


@dataclass(frozen=True)
class LoadPoint:
    """Fixed mapping from a standardized load column to a STAR-CCM+ force report."""

    column: str
    report_name: str
    part_name: str
    station: str
    side: str
    direction: tuple[float, float, float] = (0.0, 0.0, 1.0)


def _default_jets() -> tuple[JetActuator, ...]:
    return tuple(
        JetActuator(
            column=column,
            boundary_name=f"J{idx:02d}",
            profile_name=f"j{idx:02d}_mass_flow",
            report_name=f"j{idx:02d}_mass_flow_report",
        )
        for idx, column in enumerate(JET_COLUMNS, start=1)
    )


def _default_load_points() -> tuple[LoadPoint, ...]:
    labels = (
        ("Fz_S1L", "S1", "L"),
        ("Fz_S1R", "S1", "R"),
        ("Fz_S2L", "S2", "L"),
        ("Fz_S2R", "S2", "R"),
        ("Fz_S3L", "S3", "L"),
        ("Fz_S3R", "S3", "R"),
    )
    return tuple(
        LoadPoint(
            column=column,
            report_name=f"fc_load_{station}{side}",
            part_name=f"FC_Load_{station}{side}",
            station=station,
            side=side,
        )
        for column, station, side in labels
    )


DEFAULT_STARCCM_JETS = _default_jets()
DEFAULT_LOAD_POINTS = _default_load_points()
DEFAULT_STARCCM_REPORTS = (
    "drag",
    "total",
    "train_surface_pressure_max",
    *(point.report_name for point in DEFAULT_LOAD_POINTS),
)


@dataclass(frozen=True)
class StarCCMControlSpec:
    """Declarative STAR-CCM+ control contract consumed by both project lines."""

    jets: tuple[JetActuator, ...] = DEFAULT_STARCCM_JETS
    load_points: tuple[LoadPoint, ...] = DEFAULT_LOAD_POINTS
    report_names: tuple[str, ...] = DEFAULT_STARCCM_REPORTS
    window_duration: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def jet_columns(self) -> tuple[str, ...]:
        return tuple(jet.column for jet in self.jets)

    @property
    def load_columns(self) -> tuple[str, ...]:
        return tuple(point.column for point in self.load_points)

    @property
    def load_report_names(self) -> tuple[str, ...]:
        return tuple(point.report_name for point in self.load_points)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if self.jet_columns != JET_COLUMNS:
            errors.append("StarCCMControlSpec must define exactly JET_01..JET_24 in order")
        if self.load_columns != LOAD_COLUMNS:
            errors.append("StarCCMControlSpec must define exactly Fz_S1L..Fz_S3R in order")
        if len({jet.boundary_name for jet in self.jets}) != len(self.jets):
            errors.append("jet boundary names must be unique")
        if len({jet.profile_name for jet in self.jets}) != len(self.jets):
            errors.append("jet profile names must be unique")
        if len({point.report_name for point in self.load_points}) != len(self.load_points):
            errors.append("load point report names must be unique")
        if self.window_duration <= 0.0:
            errors.append("window_duration must be positive")
        return errors

    def require_valid(self) -> "StarCCMControlSpec":
        errors = self.validate()
        if errors:
            raise ValueError("; ".join(errors))
        return self

    def to_macro_context(self) -> dict[str, Any]:
        """Return a serializable contract that can later feed Java macro generation."""

        self.require_valid()
        return {
            "jets": [jet.__dict__ for jet in self.jets],
            "load_points": [point.__dict__ for point in self.load_points],
            "report_names": list(self.report_names),
            "window_duration": self.window_duration,
            "metadata": dict(self.metadata),
        }


DEFAULT_STARCCM_SPEC = StarCCMControlSpec()
