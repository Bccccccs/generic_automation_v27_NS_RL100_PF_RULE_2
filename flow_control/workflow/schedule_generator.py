"""Generate sparse-jet physical-time actuation schedules from YAML configs."""

from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml

from ..config import load_config_with_system_defaults
from ..data_schema import ControlAction, ExperimentConfig, Schedule, ScheduleStep
from ..excitation_patterns import (
    ActuationConfig,
    ScheduleTable,
    generate_pattern_table,
    write_pattern_outputs,
)
from ..excitation_patterns.sparse_groups import (
    activation_counts,
    generate_actuation_matrix,
    validate_sparse_matrix,
)


def generate_schedule(config: ExperimentConfig) -> Schedule:
    """Build the legacy ExperimentConfig schedule used by old smoke tests."""

    steps: list[ScheduleStep] = []
    interval = config.control_interval_iterations
    for step_id, iteration in enumerate(range(0, config.max_iterations, interval)):
        remaining = config.max_iterations - iteration
        duration = min(interval, remaining)
        actions = tuple(
            ControlAction(
                jet_id=jet_id,
                enabled=config.default_mass_flow_rate > 0.0,
                mass_flow_rate=config.default_mass_flow_rate,
                duty_cycle=config.default_duty_cycle,
                frequency_hz=config.default_frequency_hz,
            )
            for jet_id in config.jet_ids
        )
        steps.append(
            ScheduleStep(
                step_id=step_id,
                iteration=iteration,
                duration_iterations=duration,
                actions=actions,
            )
        )
    return Schedule(name=f"{config.case_name}_schedule", steps=tuple(steps))


def validate_actuation_matrix(config: ActuationConfig, matrix: list[list[int]]) -> list[str]:
    """Compatibility wrapper for sparse_random_groups matrix validation."""

    return validate_sparse_matrix(config, matrix)


def write_actuation_outputs(config: ActuationConfig, matrix: list[list[int]]) -> None:
    """Compatibility wrapper that writes a sparse matrix as unified schedule outputs."""

    massflows = [[value * config.mass_flow_rate for value in row] for row in matrix]
    table = ScheduleTable(switches=matrix, massflows=massflows)
    errors = validate_sparse_matrix(config, matrix)
    write_pattern_outputs(
        config,
        table,
        validation_errors=errors,
        extra={"compatibility_wrapper": True},
    )


def generate_from_config(config: ActuationConfig) -> tuple[ScheduleTable, dict[str, Any], list[str]]:
    """Generate one of the supported physical-time actuation pattern tables."""

    return generate_pattern_table(config)


def run_from_yaml(config_path: str | Path, output_dir: str | Path | None = None) -> ActuationConfig:
    data = load_config_with_system_defaults(config_path)
    config = ActuationConfig.from_mapping(data)
    if output_dir is not None:
        config = replace(config, output_dir=Path(output_dir))
    table, extra, errors = generate_from_config(config)
    write_pattern_outputs(config, table, validation_errors=errors, extra=extra)
    return config


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate physical-time sparse-jet actuation schedules."
    )
    parser.add_argument("--config", default="configs/actions/pilot_sparse24.yaml")
    parser.add_argument("--output-dir", help="Override output.run_dir from the config file.")
    args = parser.parse_args()

    data = load_config_with_system_defaults(args.config)

    if "actuation" in data:
        config = run_from_yaml(args.config, output_dir=args.output_dir)
        print(
            "generated actuation schedule: "
            f"mode={config.mode}, jets={config.n_jets}, output={config.output_dir}"
        )
    else:
        config = ExperimentConfig.from_mapping(data)
        schedule = generate_schedule(config)
        print(f"generated schedule: {schedule.name}, steps={len(schedule.steps)}")


if __name__ == "__main__":
    main()
