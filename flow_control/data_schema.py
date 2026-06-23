"""Data structures shared by the sparse-jet flow-control prototype."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class ControlAction:
    """One sparse-jet control command."""

    jet_id: str
    enabled: bool
    mass_flow_rate: float
    duty_cycle: float
    frequency_hz: float


@dataclass(frozen=True)
class ScheduleStep:
    """Control commands applied at one solver/control iteration."""

    step_id: int
    iteration: int
    duration_iterations: int
    actions: tuple[ControlAction, ...]


@dataclass(frozen=True)
class Schedule:
    """A complete control schedule for one experiment."""

    name: str
    steps: tuple[ScheduleStep, ...]


@dataclass(frozen=True)
class PlantObservation:
    """Minimal observation emitted by the mock plant or future real adapter."""

    iteration: int
    drag: float
    pressure_loss: float
    stable: bool
    notes: str = ""


@dataclass(frozen=True)
class ExperimentConfig:
    """Top-level configuration for the first maglev sparse-jet workflow."""

    project_name: str
    case_name: str
    max_iterations: int
    control_interval_iterations: int
    jet_ids: tuple[str, ...]
    default_mass_flow_rate: float
    default_duty_cycle: float
    default_frequency_hz: float
    output_dir: Path = field(default=Path("runs"))

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "ExperimentConfig":
        experiment = data.get("experiment", {})
        control = data.get("control", {})
        jets = control.get("jets", [])
        defaults = control.get("defaults", {})
        output = data.get("output", {})

        return cls(
            project_name=str(experiment.get("project_name", "maglev_sparse_jet_9w")),
            case_name=str(experiment.get("case_name", "baseline")),
            max_iterations=int(experiment.get("max_iterations", 1000)),
            control_interval_iterations=int(control.get("interval_iterations", 50)),
            jet_ids=tuple(str(jet["id"]) for jet in jets),
            default_mass_flow_rate=float(defaults.get("mass_flow_rate", 0.0)),
            default_duty_cycle=float(defaults.get("duty_cycle", 0.0)),
            default_frequency_hz=float(defaults.get("frequency_hz", 0.0)),
            output_dir=Path(output.get("run_dir", "runs")),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentConfig":
        with Path(path).open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        return cls.from_mapping(data)
