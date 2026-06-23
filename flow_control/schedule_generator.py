"""Generate first-pass sparse-jet schedules from configuration."""

from __future__ import annotations

from .data_schema import ControlAction, ExperimentConfig, Schedule, ScheduleStep


def generate_schedule(config: ExperimentConfig) -> Schedule:
    """Build a simple uniform schedule that can be replaced by optimization later."""

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


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Generate a sparse-jet control schedule.")
    parser.add_argument("--config", default="configs/maglev_sparse_jet_9w.yaml")
    args = parser.parse_args()

    config = ExperimentConfig.from_yaml(args.config)
    schedule = generate_schedule(config)
    print(f"generated schedule: {schedule.name}, steps={len(schedule.steps)}")


if __name__ == "__main__":
    main()
