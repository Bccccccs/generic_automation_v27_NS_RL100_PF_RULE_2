"""Validation helpers for sparse-jet schedules."""

from __future__ import annotations

from .data_schema import ExperimentConfig, Schedule


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
