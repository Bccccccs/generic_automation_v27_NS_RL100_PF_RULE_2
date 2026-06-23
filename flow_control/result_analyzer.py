"""Summarize sparse-jet schedule results."""

from __future__ import annotations

from .data_schema import PlantObservation


def summarize_observations(observations: list[PlantObservation]) -> dict[str, float | bool | int]:
    """Return compact metrics for reports and tests."""

    if not observations:
        return {
            "num_observations": 0,
            "final_drag": 0.0,
            "final_pressure_loss": 0.0,
            "all_stable": False,
        }

    final = observations[-1]
    return {
        "num_observations": len(observations),
        "final_drag": final.drag,
        "final_pressure_loss": final.pressure_loss,
        "all_stable": all(observation.stable for observation in observations),
    }
