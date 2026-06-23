"""Deterministic mock plant for local flow-control workflow tests."""

from __future__ import annotations

from .data_schema import PlantObservation, Schedule


def run_mock_plant(schedule: Schedule) -> list[PlantObservation]:
    """Simulate schedule execution with a simple monotonic response model."""

    observations: list[PlantObservation] = []
    drag = 1.0
    pressure_loss = 1.0

    for step in schedule.steps:
        active_mass_flow = sum(action.mass_flow_rate for action in step.actions if action.enabled)
        active_duty = sum(action.duty_cycle for action in step.actions if action.enabled)
        control_strength = min(active_mass_flow * 0.02 + active_duty * 0.01, 0.05)
        drag = max(0.2, drag * (1.0 - control_strength))
        pressure_loss = max(0.2, pressure_loss * (1.0 - control_strength * 0.5))
        observations.append(
            PlantObservation(
                iteration=step.iteration + step.duration_iterations,
                drag=drag,
                pressure_loss=pressure_loss,
                stable=control_strength < 0.049,
                notes="mock response",
            )
        )

    return observations
