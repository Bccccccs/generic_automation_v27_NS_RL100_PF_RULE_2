"""Generate and validate physical-time jet actuation schedules."""

__all__ = [
    "generate_from_mapping",
    "generate_from_yaml",
    "resolve_input_dir",
    "validate_actuation_schedule_csv",
]


def __getattr__(name: str):
    if name in {"generate_from_mapping", "generate_from_yaml", "resolve_input_dir"}:
        from . import schedule_generator

        return getattr(schedule_generator, name)
    if name == "validate_actuation_schedule_csv":
        from . import schedule_validator

        return schedule_validator.validate_actuation_schedule_csv
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
