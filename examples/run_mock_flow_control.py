"""Run the first sparse-jet mock workflow from a YAML config."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from flow_control.data_schema import ExperimentConfig
from flow_control.mock_plant import run_mock_plant
from flow_control.result_analyzer import summarize_observations
from flow_control.schedule_generator import generate_schedule
from flow_control.schedule_validator import validate_schedule


def main() -> None:
    config = ExperimentConfig.from_yaml("configs/maglev_sparse_jet_9w.yaml")
    schedule = generate_schedule(config)
    errors = validate_schedule(schedule, config)
    if errors:
        raise SystemExit("\n".join(errors))

    observations = run_mock_plant(schedule)
    summary = summarize_observations(observations)
    print(summary)


if __name__ == "__main__":
    main()
