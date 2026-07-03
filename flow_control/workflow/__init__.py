"""Schedule and actuation workflow helpers for jet flow control."""

from __future__ import annotations

__all__ = [
    "ActuationRun",
    "generate_schedule",
    "load_actuation_run",
    "read_yaml",
    "run_actuation_to_mock",
    "validate_actuation_schedule_csv",
    "validate_schedule",
]


def __getattr__(name: str):
    if name in {"ActuationRun", "load_actuation_run", "read_yaml"}:
        from . import actuation

        return getattr(actuation, name)
    if name == "generate_schedule":
        from . import schedule_generator

        return getattr(schedule_generator, name)
    if name == "run_actuation_to_mock":
        from . import mock_pipeline

        return getattr(mock_pipeline, name)
    if name in {"validate_actuation_schedule_csv", "validate_schedule"}:
        from . import schedule_validator

        return getattr(schedule_validator, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
