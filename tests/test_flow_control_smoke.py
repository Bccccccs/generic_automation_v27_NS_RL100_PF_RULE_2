from flow_control.data_schema import ExperimentConfig
from flow_control.mock_plant import run_mock_plant
from flow_control.result_analyzer import summarize_observations
from flow_control.schedule_generator import generate_schedule
from flow_control.schedule_validator import validate_schedule


def test_mock_flow_control_pipeline_smoke():
    config = ExperimentConfig.from_mapping(
        {
            "experiment": {
                "project_name": "maglev_sparse_jet_9w",
                "case_name": "smoke",
                "max_iterations": 100,
            },
            "control": {
                "interval_iterations": 25,
                "jets": [{"id": "jet_01"}, {"id": "jet_02"}],
                "defaults": {
                    "mass_flow_rate": 0.02,
                    "duty_cycle": 0.5,
                    "frequency_hz": 20.0,
                },
            },
        }
    )

    schedule = generate_schedule(config)
    assert validate_schedule(schedule, config) == []

    observations = run_mock_plant(schedule)
    summary = summarize_observations(observations)

    assert summary["num_observations"] == 4
    assert 0.0 < summary["final_drag"] <= 1.0
    assert summary["all_stable"] is True
