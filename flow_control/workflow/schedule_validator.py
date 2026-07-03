"""Validation helpers for sparse-jet schedules."""

from __future__ import annotations

import csv
from pathlib import Path

from ..data_schema import ExperimentConfig, Schedule
from ..excitation_patterns.common import MASSFLOW_COLUMNS
from ..data_schema import JET_COLUMNS


def validate_schedule(schedule: Schedule, config: ExperimentConfig) -> list[str]:
    """Return validation errors; an empty list means the schedule is usable."""

    errors: list[str] = []
    if not schedule.steps:
        errors.append("schedule must contain at least one step")

    expected_iteration = 0
    allowed_jets = set(config.jet_ids)
    for step in schedule.steps:
        if step.iteration != expected_iteration:
            errors.append(
                f"step {step.step_id} starts at {step.iteration}, expected {expected_iteration}"
            )
        if step.duration_iterations <= 0:
            errors.append(f"step {step.step_id} duration must be positive")

        for action in step.actions:
            if action.jet_id not in allowed_jets:
                errors.append(f"step {step.step_id} references unknown jet {action.jet_id}")
            if action.mass_flow_rate < 0:
                errors.append(f"step {step.step_id} jet {action.jet_id} has negative mass flow")
            if not 0.0 <= action.duty_cycle <= 1.0:
                errors.append(f"step {step.step_id} jet {action.jet_id} duty cycle outside [0, 1]")
            if action.frequency_hz < 0:
                errors.append(f"step {step.step_id} jet {action.jet_id} has negative frequency")

        expected_iteration += step.duration_iterations

    if expected_iteration != config.max_iterations:
        errors.append(f"schedule ends at {expected_iteration}, expected {config.max_iterations}")

    return errors


def validate_actuation_schedule_csv(
    path: str | Path,
    *,
    n_jets: int = 24,
    max_active_jets: int | None = None,
    max_total_mass_flow: float | None = None,
) -> list[str]:
    """Validate the unified physical-time actuation_schedule.csv format."""

    csv_path = Path(path)
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    with csv_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])

    jet_columns = list(JET_COLUMNS[:n_jets])
    massflow_columns = list(MASSFLOW_COLUMNS[:n_jets])
    expected = ["physical_time", "window_id", "t_start", "t_end", *jet_columns, *massflow_columns]
    errors: list[str] = []
    if header != expected:
        errors.append("actuation_schedule.csv columns do not match the unified B02 format")
    if not rows:
        errors.append("actuation_schedule.csv must contain at least one window")
        return errors

    previous_window_id: int | None = None
    previous_t_end: float | None = None
    for row_idx, row in enumerate(rows):
        try:
            window_id = int(row["window_id"])
            physical_time = float(row["physical_time"])
            t_start = float(row["t_start"])
            t_end = float(row["t_end"])
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"row {row_idx} has invalid time/window fields: {exc}")
            continue
        if previous_window_id is not None and window_id != previous_window_id + 1:
            errors.append(f"row {row_idx} window_id must increase by 1")
        if abs(physical_time - t_start) > 1e-12:
            errors.append(f"row {row_idx} physical_time must equal t_start")
        if t_end <= t_start:
            errors.append(f"row {row_idx} t_end must be greater than t_start")
        if previous_t_end is not None and abs(t_start - previous_t_end) > 1e-12:
            errors.append(f"row {row_idx} t_start must equal previous t_end")

        active = 0
        total_mass_flow = 0.0
        for jet_column, massflow_column in zip(jet_columns, massflow_columns):
            try:
                jet_value = int(float(row[jet_column]))
                massflow_value = float(row[massflow_column])
            except (KeyError, TypeError, ValueError) as exc:
                errors.append(f"row {row_idx} has invalid {jet_column}/{massflow_column}: {exc}")
                continue
            if jet_value not in (0, 1):
                errors.append(f"row {row_idx} {jet_column} must be 0 or 1")
            if jet_value == 0 and abs(massflow_value) > 1e-12:
                errors.append(f"row {row_idx} {jet_column}=0 requires {massflow_column}=0")
            if jet_value == 1 and massflow_value <= 0:
                errors.append(f"row {row_idx} {jet_column}=1 requires {massflow_column}>0")
            active += jet_value
            total_mass_flow += massflow_value
        if max_active_jets is not None and active > max_active_jets:
            errors.append(f"row {row_idx} active jets {active} exceed {max_active_jets}")
        if max_total_mass_flow is not None and total_mass_flow > max_total_mass_flow + 1e-12:
            errors.append(
                f"row {row_idx} total mass flow {total_mass_flow} exceeds {max_total_mass_flow}"
            )
        previous_window_id = window_id
        previous_t_end = t_end
    return errors
